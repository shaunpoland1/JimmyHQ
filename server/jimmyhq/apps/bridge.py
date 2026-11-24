"""The bridge: every request an app's page makes of JimmyHQ arrives here, and this code
decides whether what the owner agreed to allows it.

A framed app has no credential and cannot reach ``/api/`` at all. It posts
``{id, call, args}`` to the shell over a ``MessagePort``; the shell relays it to

    POST /api/apps/<app id>/bridge/<call>      body: {id, call, args}  (and "jimmyhq": 1)

(``id`` and ``call`` may be left out: the id is only echoed, and the call is the one in
the address. JimmyHQ's own screen for a described app sends ``{args}`` alone.)

and posts the answer back to the frame unchanged:

    {id, ok: true, result}
    {id, ok: false, error: {code, message, retry_after_s?, owner_message?}}

(``message`` is for the app's author. ``owner_message``, when JimmyHQ has one, says the
same to the owner in words they can act on; a page shows it with ``why()``.)

A relayed call is always answered with HTTP 200 and one of those two shapes: a refusal
is an answer, not a transport failure. Codes: ``not_allowed``, ``quota``, ``invalid``,
``not_found``, ``too_large``, ``unavailable``, ``internal``.

**Who is asking.** Nothing the page sends says which app it is. The app id is the one in
the address, and the address is written by the shell: first-party code, running in the
owner's signed-in session, which knows which app it mounted in which frame because it
created that frame and holds its port. The frame cannot forge it: its origin is opaque,
it carries no cookie, its policy forbids every connection, and a request that says it
comes from anywhere but JimmyHQ's own pages is refused here as well. The route needs the
owner's session; a token is not enough.

**What it may do.** For every call, in this order: the id is shaped like an app id; the
call exists; the app is installed and switched on; it is within its call rates; it holds
the permission *the owner granted* (the record of consent, never the manifest's wish
list); the arguments are the ones the call takes. Then the call acts as ``app:<id>``.
Who files, who asks a model and which scope memory searches are set here and cannot be
sent. Text an app sends is stored or passed on as data. Nothing in it is ever read as an
instruction to JimmyHQ.

**What is installed.** ``find_app(core, app_id)`` answers from the install record
(``store``). Everything in this module and in ``files`` goes through that one function.

**A full app's program.** ``app.call`` queues a ``call`` delivery in the app's inbox
(``inbox``) and waits up to ``apps.call_timeout_s`` for the program's answer. A full
app's program reaches the same calls through ``api`` with its own token.

**Records.** The five ``records.*`` calls are carried out by ``described.Described`` over
the app's own database (``appdata``): one set of rules and one shape of record whether
the caller is an app's page or JimmyHQ's own screen for a described app.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from jimmyhq.apps import appdata, described, quotas, store
from jimmyhq.apps.appdata import AppDataError
from jimmyhq.apps.depth import too_deep
from jimmyhq.apps.manifest import APP_API, Manifest
from jimmyhq.apps.permissions import FULL, SANDBOXED, TIERS, allows
from jimmyhq.apps.quotas import (
    FETCH_DEFAULT_KB,
    FETCH_DEFAULT_S,
    FETCH_MAX_KB,
    FETCH_TIMEOUT_S,
    LOCAL_ONLY,
    QuotaExceeded,
)
from jimmyhq.auth import Principal
from jimmyhq.settings import SettingError
from jimmyhq.web.deps import owner

if TYPE_CHECKING:
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.bridge")

BRIDGE_VERSION = 1
CODES = ("not_allowed", "quota", "invalid", "not_found", "too_large", "unavailable",
         "internal")
# Calls the shell answers itself. They never need the server.
SHELL_CALLS = ("ui.toast", "ui.badge", "ui.settings", "ui.save", "speech.dictate",
               "speech.available")

_APP_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}\Z")
_CALL = re.compile(r"^[a-z]{1,20}\.[a-z_]{1,20}\Z")
_CARD_KIND = re.compile(r"^[a-z][a-z0-9_]{0,30}\Z")
_SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")
_HEADER = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}\Z")
_ENVELOPE_ROOM = 16 * 1024  # what a request may weigh beyond its largest value
_MODEL_ROLES = ("default", "cheap", "smart", "code", "vision")
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_OWN_HEADERS = frozenset({"host", "content-length", "connection", "transfer-encoding",
                          "upgrade", "te", "trailer", "expect", "cookie", "user-agent"})
# Headers that say who asked, as a proxy writes them: a service that believes them could
# take an app's request for one from somewhere else, so an app never sets them.
_FORWARDING_HEADERS = frozenset({"forwarded", "x-real-ip"})
FETCH_AT_ONCE = "apps.net_fetch_at_once"
FETCH_AT_ONCE_LABEL = "Network requests one app may have open at once"
_FETCHING: dict[tuple[int, str], int] = {}
_FETCHING_LOCK = threading.Lock()

class BridgeError(Exception):
    """A call that was not carried out. ``message`` is a sentence for the app's author:
    it says nothing happened and what to do instead. ``owner_message`` is JimmyHQ's own
    sentence for the owner, set only where JimmyHQ wrote it, never from what an app said.
    (A program that passes on a refusal JimmyHQ gave it may hand back that refusal's own
    ``owner_message`` with its failed answer to ``app.call``; JimmyHQ keeps it only if it
    is a sentence it gave a program, see ``inbox.check_owner_message``.)"""

    def __init__(self, code: str, message: str, retry_after_s: int | None = None,
                 owner_message: str | None = None):
        super().__init__(message)
        self.code = code if code in CODES else "internal"
        self.message = message
        self.retry_after_s = retry_after_s
        self.owner_message = owner_message


# --- the seam with install ------------------------------------------------------------------


@dataclass(frozen=True)
class Installed:
    """One installed app, as the bridge needs to know it."""

    manifest: Manifest
    enabled: bool
    granted: tuple[str, ...]  # what the owner agreed to: the only list permissions read
    folder: Path  # the folder of the version in use: .../versions/<version>-<hash8>
    development: bool = False  # linked by `jimmyhq apps dev`: files are served uncached
    # Hosts the owner gave it himself after install (``owner_hosts``), read from the record
    # on every call: none unless the version in use declares ``owner_hosts``.
    owner_hosts: tuple[str, ...] = ()
    # Addresses on the owner's own network he let it reach, "<scheme>://<host>:<port>"
    # (``local_reach``), read from the record on every call.
    local_addresses: tuple[str, ...] = ()
    # For each of those, the addresses its host led to when the owner added or last
    # confirmed it: at a request, a name must lead to these and nowhere else.
    local_resolved: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def reaches(self, host: str) -> bool:
        """Whether ``net.fetch`` may go to this exact host: one the owner agreed to at
        install, or one he gave the app himself. Nothing else."""
        return allows(self.granted, "net", host=host) or host in self.owner_hosts


def find_app(core: Core, app_id: str) -> Installed | None:
    """What is installed under this id, or None: also None for an app whose files have
    gone missing or no longer read, since nothing of it can be shown or run.

    ``granted`` is the record of what the owner agreed to (the ``apps`` lens), never the
    manifest's own list.
    """
    if not isinstance(app_id, str) or not _APP_ID.match(app_id):
        return None
    row = store.get(core, app_id)
    if row is None:
        return None
    try:
        manifest = store.manifest_of(core, app_id)
    except store.AppError:
        return None
    from jimmyhq.apps import dev, local_reach, owner_hosts  # noqa: PLC0415

    return Installed(manifest=manifest, enabled=bool(row["enabled"]),
                     granted=tuple(row["permissions"]),
                     folder=store.version_dir(core, app_id, row["version"], row["tree"]),
                     development=dev.is_development(core, app_id),
                     owner_hosts=owner_hosts.granted(manifest, row),
                     local_addresses=local_reach.granted(row),
                     local_resolved=local_reach.stored(row))


# --- what a call is given -------------------------------------------------------------------


@dataclass(frozen=True)
class Ctx:
    core: Core
    app: Installed

    @property
    def id(self) -> str:
        return self.app.manifest.id

    @property
    def actor(self) -> str:
        return self.app.manifest.actor

    def data(self) -> appdata.AppData:
        return appdata.open_data(self.core, self.id, self.app.manifest.tables)

    def limits(self) -> appdata.Limits:
        return storage_limits(self.core, self.id)


def storage_limits(core: Core, app_id: str) -> appdata.Limits:
    """The owner's storage settings for this app, as the store wants them."""
    return appdata.Limits(
        storage_bytes=quotas.limit(core, app_id, "storage_mb") * 1024 * 1024,
        value_bytes=quotas.limit(core, app_id, "value_kb") * 1024,
        records=quotas.limit(core, app_id, "records"))


def record_caps(core: Core, app_id: str) -> described.Caps:
    """The owner's limits on one record and one table, for this app."""
    return described.Caps(
        text_chars=quotas.limit(core, app_id, "record_text_chars"),
        record_bytes=quotas.limit(core, app_id, "record_kb") * 1024,
        table_records=quotas.limit(core, app_id, "table_records"))


def records_of(ctx: Ctx) -> described.Described:
    """The app's records, checked by its manifest's tables and kept in its own database."""
    manifest = ctx.app.manifest
    if not manifest.tables:
        raise BridgeError("invalid", "Your app declares no tables, so it has no records. "
                                     "Nothing was done. Add \"tables\" to jimmyhq.app.json, "
                                     "or use storage.set for plain values.")
    try:
        return described.Described(manifest.tables, manifest.views,
                                   ctx.data().records(ctx.limits()),
                                   caps=record_caps(ctx.core, ctx.id))
    except described.DescribedError as error:
        raise BridgeError("invalid", f"{error} Nothing was done.") from None


def only(call: str, args: dict[str, Any], takes: tuple[str, ...],
         needs: tuple[str, ...] = (), note: str = "") -> None:
    """Refuse an argument the call does not take, or a missing one it needs."""
    for name in args:
        if name not in takes:
            shown = name if re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) else "that argument"
            listed = ", ".join(takes) if takes else "no arguments"
            raise BridgeError("invalid", f"{call} does not take \"{shown}\". Nothing was "
                                         f"done. {note}It takes: {listed}.")
    for name in needs:
        if args.get(name) is None:
            raise BridgeError("invalid", f"{call} needs \"{name}\". Nothing was done. Pass "
                                         f"it in the call's arguments.")


def text_arg(call: str, args: dict[str, Any], name: str, most: int, *,
             default: str | None = None) -> str | None:
    value = args.get(name)
    if value is None:
        return default
    if not isinstance(value, str) or len(value) > most:
        raise BridgeError("invalid", f"\"{name}\" for {call} must be text of at most {most} "
                                     "characters. Nothing was done.")
    return value


# --- the calls ------------------------------------------------------------------------------


def storage_get(ctx: Ctx, args: dict[str, Any]) -> Any:
    only("storage.get", args, ("key",), ("key",))
    return ctx.data().get(args["key"])


def storage_set(ctx: Ctx, args: dict[str, Any]) -> Any:
    only("storage.set", args, ("key", "value"), ("key",))
    if args.get("value") is None:
        ctx.data().delete(args["key"])  # storing nothing is the same as removing it
    else:
        ctx.data().set(args["key"], args["value"], ctx.limits())
    return None


def storage_delete(ctx: Ctx, args: dict[str, Any]) -> bool:
    only("storage.delete", args, ("key",), ("key",))
    return ctx.data().delete(args["key"])


def storage_keys(ctx: Ctx, args: dict[str, Any]) -> list[str]:
    only("storage.keys", args, ("prefix", "limit"))
    return ctx.data().keys(args.get("prefix", ""), args.get("limit"))


_RECORD_TAKES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "records.list": (("table", "view", "filter", "sort", "search", "limit", "after"),
                     ("table",)),
    "records.get": (("table", "id"), ("table", "id")),
    "records.create": (("table", "values"), ("table", "values")),
    "records.update": (("table", "id", "values"), ("table", "id", "values")),
    "records.delete": (("table", "id", "unlink"), ("table", "id")),
}


def records_call(name: str) -> Callable[[Ctx, dict[str, Any]], Any]:
    """One of the five ``records.*`` calls: the arguments it takes, then the app's records."""
    takes, needs = _RECORD_TAKES[name]

    def run(ctx: Ctx, args: dict[str, Any]) -> Any:
        only(name, args, takes, needs)
        return records_of(ctx).handle(name, args)

    return run


_TASK_SHOWN = ("id", "title", "notes", "area", "due", "due_time", "repeat", "status",
               "created", "done_at")


def _task(task: dict[str, Any]) -> dict[str, Any]:
    return {key: task.get(key) for key in _TASK_SHOWN}


def tasks_list(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq import tasks  # noqa: PLC0415

    only("tasks.list", args, ("when", "area"))
    when = args.get("when") or "open"
    if when not in ("open", "today"):
        raise BridgeError("invalid", "\"when\" for tasks.list must be \"open\" or "
                                     "\"today\". Nothing was read.")
    area = text_arg("tasks.list", args, "area", 60)
    service = tasks.service(ctx.core)
    found = (service.list_today if when == "today" else service.list_open)(
        assignee="owner", area=area)
    return {"tasks": [_task(t) for t in found]}


def tasks_create(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq import tasks  # noqa: PLC0415

    only("tasks.create", args, ("title", "notes", "area", "due", "due_time", "repeat"),
         ("title",), note="A task an app adds is always the owner's. ")
    check_private_task(ctx.app.manifest, args)
    quotas.take(ctx.core, ctx.id, "tasks_per_day")
    made = tasks.service(ctx.core).create(
        args["title"], actor=ctx.actor, notes=args.get("notes") or "", area=args.get("area"),
        due=args.get("due"), due_time=args.get("due_time"), repeat=args.get("repeat"),
        assignee="owner")
    return _task(made)


def tasks_complete(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq import tasks  # noqa: PLC0415

    only("tasks.complete", args, ("id",), ("id",))
    service = tasks.service(ctx.core)
    check_own_task(ctx.actor, service.get(args["id"]))
    return _task(service.complete(args["id"], actor=ctx.actor))


def check_private_task(manifest: Manifest, args: dict[str, Any]) -> None:
    """A private app's task, like its card, carries a title only: a task is on the
    owner's record."""
    if manifest.private and args.get("notes"):
        raise BridgeError("invalid", "Your app is marked private, so a task it adds has a "
                                     "title only, and notes are not taken. Nothing was "
                                     "added. Keep the details in app.private_db().")


def check_own_task(actor: str, task: dict[str, Any]) -> None:
    """An app marks done only the tasks it added itself. Reading the list (with
    ``tasks.read``) does not make the owner's other tasks its to finish."""
    if task.get("filed_by") != actor:
        raise BridgeError("not_allowed", "Your app can mark done only the tasks it added "
                                         "itself, and it did not add this one. Nothing was "
                                         "changed. The owner can finish it on the Tasks "
                                         "screen.")


_CARD_TAKES = ("kind", "title", "summary", "body", "why", "details", "subject", "lane",
               "actions")


def card_kind(manifest: Manifest, kind: Any) -> str:
    """The kind a card is filed under: always ``app.<id>.<kind>``, whatever was sent."""
    declared = [c.kind for c in manifest.cards]
    if kind is None:
        kind = declared[0] if declared else "card"
    if not isinstance(kind, str) or not _CARD_KIND.match(kind):
        raise BridgeError("invalid", "\"kind\" for bench.file must be one short lower-case "
                                     "word such as \"draft\". Nothing was filed. JimmyHQ "
                                     f"adds \"app.{manifest.id}.\" in front itself.")
    if declared and kind not in declared:
        raise BridgeError("invalid", "\"kind\" for bench.file must be one of the card types "
                                     f"your manifest declares: {', '.join(declared)}. "
                                     "Nothing was filed.")
    return manifest.card_kind(kind)


def card_subject(manifest: Manifest, subject: Any) -> str | None:
    """A card's subject, kept inside the app's own names: ``app.<id>:<what was sent>``.
    An app cannot attach its card to a task, a memory or anything else it does not own."""
    if subject is None:
        return None
    if not isinstance(subject, str) or not _SUBJECT.match(subject):
        raise BridgeError("invalid", "\"subject\" for bench.file must be a short name of "
                                     "letters, digits, dots, colons and hyphens, such as "
                                     "\"post:12\". Nothing was filed.")
    return f"app.{manifest.id}:{subject}"


def open_cards(core: Core, actor: str) -> int:
    """How many cards this actor has on the Bench now, in any lane."""
    from jimmyhq import bench  # noqa: PLC0415

    lanes = bench.board(core)["lanes"]
    return sum(1 for cards in lanes.values() for card in cards if card["filed_by"] == actor)


# What a private app's card may carry besides its title: parts that hold none of its words.
# "kind" is one of the card types its manifest declares, "lane" is one of two fixed words,
# "actions" are the Bench's own buttons exactly as the Bench defines them, and "subject" is
# one of the short words the app's manifest declares in ``card_subjects`` (up to twelve lower
# case letters), with at most a number after it (``reminder:12``), which only the app itself
# can read back. A free word could carry what the app means to keep private.
PRIVATE_CARD_TAKES = ("kind", "title", "lane", "actions", "subject")
_PRIVATE_SUBJECT = re.compile(r"([a-z]{1,12})(:[0-9]{1,9})?")


def check_private_card(manifest: Manifest, args: dict[str, Any]) -> None:
    """A private app's card is on the owner's record, so it carries its title and nothing
    of the app's own words: no summary, body, reason, details or free-text buttons."""
    if not manifest.private:
        return
    from jimmyhq.bench import cards as bench_cards  # noqa: PLC0415

    extra = [name for name in args if name not in PRIVATE_CARD_TAKES]
    if extra:
        raise BridgeError("invalid", "Your app is marked private, so a card it files "
                                     "carries a title only, with no text of its own besides "
                                     f"it. Nothing was filed. Leave out {', '.join(extra)}. "
                                     "Keep the details in app.private_db(); the Bench shows "
                                     "an \"Open\" button that takes the owner to your app. "
                                     "Allowed: " + ", ".join(PRIVATE_CARD_TAKES) + ".")
    actions = args.get("actions")
    if actions is not None and not (isinstance(actions, list)
                                    and all(bench_cards.is_bench_action(a) for a in actions)):
        raise BridgeError("invalid", "Your app is marked private, so a card it files may "
                                     "offer only the Bench's own buttons (\"approve\", "
                                     "\"not_yet\", \"no\", \"ok\", \"undo\"), written "
                                     "exactly as the Bench writes them, never a button with "
                                     "words of your own. Nothing was filed. Leave out "
                                     "\"actions\" to get the usual buttons.")
    subject = args.get("subject")
    if subject is not None:
        found = _PRIVATE_SUBJECT.fullmatch(subject) if isinstance(subject, str) else None
        if found is None or found.group(1) not in manifest.card_subjects:
            declared = ", ".join(f'"{w}"' for w in manifest.card_subjects) or "none declared"
            raise BridgeError("invalid", "Your app is marked private, so a card's \"subject\" "
                                         "may be only one of the words your manifest lists "
                                         "under \"card_subjects\" (a lower-case word of at "
                                         "most 12 letters), with a number after it if you "
                                         "like, such as \"reminder:12\". It is never text "
                                         f"about what the card is for. Declared: {declared}. "
                                         "Nothing was filed.")


def bench_file(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq import bench  # noqa: PLC0415

    manifest = ctx.app.manifest
    only("bench.file", args, _CARD_TAKES, ("title",),
         note="Who filed a card, its source and its place on the ladder are set by "
              "JimmyHQ. ")
    check_private_card(manifest, args)
    fields = {key: args[key] for key in ("title", "summary", "body", "why", "details",
                                         "lane", "actions") if args.get(key) is not None}
    fields["kind"] = card_kind(manifest, args.get("kind"))
    fields["subject"] = card_subject(manifest, args.get("subject"))
    most = quotas.limit(ctx.core, ctx.id, "open_cards")
    have = open_cards(ctx.core, ctx.actor)
    if have + 1 > most:
        raise BridgeError(
            "quota",
            f"This app may have at most {most} cards waiting on the Bench, and it has "
            f"{have}. Nothing was filed. Wait for the owner to decide some, or ask them to "
            f"raise \"{quotas.BY_NAME['open_cards'].label}\" under Settings, Apps.")
    card_id = bench.file_card(ctx.core, filed_by=ctx.actor, source=manifest.name, **fields)
    return {"card_id": card_id}


def settings_get(ctx: Ctx, args: dict[str, Any]) -> Any:
    only("settings.get", args, ("key",))
    manifest = ctx.app.manifest
    declared = {s.key: s for s in manifest.settings}

    def read(key: str) -> Any:
        try:
            return ctx.core.settings.get(manifest.setting_key(key))
        except SettingError:
            return declared[key].default  # not registered in this process yet

    key = args.get("key")
    if key is None:
        return {name: read(name) for name in declared}
    if not isinstance(key, str) or key not in declared:
        listed = ", ".join(declared) or "none: add \"settings\" to jimmyhq.app.json"
        raise BridgeError("not_found", "Your app declares no setting of that name, and it "
                                       "can read only its own. Nothing was read. Its "
                                       f"settings are: {listed}.")
    return read(key)


def local_models_only(core: Core, app: Installed) -> bool:
    """Whether this app's model calls must stay on models the owner runs themselves."""
    manifest = app.manifest
    if manifest.tier == SANDBOXED and core.settings.get(LOCAL_ONLY):
        return True
    try:
        return bool(core.settings.get(manifest.setting_key("local_models_only")))
    except SettingError:
        return bool(manifest.private)  # that setting's default, before it is declared


_ROLE_WORDS = {
    "default": ("a model", "the default role"),
    "vision": ("a model that can see pictures", "the vision role"),
    "embeddings": ("a model for similarity search", "the embeddings role"),
}


def no_model_words(app_name: str, private: bool, own_machines: bool, role: str = "default",
                   asked_local: bool = False) -> str:
    """What the owner reads when an app asks a model and none can answer: which role is
    empty and where to fill it (Settings, Models), and, for a private app (or one held to
    their own machines, by them or by that one call), that the model must be on their own
    machines. A role other than the default borrows the default role's models, so the
    sentence offers both."""
    what, where = _ROLE_WORDS.get(role, ("a model", f"the {role} role (or the default role)"))
    if private:
        return (f"{app_name} is a private app, so it needs {what} on your own machines, and "
                f"none is set up yet. Add one that runs on your own machines to {where} "
                "under Settings, Models, then try again.")
    if asked_local:
        return (f"{app_name} asked to keep this on your own machines, and there is no model "
                f"there for {where}. Add one that runs on your own machines to it under "
                "Settings, Models, then try again.")
    if own_machines:
        return (f"{app_name} may only use {what} on your own machines, and none is set up "
                f"yet. Add one that runs on your own machines to {where} under Settings, "
                "Models, then try again.")
    return (f"{app_name} needs {what}, and none is set up yet. Add one to {where} under "
            "Settings, Models, then try again.")


def _brain(core: Core) -> Any:
    from jimmyhq import models  # noqa: PLC0415

    return models.brain(core)


def narrowing_flag(args: dict[str, Any], name: str) -> bool:
    """Rule ``a call may only narrow``: ``stay_local`` (only a model on the owner's own
    machines may answer this call) and ``off_record`` (keep token counts only, never the
    text) may be ``true`` or left out. A call can make itself stricter than the owner's
    settings for that one ask, never looser, so ``false`` is refused like any value that
    is not ``true``."""
    value = args.get(name)
    if value is None:
        return False
    if value is not True:
        raise BridgeError("invalid", f"\"{name}\" for model.ask can only be true, which makes "
                                     "this one call stricter; leave it out otherwise. "
                                     "Nothing was sent.")
    return True


async def model_ask(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq.apps import reach  # noqa: PLC0415 - pictures and outside text
    from jimmyhq.models.shapes import ModelError  # noqa: PLC0415

    only("model.ask", args, ("prompt", "system", "role", "json_schema", "max_tokens",
                             "images", "untrusted", "stay_local", "off_record"),
         ("prompt",),
         note="Who is asking, which provider answers and whether it stays local are "
              "decided by the owner's settings, never by the call. ")
    stay_local = narrowing_flag(args, "stay_local")
    off_record = narrowing_flag(args, "off_record")
    core, app_id = ctx.core, ctx.id
    most = quotas.limit(core, app_id, "value_kb") * 1024
    prompt = text_arg("model.ask", args, "prompt", most)
    system = text_arg("model.ask", args, "system", most)
    role = reach.ask_role(args.get("role"), args.get("images") is not None)
    if not prompt or not prompt.strip():
        raise BridgeError("invalid", "\"prompt\" for model.ask is empty. Nothing was sent. "
                                     "Pass the text to ask.")
    options: dict[str, Any] = {}
    if args.get("json_schema") is not None:
        options["json_schema"] = args["json_schema"]
    if args.get("max_tokens") is not None:
        options["max_tokens"] = args["max_tokens"]

    def admit() -> bool:
        quotas.ensure_room(core, app_id, "model_tokens_per_day")
        quotas.take(core, app_id, "model_calls_per_day")
        quotas.take(core, app_id, "model_calls_per_hour")
        return stay_local or local_models_only(core, ctx.app)

    content, _ = await run_in_threadpool(reach.ask_content, ctx, args, prompt, most)
    def owner_words() -> str:
        held = local_models_only(core, ctx.app)
        return no_model_words(ctx.app.manifest.name, bool(ctx.app.manifest.private),
                              held or stay_local, role, asked_local=stay_local and not held)

    if role == reach.VISION:
        await run_in_threadpool(reach.check_vision_set_up, _brain(core), owner_words)
    local_only = await run_in_threadpool(admit)
    try:
        reply = await _brain(core).chat(
            [{"role": "user", "content": content}], role=role, system=system,
            actor=ctx.actor, caller=f"apps.{app_id}", local_only=local_only,
            keep_text=not (ctx.app.manifest.private or off_record), **options)
    except ModelError as exc:
        code = "invalid" if exc.kind == "bad_request" else "unavailable"
        sentence = reach.vision_sentence(exc.kind) or exc.public
        for_owner = None
        if exc.kind == "local_only":
            who = ("This call asked to stay on the owner's own computer"
                   if stay_local and not local_models_only(core, ctx.app)
                   else "The owner lets this app use only models on their own computer")
            sentence = (f"{who}, and no model there is set up for this role. Nothing was "
                        "sent anywhere. Ask them to add a local model to the role under "
                        "Settings, Models.")
        if exc.kind in ("local_only", "not_configured"):
            if exc.kind == "not_configured":
                sentence = ("No model is set up for this to use, so nothing was asked. Show "
                            "the owner this error's owner_message (why() in _ui.js does) "
                            "and ask again once they have added one.")
            for_owner = await run_in_threadpool(owner_words)
        if exc.kind == "no_images":
            for_owner = reach.no_pictures_words(ctx.app.manifest.name)
        raise BridgeError(code, sentence, owner_message=for_owner) from None
    used = int(reply.input_tokens or 0) + int(reply.output_tokens or 0)
    await run_in_threadpool(quotas.add, core, app_id, "model_tokens_per_day", used)
    return {"text": reply.text, "data": reply.data, "local": reply.is_local,
            "model": reply.model}


def memory_recall(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    from jimmyhq import memory  # noqa: PLC0415

    only("memory.recall", args, ("query", "k"), ("query",),
         note="An app searches the shared part of memory only. ")
    query = text_arg("memory.recall", args, "query", 2000)
    k = args.get("k")
    if k is not None and (isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 20):
        raise BridgeError("invalid", "\"k\" for memory.recall must be a whole number from "
                                     "1 to 20. Nothing was searched.")
    if not query or not query.strip():
        raise BridgeError("invalid", "\"query\" for memory.recall is empty. Nothing was "
                                     "searched. Pass what to look for.")
    # A private app's question is its own: the receipt keeps neither its length nor a
    # hash of it, as for an off-the-record conversation.
    found = memory.recall(ctx.core, query, actor=ctx.actor, caller=f"apps.{ctx.id}",
                          scope=("shared",), k=k, private=ctx.app.manifest.private)
    return {"passages": [{"text": p.text, "note": p.note, "heading": p.heading}
                         for p in found.passages],
            "text": found.render(), "receipt": found.receipt_id}


def is_local(app: Installed, parsed: Any) -> bool:
    """Whether a fetch goes to an address on the owner's own network that he let this app
    reach (``local_reach``): exactly its scheme, host and port. A host the app may reach
    the ordinary way, over https on the standard port, goes the ordinary way."""
    from jimmyhq.apps import local_reach  # noqa: PLC0415

    ordinary = (parsed.scheme == "https" and parsed.port == 443 and parsed.literal is None
                and app.reaches(parsed.host))
    return not ordinary and local_reach.matches(app.local_addresses, parsed)


def fetch_target(app: Installed, url: Any) -> Any:
    """The address a ``net.fetch`` may go to: https, the standard port, and exactly a
    host the owner granted; or exactly an address on his own network he let it reach.
    Returns the parsed address. No network."""
    from jimmyhq.lookup import safety  # noqa: PLC0415
    from jimmyhq.lookup.errors import WebError  # noqa: PLC0415

    try:
        parsed = safety.parse(url)
    except WebError:
        raise BridgeError("invalid", "\"url\" for net.fetch must be a full https address "
                                     "such as https://api.example.com/v1/items. Nothing "
                                     "was sent.") from None
    if is_local(app, parsed):
        return parsed
    hosts = sorted({*(g.partition(":")[2] for g in app.granted if g.startswith("net:")),
                    *app.owner_hosts})
    if parsed.literal is not None or not app.reaches(parsed.host):
        listed = ", ".join(hosts) if hosts else "none"
        mine = (" or, for an address only the owner knows, declare \"owner_hosts\" and let "
                "the owner add it on your app's page" if app.manifest.owner_hosts is None
                else " or ask the owner to add it on your app's page under Settings, Apps")
        raise BridgeError(
            "not_allowed",
            f"Your app \"{app.manifest.id}\" has not been granted that host, so nothing "
            f"was sent. The hosts it may reach are: {listed}. Add \"net:<host>\" to "
            "\"permissions\" in jimmyhq.app.json, which the owner is asked to agree to "
            f"when they install or update it,{mine}. For an address on the owner's own "
            "network, the owner adds it himself under \"Addresses on your own network\" on "
            "your app's page under Apps; tell him which in your settings.")
    if parsed.scheme != "https" or parsed.port != 443:
        raise BridgeError("not_allowed", "net.fetch only uses https on the standard port, "
                                         "so nothing was sent. Write the address as "
                                         f"https://{parsed.host}/...")
    return parsed


def check_granted_host_public(checked: Any) -> None:
    """Rule ``a_granted_host_is_always_public``: a host an app reaches the ordinary way,
    whether its manifest names it (``net:<host>``) or the owner gave it (``owner_hosts``),
    is checked where its name resolves at every request and may never lead to the owner's
    own machine or private network, even when the owner let web lookup read that host
    there (``lookup.allow_private_hosts``): that exception is for web lookup, and what a
    site's name points at can change after it was granted. The one way an app reaches such
    an address is the owner adding it under "Addresses on your own network"
    (``local_reach``)."""
    if checked.allowed_private:
        raise BridgeError("not_allowed", f"{checked.host} leads to an address on the "
                                         "owner's own machine or private network. Nothing was "
                                         "sent. An app reaches such an address only when "
                                         "the owner adds it himself under \"Addresses on your "
                                         "own network\" on your app's page under Apps; tell "
                                         "him which in your settings.")


async def check_ordinary(core: Any, app: Installed, parsed: Any, resolver: Any) -> Any:
    """Resolve and check a host the app reaches the ordinary way, now. A name that leads to
    a private address is refused (``check_granted_host_public``) unless the owner added
    exactly that address under "Addresses on your own network", when it is held to those
    rules instead (``local_reach.check_at_connect``). Raises ``WebError`` or
    ``BridgeError``."""
    from jimmyhq.apps import local_reach  # noqa: PLC0415
    from jimmyhq.lookup import safety  # noqa: PLC0415
    from jimmyhq.lookup.errors import WebError  # noqa: PLC0415

    added = local_reach.matches(app.local_addresses, parsed)
    try:
        checked = await safety.check(core, parsed.url, resolver)
    except WebError as exc:
        if exc.kind != "refused" or not added:
            raise
        checked = None
    if checked is None or (checked.allowed_private and added):
        led = dict(app.local_resolved).get(
            local_reach.origin_of(parsed.scheme, parsed.host, parsed.port), ())
        return await local_reach.check_at_connect(core, parsed, resolver, led)
    check_granted_host_public(checked)
    return checked


def hosts_list(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The hosts the owner gave this app himself, oldest first, with what its manifest
    asked for. There is no call that adds one: only the owner can, on the app's page."""
    only("hosts.list", args, ())
    asked = ctx.app.manifest.owner_hosts
    if asked is None:
        raise BridgeError("not_allowed", "Your app does not declare \"owner_hosts\", so the "
                                         "owner cannot give it hosts of his own and there are "
                                         "none to list. Nothing was read. Declare it in "
                                         "jimmyhq.app.json, such as \"owner_hosts\": "
                                         "{\"purpose\": \"Publish drafts to your own "
                                         "site.\", \"max\": 3}.")
    return {"hosts": list(ctx.app.owner_hosts), "max": asked.max, "purpose": asked.purpose}


def ui_limits(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Rule ``an app may ask what it may save and how big an answer may be``: the owner's
    own limits for files a page hands them, for one answer to a call, and for a full-size
    picture, as plain numbers, so an app sizes its work to them instead of finding them out
    by being refused. It needs no permission and says nothing else about the owner."""
    only("ui.limits", args, ())
    get = ctx.core.settings.get
    return {"save_max_mb": int(get("apps.save_max_mb")),
            "save_parts": int(get("apps.save_parts")),
            "save_per_minute": int(get("apps.save_per_minute")),
            "save_html": bool(get("apps.save_html")),
            "call_max_kb": int(get("apps.call_max_kb")),
            "picture_px": int(get("apps.picture_px"))}


def fetch_headers(headers: Any) -> dict[str, str]:
    """The headers an app may send. JimmyHQ sets the ones that say where a request goes
    and how long it is."""
    if headers is None:
        return {}
    if not isinstance(headers, dict) or len(headers) > 30:
        raise BridgeError("invalid", "\"headers\" for net.fetch must be an object of at "
                                     "most 30 names and values. Nothing was sent.")
    out: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower() if isinstance(name, str) else ""
        if (not _HEADER.match(name if isinstance(name, str) else "")
                or lower in _OWN_HEADERS or lower.startswith(("proxy-", "sec-"))):
            raise BridgeError("invalid", "net.fetch does not send that header: JimmyHQ "
                                         "sets Host, Cookie, User-Agent, the length and "
                                         "the connection headers itself. Nothing was sent. "
                                         "Leave it out.")
        if lower in _FORWARDING_HEADERS or lower.startswith("x-forwarded-"):
            raise BridgeError("invalid", f"net.fetch does not send {name}: it says where a "
                                         "request came from, as a proxy writes it, and an "
                                         "app's request comes from JimmyHQ. Nothing was "
                                         "sent. Leave it out.")
        if (not isinstance(value, str) or len(value) > 4000
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise BridgeError("invalid", "A header's value for net.fetch must be one line "
                                         "of text of at most 4000 characters. Nothing was "
                                         "sent.")
        out[name] = value
    return out


def fetch_timeout(most_s: int, asked: Any) -> int:
    """How long a fetch may take: the owner's limit, or less when the app asks for less
    with ``timeout_s`` (a whole number of seconds, at least 1)."""
    if asked is None:
        return int(most_s)
    if isinstance(asked, bool) or not isinstance(asked, int) or asked < 1:
        raise BridgeError("invalid", "\"timeout_s\" for net.fetch is a whole number of "
                                     "seconds, at least 1. Nothing was sent.")
    return min(int(most_s), asked)


@dataclass(frozen=True)
class FetchLimits:
    """How large one answer may be and how long one fetch may take, for one host: the
    owner's ceiling, or less where the manifest expects less or says nothing (rule
    ``net_expects_is_what_a_host_gets``). Each refusal names what set the limit."""

    host: str
    most_kb: int
    wait_s: int
    ceiling_kb: int  # the owner's apps.net_fetch_max_kb
    ceiling_s: int  # the owner's wait setting for this kind of fetch
    wait_key: str  # which setting that is
    hint_kb: int | None = None
    hint_s: int | None = None

    def too_large(self) -> BridgeError:
        if self.hint_kb is not None and self.hint_kb <= self.ceiling_kb:
            return BridgeError("too_large", (
                f"The answer from {self.host} is larger than the {self.hint_kb} KB your "
                "manifest's \"net_expects\" says to expect from it, so none of it was "
                "returned. Ask for less at a time, or raise \"max_kb\" there."))
        if self.hint_kb is None and self.most_kb < self.ceiling_kb:
            return BridgeError("too_large", (
                f"The answer from {self.host} is larger than the {self.most_kb} KB an app is "
                f"given from a host its manifest says nothing about (\"{FETCH_DEFAULT_KB_LABEL}"
                f"\", {FETCH_DEFAULT_KB}), so none of it was returned. Ask for less at a "
                f"time, or say what to expect from {self.host} with \"max_kb\" under "
                "\"net_expects\" in your manifest."),
                owner_message=(f"An app was sent an answer from {self.host} larger than the "
                               f"{self.most_kb} KB you allow for a host it says nothing "
                               f"about, so it was refused. Raise \"{FETCH_DEFAULT_KB_LABEL}"
                               "\" under Settings, Apps, advanced, if you want it to have it."))
        need = (f" Your manifest expects up to {self.hint_kb} KB from {self.host}."
                if self.hint_kb is not None else "")
        return BridgeError("too_large", (
            f"The answer from {self.host} is larger than the {self.ceiling_kb} KB the owner "
            f"lets an app be given (\"{FETCH_MAX_KB_LABEL}\", {FETCH_MAX_KB}), so none of it "
            f"was returned.{need} Ask for less at a time, or ask the owner to raise it under "
            "Settings, Apps."),
            owner_message=(f"An app was sent an answer from {self.host} larger than the "
                           f"{self.ceiling_kb} KB you allow, so it was refused. Raise "
                           f"\"{FETCH_MAX_KB_LABEL}\" under Settings, Apps, advanced, if "
                           "you want it to have it."))

    def too_slow(self, waited_s: int) -> BridgeError:
        label = _WAIT_LABELS.get(self.wait_key, self.wait_key)
        if waited_s < self.wait_s:
            return BridgeError("unavailable", f"{self.host} took longer than {waited_s} "
                                              "seconds, the wait you asked for, to answer. "
                                              "Try again later, or ask for a longer wait.")
        if self.hint_s is not None and self.hint_s <= self.ceiling_s:
            return BridgeError("unavailable", (
                f"{self.host} took longer than the {self.hint_s} seconds your manifest's "
                "\"net_expects\" says to expect, so nothing came back. Try again later, or "
                "raise \"timeout_s\" there."))
        if self.hint_s is None and self.wait_s < self.ceiling_s:
            return BridgeError("unavailable", (
                f"{self.host} took longer than the {self.wait_s} seconds an app waits for a "
                f"host its manifest says nothing about (\"{FETCH_DEFAULT_S_LABEL}\", "
                f"{FETCH_DEFAULT_S}), so nothing came back. Try again later, or say how long "
                f"to wait for {self.host} with \"timeout_s\" under \"net_expects\" in your "
                "manifest."),
                owner_message=(f"An app's request to {self.host} was stopped after "
                               f"{self.wait_s} seconds, the wait you allow for a host it says "
                               f"nothing about. Raise \"{FETCH_DEFAULT_S_LABEL}\" under "
                               "Settings, Apps, advanced, if it needs longer."))
        need = (f" Your manifest expects to wait up to {self.hint_s} seconds for it."
                if self.hint_s is not None else "")
        return BridgeError("unavailable", (
            f"{self.host} took longer than {self.ceiling_s} seconds to answer, the most the "
            f"owner lets an app wait (\"{label}\", {self.wait_key}), so nothing came back."
            f"{need} Try again later, or ask the owner to raise it under Settings, Apps."),
            owner_message=(f"An app's request to {self.host} was stopped after "
                           f"{self.ceiling_s} seconds, the most you allow. Raise \"{label}\" "
                           "under Settings, Apps, advanced, if it needs longer."))


FETCH_MAX_KB_LABEL = "Largest network answer for an app (KB)"
FETCH_DEFAULT_KB_LABEL = "Network answer size for a host an app says nothing about (KB)"
FETCH_DEFAULT_S_LABEL = "Wait for a host an app says nothing about (seconds)"
_WAIT_LABELS = {"apps.net_fetch_timeout_s": "Longest wait for an app's network request "
                                            "(seconds)",
                "apps.net_fetch_file_timeout_s": "Longest wait for an app's file to be sent "
                                                 "(seconds)"}


def fetch_limits(core: Core, app: Installed, host: str, *, upload: bool) -> FetchLimits:
    """Rule ``net_expects_is_what_a_host_gets``: a host the manifest says what it expects of
    gets that, and any other host gets the owner's defaults; neither is ever more than the
    owner's ceilings. Nothing an app writes raises a limit past them."""
    from jimmyhq.apps import file_body  # noqa: PLC0415 - it imports this module

    wait_key = file_body.TIMEOUT_S if upload else FETCH_TIMEOUT_S
    ceiling_kb = int(core.settings.get(FETCH_MAX_KB))
    ceiling_s = int(core.settings.get(wait_key))
    said = app.manifest.expects(host)
    hint_kb = said.max_kb if said else None
    hint_s = said.timeout_s if said else None
    default_kb = int(core.settings.get(FETCH_DEFAULT_KB))
    # A file being sent already has its own, longer, ceiling and no shorter default.
    default_s = ceiling_s if upload else int(core.settings.get(FETCH_DEFAULT_S))
    return FetchLimits(host=host, ceiling_kb=ceiling_kb, ceiling_s=ceiling_s,
                       wait_key=wait_key, hint_kb=hint_kb, hint_s=hint_s,
                       most_kb=min(ceiling_kb, hint_kb or default_kb),
                       wait_s=min(ceiling_s, hint_s or default_s))


def expect_notes(core: Core, manifest: Manifest) -> list[str]:
    """For the app's page: one sentence for each host where the app expects more than the
    owner's settings allow now, saying what is refused and which setting to raise."""
    from jimmyhq.apps import permissions as perms  # noqa: PLC0415

    ceiling_kb = int(core.settings.get(FETCH_MAX_KB))
    ceiling_s = int(core.settings.get(FETCH_TIMEOUT_S))
    out: list[str] = []
    for said in manifest.net_expects:
        if said.max_kb is not None and said.max_kb > ceiling_kb:
            out.append(f"{manifest.name} expects answers of up to "
                       f"{perms.size_words(said.max_kb)} from {said.host}, and you allow "
                       f"{perms.size_words(ceiling_kb)}: larger ones are refused. Raise "
                       f"\"{FETCH_MAX_KB_LABEL}\" under Settings, Apps, advanced, if you "
                       "want them.")
        if said.timeout_s is not None and said.timeout_s > ceiling_s:
            label = _WAIT_LABELS[FETCH_TIMEOUT_S]
            out.append(f"{manifest.name} expects to wait up to "
                       f"{perms.wait_words(said.timeout_s)} for {said.host}, and you allow "
                       f"{perms.wait_words(ceiling_s)}: a slower answer is cut off. Raise "
                       f"\"{label}\" under Settings, Apps, advanced, if it needs longer.")
    return out


async def net_fetch(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    import asyncio  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from jimmyhq import lookup  # noqa: PLC0415
    from jimmyhq.apps import file_body, local_reach  # noqa: PLC0415 - they import this module
    from jimmyhq.lookup.errors import WebError  # noqa: PLC0415

    only("net.fetch", args, ("url", "method", "headers", "body", "json", "file", "multipart",
                             "fresh_connection", "as", "timeout_s"), ("url",))
    core = ctx.core
    parsed = fetch_target(ctx.app, args["url"])
    local = is_local(ctx.app, parsed)
    method = str(args.get("method") or "GET").upper()
    if method not in _METHODS:
        raise BridgeError("invalid", "\"method\" for net.fetch must be one of: "
                                     f"{', '.join(_METHODS)}. Nothing was sent.")
    want = args.get("as") or "text"
    if want not in ("text", "base64"):
        raise BridgeError("invalid", "\"as\" for net.fetch must be \"text\" or "
                                     "\"base64\". Nothing was sent.")
    headers = fetch_headers(args.get("headers"))
    fetch_timeout(1, args.get("timeout_s"))  # refused before anything is built
    file_body.check_one_body(args)
    fresh = file_body.check_fresh(args.get("fresh_connection"))
    built = file_body.has_files(args)
    if built:
        file_body.check_method(method)
    content: Any = None
    if args.get("json") is not None and args.get("body") is not None:
        raise BridgeError("invalid", "net.fetch takes \"body\" or \"json\", not both. "
                                     "Nothing was sent.")
    if built:
        pass  # built from the files below, after every other check
    elif args.get("json") is not None:
        content = appdata.encode(args["json"], "\"json\"").encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif args.get("body") is not None:
        if not isinstance(args["body"], str):
            raise BridgeError("invalid", "\"body\" for net.fetch must be text. Nothing was "
                                         "sent. Use \"json\" to send an object.")
        content = args["body"].encode("utf-8")
    if fresh and not built:
        headers["Connection"] = "close"
    upload: Any = None
    if built:
        upload = await run_in_threadpool(file_body.build, core, ctx.id, args, headers)
        headers = file_body.body_headers(headers, upload, fresh)
        content = upload.chunks()
    began = False
    try:
        began = fetch_begins(core, ctx.id)
        await run_in_threadpool(quotas.take, core, ctx.id, "fetches_per_hour")
    except BaseException:
        if began:
            fetch_ends(core, ctx.id)
        if upload is not None:
            upload.close()
        raise
    body = bytearray()
    try:
        limits = fetch_limits(core, ctx.app, parsed.host, upload=upload is not None)
        most = limits.most_kb * 1024
        timeout_s = fetch_timeout(limits.wait_s, args.get("timeout_s"))
        service = lookup.service(core)
        async with asyncio.timeout(timeout_s):
            # Resolved and checked here: a granted name that points at a private or
            # local address is still refused.
            if local:
                # Only the exact address the owner added, checked again now, and never a
                # cloud's metadata or JimmyHQ's own port (local_reach).
                led = dict(ctx.app.local_resolved).get(
                    local_reach.origin_of(parsed.scheme, parsed.host, parsed.port), ())
                checked = await local_reach.check_at_connect(core, parsed, service.resolver,
                                                             led)
            else:
                checked = await check_ordinary(core, ctx.app, parsed, service.resolver)
            async with service.client(timeout_s) as client:
                request = client.build_request(
                    method, checked.connect_url, content=content,
                    headers=headers | {"Host": checked.host_header, "User-Agent": "JimmyHQ"},
                    extensions={"sni_hostname": checked.host})
                response = await client.send(request, stream=True)
                try:
                    async for piece in response.aiter_bytes():
                        body += piece
                        if len(body) > most:
                            raise limits.too_large()
                finally:
                    await response.aclose()
    except WebError as exc:
        code = "not_allowed" if exc.kind == "refused" else "unavailable"
        raise BridgeError(code, f"{exc.message} Nothing was sent.") from None
    except TimeoutError:
        raise limits.too_slow(timeout_s) from None
    except (httpx.HTTPError, httpx.InvalidURL):
        raise BridgeError("unavailable", f"{parsed.host} could not be reached, or the "
                                         "connection broke part-way. Try again "
                                         "later.") from None
    except OSError:
        if upload is None:
            raise
        raise BridgeError("unavailable", "The file changed or could not be read while it "
                                         "was being sent, so the request was stopped. Try "
                                         "again once it is written.") from None
    finally:
        fetch_ends(core, ctx.id)
        if upload is not None:
            upload.close()
    await run_in_threadpool(_fetched, core, ctx, parsed.host, len(body), checked.address)
    if upload is not None:
        await run_in_threadpool(file_body.sent, core, ctx.id, ctx.actor, parsed.host,
                                method, upload)
    shown = {name: response.headers[name] for name in ("content-type", "location",
                                                       "retry-after", "etag")
             if name in response.headers}
    # A redirect is handed back, never followed: the next address is the app's to ask for,
    # and it is checked like any other.
    out: dict[str, Any] = {"status": response.status_code, "headers": shown}
    # Read from the buffer itself, with no copy of it first: an answer may be large.
    if want == "base64":
        out["base64"] = base64.b64encode(body).decode("ascii")
    else:
        out["text"] = body.decode("utf-8", errors="replace")
    del body
    return out


def fetch_begins(core: Core, app_id: str) -> bool:
    """Rule ``a_few_fetches_at_once``: one more fetch for this app is under way, unless it
    has as many open as the owner allows (``apps.net_fetch_at_once``)."""
    most = int(core.settings.get(FETCH_AT_ONCE))
    key = (id(core), app_id)
    with _FETCHING_LOCK:
        if _FETCHING.get(key, 0) >= most:
            raise BridgeError("quota", (
                f"Your app already has {most} network request{'' if most == 1 else 's'} "
                f"under way, the most the owner allows at once (\"{FETCH_AT_ONCE_LABEL}\", "
                f"{FETCH_AT_ONCE}), so nothing was sent. Wait for one to finish, then try "
                "again."), retry_after_s=1)
        _FETCHING[key] = _FETCHING.get(key, 0) + 1
    return True


def fetch_ends(core: Core, app_id: str) -> None:
    key = (id(core), app_id)
    with _FETCHING_LOCK:
        left = _FETCHING.get(key, 0) - 1
        if left > 0:
            _FETCHING[key] = left
        else:
            _FETCHING.pop(key, None)


def _fetched(core: Core, ctx: Ctx, host: str, size: int, address: str) -> None:
    """The receipt for one fetch: where (the name, and the address connected to) and how
    much, never what."""
    from jimmyhq.apps import kinds  # noqa: PLC0415

    core.tape.print("apps.fetched", {"app_id": ctx.id, "host": host, "size": size,
                                     "address": address},
                    actor=ctx.actor, subject=kinds.subject(ctx.id))


_APP_CALL_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}\Z")


def check_app_call_name(ctx: Ctx, name: Any, offered: list[str] | None) -> str:
    """The page may ask its program only for a call the program said it offers at start
    (``@app.call`` in the SDK)."""
    if not isinstance(name, str) or not _APP_CALL_NAME.match(name):
        raise BridgeError("invalid", "\"name\" for app.call is a short lower-case name such "
                                     "as \"board\". Nothing was sent.")
    if offered is not None and name not in offered:
        listed = ", ".join(offered) or "none"
        raise BridgeError("not_found", f"Your app's program offers no call \"{name}\". "
                                       f"Nothing was sent. It offers: {listed}.")
    return name


async def app_call(ctx: Ctx, args: dict[str, Any]) -> Any:
    """Ask the app's own program, through its inbox, and wait for its answer."""
    from jimmyhq.apps import inbox  # noqa: PLC0415 - it imports this module

    only("app.call", args, ("name", "args"), ("name",))
    core = ctx.core
    plugs = await run_in_threadpool(inbox.plugs, core, ctx.id)
    name = check_app_call_name(ctx, args["name"], plugs.get("calls") if plugs else None)
    if not await run_in_threadpool(inbox.responding, core, ctx.id):
        raise BridgeError("unavailable", f"{ctx.app.manifest.name}'s program is not running "
                                         "or not responding, so nothing was sent. Try again "
                                         "in a moment; the Apps screen shows how it is.")
    timeout_s = core.settings.get("apps.call_timeout_s")
    delivery = await run_in_threadpool(
        inbox.deliver, core, ctx.id, "call", {"name": name, "args": args.get("args") or {}})
    reply = await inbox.await_answer(core, delivery, timeout_s)
    if reply is None:
        await run_in_threadpool(inbox.expire, core, delivery)
        raise BridgeError(
            "unavailable",
            f"{ctx.app.manifest.name}'s program did not answer within {timeout_s} seconds "
            "(the owner's limit, apps.call_timeout_s, at most 300). The call will not be "
            "carried out later. For work that takes longer, answer at once with a job id, "
            "do the work in a thread, and let the page ask again for the result.",
            owner_message=f"{ctx.app.manifest.name} took longer than {timeout_s} seconds to "
                          "answer, so nothing was done. Try again; if it keeps happening, "
                          "you can allow it longer under Settings, Apps.")
    if not reply.ok:
        raise BridgeError("internal", reply.error or "The app's program could not do that.",
                          owner_message=reply.owner_message)
    return reply.result


@dataclass(frozen=True)
class Call:
    name: str
    run: Callable[[Ctx, dict[str, Any]], Any]
    permission: str | None = None  # None: every app may, on its own data
    tiers: tuple[str, ...] = TIERS
    checks_own: bool = False  # the call checks its permission itself (it names a host)


CALLS: dict[str, Call] = {c.name: c for c in (
    Call("storage.get", storage_get),
    Call("storage.set", storage_set),
    Call("storage.delete", storage_delete),
    Call("storage.keys", storage_keys),
    *(Call(name, records_call(name)) for name in _RECORD_TAKES),
    Call("tasks.list", tasks_list, "tasks.read"),
    Call("tasks.create", tasks_create, "tasks.write"),
    Call("tasks.complete", tasks_complete, "tasks.write"),
    Call("bench.file", bench_file, "bench.file"),
    Call("settings.get", settings_get),
    Call("model.ask", model_ask, "model.ask"),
    Call("memory.recall", memory_recall, "memory.recall"),
    Call("net.fetch", net_fetch, "net", checks_own=True),
    Call("hosts.list", hosts_list),
    Call("ui.limits", ui_limits),
    Call("app.call", app_call, tiers=(FULL,)),
)}


# --- W1: similarity and the web. The work is in ``reach``; the gate below is the same. ---


def _reach(name: str) -> Callable[[Ctx, dict[str, Any]], Any]:
    async def run(ctx: Ctx, args: dict[str, Any]) -> Any:
        from jimmyhq.apps import reach  # noqa: PLC0415 - it imports this module

        return await getattr(reach, name)(ctx, args)

    run.__name__ = name
    return run


CALLS.update({c.name: c for c in (
    Call("model.embed", _reach("model_embed"), "model.ask"),
    Call("web.search", _reach("web_search"), "web"),
    Call("web.read", _reach("web_read"), "web"),
)})

# --- end of W1 ------------------------------------------------------------------------------


# --- the gate every call goes through -------------------------------------------------------


def check_app_id(app_id: Any) -> str:
    if not isinstance(app_id, str) or not _APP_ID.match(app_id):
        raise BridgeError("not_found", "There is no app with that id. Nothing was done.")
    return app_id


def check_call(call: Any) -> Call:
    """The call of that name, or a refusal that lists the ones there are."""
    if isinstance(call, str) and call in SHELL_CALLS:
        raise BridgeError("invalid", f"{call} is answered by the JimmyHQ screen itself and "
                                     "never reaches the server. Nothing was done. Call it "
                                     "through the bridge script the page was given.")
    found = CALLS.get(call) if isinstance(call, str) else None
    if found is None:
        shown = f"\"{call}\"" if isinstance(call, str) and _CALL.match(call) else "that"
        raise BridgeError("invalid", f"There is no bridge call named {shown}. Nothing was "
                                     f"done. The calls are: {', '.join(CALLS)}.")
    return found


def check_installed(core: Core, app_id: str) -> Installed:
    """The app, if it is installed and switched on."""
    app = find_app(core, app_id)
    if app is None or app.manifest.id != app_id:
        raise BridgeError("not_found", f"No app called \"{app_id}\" is installed. Nothing "
                                       "was done. Install it from the Apps screen first.")
    if not app.enabled:
        raise BridgeError("not_allowed", f"The app \"{app_id}\" is switched off, so "
                                         "nothing was done. The owner can switch it on "
                                         "from the Apps screen.")
    return app


def check_rates(core: Core, app_id: str) -> None:
    """Count this call against each of the app's call rates."""
    for name in ("calls_per_second", "calls_per_minute", "calls_per_day"):
        quotas.take(core, app_id, name)


def check_permission(app: Installed, call: Call) -> None:
    """Refuse a call the owner has not granted. Reads what was granted, not what the
    manifest asks for."""
    manifest = app.manifest
    if manifest.tier not in call.tiers:
        raise BridgeError("not_allowed", f"{call.name} is only for a "
                                         f"{' or '.join(call.tiers)} app, and "
                                         f"\"{manifest.id}\" is {manifest.tier}. Nothing "
                                         "was done.")
    if call.permission is None or call.checks_own:
        return
    if not allows(app.granted, call.permission):
        raise BridgeError(
            "not_allowed",
            f"Your app \"{manifest.id}\" has not been granted \"{call.permission}\", so "
            f"{call.name} did nothing. Add \"{call.permission}\" to \"permissions\" in "
            "jimmyhq.app.json; the owner is asked to agree when they install or update it.")


def admit(core: Core, app_id: Any, call_name: Any) -> tuple[Installed, Call]:
    """Every check that comes before a call is carried out, in order."""
    app_id = check_app_id(app_id)
    call = check_call(call_name)
    app = check_installed(core, app_id)
    check_rates(core, app_id)
    check_permission(app, call)
    # --- roles: a record tool reaches nothing outside the app (record_effect.py) ---
    from jimmyhq.apps import record_effect  # noqa: PLC0415

    record_effect.check_outside_call(core, app_id, call.name)
    return app, call


def refusal(exc: Exception) -> BridgeError:
    """Any failure as the one error shape an app is given."""
    from jimmyhq.bench.rules import BenchError  # noqa: PLC0415
    from jimmyhq.tasks import TaskError  # noqa: PLC0415

    if isinstance(exc, BridgeError):
        return exc
    if isinstance(exc, QuotaExceeded):
        return BridgeError("quota", exc.message, exc.retry_after_s)
    if isinstance(exc, AppDataError | described.RecordError):
        return BridgeError(exc.code, exc.message)
    if isinstance(exc, TaskError | BenchError):
        code = {403: "not_allowed", 404: "not_found", 413: "too_large"}.get(exc.status,
                                                                            "invalid")
        detail = exc.detail if exc.detail.endswith((".", "?", "!")) else exc.detail + "."
        return BridgeError(code, f"{detail} Nothing was changed.")
    # What went wrong may hold what the app sent, so only its type is logged.
    log.error("bridge call failed: %s", type(exc).__name__)
    return BridgeError("internal", "JimmyHQ could not carry that out because of a fault of "
                                   "its own. Nothing is known to have changed. Try again; "
                                   "if it keeps happening, tell the owner.")


async def dispatch(core: Core, app_id: Any, call_name: Any, args: Any) -> Any:
    """Carry out one call for one app and return its result, or raise ``BridgeError``."""
    try:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise BridgeError("invalid", "A call's arguments must be an object, such as "
                                         "{\"key\": \"name\"}. Nothing was done.")
        app, call = await run_in_threadpool(admit, core, app_id, call_name)
        ctx = Ctx(core, app)
        if inspect.iscoroutinefunction(call.run):
            return await call.run(ctx, args)
        return await run_in_threadpool(call.run, ctx, args)
    except Exception as exc:  # noqa: BLE001 - every failure becomes one error shape
        raise refusal(exc) from None


# --- the messages ---------------------------------------------------------------------------


def ok(message_id: Any, result: Any) -> dict[str, Any]:
    return {"id": message_id, "ok": True, "result": result}


def failed(message_id: Any, error: BridgeError) -> dict[str, Any]:
    body: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.retry_after_s is not None:
        body["retry_after_s"] = error.retry_after_s
    if error.owner_message:
        body["owner_message"] = error.owner_message
    return {"id": message_id, "ok": False, "error": body}


def read_message(message: Any, call_name: str) -> tuple[Any, dict[str, Any]]:
    """The id and arguments of a relayed request, or a refusal. The id is only ever
    echoed; the call is the one in the address."""
    if not isinstance(message, dict) or set(message) - {"jimmyhq", "id", "call", "args"}:
        raise BridgeError("invalid", "A bridge request is an object with \"id\", \"call\" "
                                     "and \"args\" only. Nothing was done.")
    version = message.get("jimmyhq", BRIDGE_VERSION)
    if version != BRIDGE_VERSION or isinstance(version, bool):
        raise BridgeError("invalid", f"This JimmyHQ speaks version {BRIDGE_VERSION} of the "
                                     "bridge (app API "
                                     f"{APP_API[0]}.{APP_API[1]}). Nothing was done. Use "
                                     "the bridge script the page was given.")
    if message.get("call", call_name) != call_name:
        raise BridgeError("invalid", "The request names one call and its address another. "
                                     "Nothing was done.")
    args = message.get("args")
    return _message_id(message.get("id")), ({} if args is None else args)


def _message_id(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    if isinstance(value, str) and len(value) > 100:
        return None
    return value


def parse_body(raw: bytes) -> Any:
    def refuse(_: str) -> Any:
        raise ValueError

    try:
        text = raw.decode("utf-8")
        if too_deep(text):
            raise ValueError
        return json.loads(text, parse_constant=refuse)
    except (ValueError, RecursionError):
        raise BridgeError("invalid", "The request is not JSON that can be read (it may be "
                                     "nested too deep, or hold a number that is not "
                                     "finite). Nothing was done.") from None


def body_limit(core: Core, app_id: str) -> int:
    """The most bytes one relayed request may weigh."""
    which = app_id if _APP_ID.match(app_id) else "none"
    return quotas.limit(core, which, "value_kb") * 1024 + _ENVELOPE_ROOM


def from_own_pages(request: Request) -> bool:
    """False when the browser says the request did not come from JimmyHQ's own pages: a
    frame, another site, or a page with no origin at all."""
    site = request.headers.get("sec-fetch-site")
    if site is not None and site.lower() not in ("same-origin", "none"):
        return False
    if request.headers.get("sec-fetch-dest", "").lower() in ("iframe", "frame", "document",
                                                             "embed", "object"):
        return False
    return request.headers.get("origin", "").lower() != "null"


async def handle(core: Core, app_id: str, call_name: str, raw: bytes) -> dict[str, Any]:
    """One relayed request, as bytes, to the response the shell posts back."""
    message_id = None
    try:
        message = parse_body(raw)
        if isinstance(message, dict):
            message_id = _message_id(message.get("id"))
        message_id, args = read_message(message, call_name)
        return ok(message_id, await dispatch(core, app_id, call_name, args))
    except BridgeError as error:
        return failed(message_id, error)


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/apps", tags=["apps"])

    @router.post("/{app_id}/bridge/{call}")
    async def bridge(app_id: str, call: str, request: Request,
                     _: Principal = Depends(owner)):  # noqa: B008
        if not from_own_pages(request):
            return JSONResponse({"detail": "Only JimmyHQ's own screen can relay an app's "
                                           "calls. Nothing was done."}, 403)
        most = await run_in_threadpool(body_limit, core, app_id)
        too_large = failed(None, BridgeError(
            "too_large", f"One request can be at most {most // 1024} KB. Nothing was done. "
                         "Send less in one call."))
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > most:
            return too_large
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > most:
                return too_large
            chunks.append(chunk)
        return await handle(core, app_id, call, b"".join(chunks))

    return router
