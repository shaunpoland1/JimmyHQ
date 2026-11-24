"""A fake JimmyHQ for an app author's tests: no server, no network, no real record.

    from jimmyhq_app.testing import FakeCore

    def test_a_draft_is_put_on_the_bench(tmp_path):
        core = FakeCore("drafts", data_dir=tmp_path, permissions=["bench.file", "model.ask",
                        "jobs"], settings={"draft_minutes": 60})
        core.replies.append("A short post.")
        with core.installed():
            import drafts.main                  # its App() talks to the fake
        core.run_job("draft")
        [card] = core.cards.values()
        assert card["kind"] == "app.drafts.draft" and card["body"] == "A short post."
        core.decide(card["card_id"], "approve")

It answers every call the real app API answers, holds the app to the permissions it is
given (all of them when ``permissions`` is None) and to the kinds it declared, and keeps
what the app did in plain attributes: ``events``, ``cards`` (and ``filed``, the
arguments of each ``bench.file`` as the app sent them), ``tasks``, ``storage``,
``asked`` (model prompts), ``embedded`` (texts), ``searched`` and ``read`` (the web),
``fetched``, ``sent_files`` (``{url, ref, data}`` for each file a fetch sent), ``vault``,
``kinds``, ``plugs``, ``counts`` (every number given to ``ui.count``, and ``core.count``
for the last). ``core.upload(data, name)`` plays the owner giving the app a
picture: it is kept in ``data_dir/uploads`` and its ``{ref, name, size, type}`` returned,
as ``hq.files.upload`` would give the page. It drives the app the way JimmyHQ does,
through deliveries: ``run_job``, ``decide``, ``call``, ``tool``, ``collect``,
``change_setting``, ``change_calendar``, each returning what the app's handler returned,
or raising ``CallError`` with the app's error sentence.

**Messages from devices.** ``core.inbound("event", {"state": "on"}, key_name="Home hub")``
plays a device sending the app a message, as JimmyHQ hands it over once its key, the app's
``inbound`` permission and the body (a JSON object) pass. ``query={"uid": "..."}`` plays
the parameters a sender adds to the address. A JSON list passes only for a name made with
``FakeCore(inbound_arrays=["event"])``, as a manifest entry with ``"array": true``. It
returns None, as the device is only ever told the message was taken; a handler that raises
raises ``CallError`` here, which JimmyHQ would count on the app's page. Each message handed
over is kept in ``core.inbound_sent``.

What it answers is set in plain attributes too: ``replies`` (model answers),
``recalls``, ``responses`` (net.fetch, by address), ``search_results`` (web.search, by
query, or ``"*"`` for any), ``pages`` (web.read, by address: the cleaned text),
``embedder`` (a function from a text to its vector), ``web_on`` (the owner's web lookup
switch), ``vision_on`` (whether a model that sees pictures is set up), ``local_model``
(whether one runs on the owner's own machines, for ``stay_local``), and
``timezone``/``week_start``. Pictures named in ``model.ask`` must be files inside
``data_dir``, as with the real core.

**Roles.** ``roles={"writer": "Wren", "checker": None}`` says who holds each role, by
label (None: empty). ``work.give`` then lands in ``core.work``, or is refused for an empty
role as JimmyHQ refuses it; ``core.end_work(id, "done", note=...)`` hands the app the
``work`` delivery; ``core.tool(name, args, role="writer")`` says which role called. While
a tool registered with ``@app.record_tool`` is handled, every call that reaches outside
the app is refused, as JimmyHQ refuses it.

**Limits.** ``app.limits()`` answers JimmyHQ's defaults; ``FakeCore(limits={"call_max_kb": 64})``
or ``core.limits["save_max_mb"] = 2`` plays an owner who set them differently.

**Hosts of the owner's own.** ``FakeCore(owner_hosts={"purpose": ..., "max": 3})`` plays
a manifest that declares ``owner_hosts``; ``core.grant_host(host, secrets={name: value})``
plays the owner adding one on the app's page, with the secrets typed in beside it, and
``core.remove_host(host)`` taking it away. ``net.fetch`` then reaches it as a granted host,
``app.hosts.list()`` lists it, and ``app.secrets.get(name, host=host)`` reads its secrets.
An answer in ``responses`` may be a function of the fetch's arguments, or a ``CallError``
to raise.

**Feeds.** Another app's feed is played by the fake: ``core.offer("reader.articles",
{...})`` prints one item (and makes that app installed and offering that feed),
``core.erase_item("reader.articles", seq)`` erases one, and ``core.switch_off("reader")``,
``core.remove_app("reader")`` and ``core.stop_offering("reader.articles")`` give the
sentences JimmyHQ gives. ``app.feeds.read`` is held to ``feed:<app>.<feed>`` like any
other permission.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import itertools
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jimmyhq_app import app as app_module
from jimmyhq_app._types import CallError, Limits

# One article every app's tests can read, with what makes splitting sentences hard: a
# heading, Markdown marks, an abbreviation, an initial, a decimal, curly quotes, a
# no-break space, a list, and Windows line endings. Shared so that two apps that read it
# can be shown to number its sentences the same way (``jimmyhq_app.text``).
SAMPLE_ARTICLE = (
    "# Beds that hold water through a dry summer\r\n\r\n"
    "A three-year trial led by Dr. Lee found that **mulched** beds held 41% more "
    "water in August. The beds were 3.5 metres long, e.g. about the size of a garden "
    "plot. A. N. Other, who ran one farm, said: \u201cWe watered half as often.\u201d "
    "Yields were the same!\r\n\r\n"
    "> The chips should be at least 8\u00a0centimetres deep.\r\n\r\n"
    "- Wood chips settle in a season.\r\n"
    "- See [the full report](https://news.example.com/report) for the figures.\r\n\r\n"
    "Is it worth it? Most growers in the trial said yes. A report is due in Jan. 2028."
)
SAMPLE_ARTICLE_SENTENCES = 11  # how many sentences jimmyhq_app.text finds in it

# The cases every app that checks a draft against its sources runs through
# ``jimmyhq_app.claims``, so two apps' checks are shown to agree: (written in the draft,
# said in the source, tolerance in percent, whether they match).
NUMBER_CASES = (
    ("3.4", "3.42", 1.0, True), ("40%", "39.6%", 1.0, True),
    ("1.2 million", "1,234,567", 1.0, True), ("8,000", "7,950", 1.0, True),
    ("45%", "38%", 1.0, False), ("8,000", "7,900", 1.0, False),
    ("8,000", "7,900", 2.0, True), ("41%", "41 percent", 0.0, True),
    ("2.5 billion", "2,500,000,000", 0.0, True), ("12", "120", 1.0, False),
    ("0", "0", 0.0, True),
)
# (a sentence of the source, a quoted span in a draft, whether it is word for word).
QUOTE_CASES = (
    ("A. N. Other said: \u201cWe watered half as often.\u201d", "we watered half as often", True),
    ("\"We have never seen demand like this,\" said the officer.",
     "We have  never seen demand like this.", True),
    ("It\u2019s the best year we\u2019ve had.", "It's the best year we've had", True),
    ("We watered half as often.", "We watered less often", False),
    ("We watered half as often.", "watered half as often, every week", False),
)

_ULID_CHARS = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_OUTSIDE = frozenset({"net.fetch", "model.ask", "model.embed", "web.search", "web.read",
                      "tasks.create", "tasks.complete", "bench.file", "bench.update",
                      "bench.withdraw", "work.give", "work.withdraw", "vault.put"})
_NEEDS = {"roles.get": "agents.tools", "work.give": "agents.tools",
          "work.withdraw": "agents.tools", "tasks.list": "tasks.read",
          "tasks.create": "tasks.write",
          "tasks.complete": "tasks.write", "bench.file": "bench.file",
          "bench.update": "bench.file", "bench.withdraw": "bench.file",
          "model.ask": "model.ask", "model.embed": "model.ask",
          "memory.recall": "memory.recall", "web.search": "web", "web.read": "web",
          "tape.read": None}
_FEED_ITEMS_MOST = 100  # as apps.quota.feed_items_per_read
_SOME_MS = 1_800_000_000_000  # when the fake says a message came, unless told


_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif",
                "webp": "image/webp", "avif": "image/avif", "heic": "image/heic"}


# What JimmyHQ lets a private app's card carry besides its title (see "A private app's card" in
# docs/apps.md). A test in the JimmyHQ repo runs one set of cases against this fake and against
# the real bridge, so the two cannot drift apart.
_PRIVATE_CARD_TAKES = ("kind", "title", "lane", "actions", "subject")
_PRIVATE_SUBJECT = re.compile(r"([a-z]{1,12})(:[0-9]{1,9})?")
_BENCH_ACTION_DEFAULTS = {"style": "default", "needs_reason": False, "closes": False,
                          "moves_to": None}
_BENCH_ACTIONS = {a["id"]: {**_BENCH_ACTION_DEFAULTS, **a} for a in (
    {"id": "approve", "label": "Approve", "style": "primary", "closes": True},
    {"id": "not_yet", "label": "Not yet", "style": "default", "moves_to": "waiting"},
    {"id": "no", "label": "No", "style": "danger", "needs_reason": True, "closes": True},
    {"id": "ok", "label": "Got it", "style": "primary", "closes": True},
    {"id": "undo", "label": "Undo", "style": "default", "closes": True})}


def _is_bench_action(action: Any) -> bool:
    return (isinstance(action, dict) and isinstance(action.get("id"), str)
            and _BENCH_ACTIONS.get(action["id"]) == {**_BENCH_ACTION_DEFAULTS, **action})


def _sniff(data: bytes) -> str | None:
    """What kind of picture these bytes are, read from the bytes, as JimmyHQ does."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "avif"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1"):
        return "heic"
    return None


def _id(n: int) -> str:
    out = ""
    for _ in range(26):
        out = _ULID_CHARS[n % 32] + out
        n //= 32
    return out


class FakeCore:
    fake = True  # App.run() only registers; the test drives the deliveries

    def __init__(self, app_id: str = "test", *, data_dir: str | Path | None = None,
                 permissions: list[str] | None = None, settings: dict[str, Any] | None = None,
                 secrets: dict[str, str] | None = None, private: bool = False,
                 cards: list[str] | None = None, card_subjects: list[str] | None = None,
                 timezone: str = "",
                 week_start: str = "monday", page_chars: int = 12_000,
                 roles: dict[str, str | None] | None = None,
                 owner_hosts: dict[str, Any] | None = None,
                 inbound_arrays: list[str] | None = None,
                 limits: dict[str, Any] | None = None):
        self.app_id, self.data_dir = app_id, data_dir
        # The owner's limits ``app.limits()`` reads: JimmyHQ's defaults, changed by ``limits=``
        # or by setting a key here, such as ``core.limits["call_max_kb"] = 64``.
        self.limits: dict[str, Any] = {**asdict(Limits()), **(limits or {})}
        # The inbound names whose manifest entry says "array": true.
        self.inbound_arrays = set(inbound_arrays or ())
        self.permissions = None if permissions is None else list(permissions)
        self.settings = dict(settings or {})
        self.secrets = dict(secrets or {})
        # The manifest's "owner_hosts" ({purpose, max}), or None when it declares none;
        # what the owner gave it on its page, and the secrets typed in beside each.
        self.owner_hosts_asked = dict(owner_hosts) if owner_hosts is not None else None
        self.owner_hosts: list[str] = []
        self.host_secrets: dict[tuple[str, str], str] = {}  # (host, name) -> value
        self.private = private
        self.card_kinds = cards  # None: any kind may be filed
        # The manifest's "card_subjects": the only words a private app's card may carry as its
        # subject. Empty, a private app's card may carry none.
        self.card_subjects = tuple(card_subjects or ())
        self.roles: dict[str, str | None] = dict(roles or {})  # role -> label, or None
        self.work: dict[str, dict[str, Any]] = {}  # what work.give handed out
        self._in_record: str | None = None
        self.replies: list[str | Callable[[dict[str, Any]], Any]] = []
        self.recalls: list[str] = []
        self.responses: dict[str, dict[str, Any]] = {}  # url -> what net.fetch returns
        self.ladder: dict[tuple[str, str], str] = {}  # (tool, action) -> level
        self.events: list[dict[str, Any]] = []
        self.cards: dict[str, dict[str, Any]] = {}
        self.filed: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.storage: dict[str, Any] = {}
        self.asked: list[dict[str, Any]] = []
        self.fetched: list[dict[str, Any]] = []
        self.sent_files: list[dict[str, Any]] = []  # each file a fetch sent: {url, ref, data}
        self.vault: dict[str, bytes] = {}
        self.emitted: list[dict[str, Any]] = []  # what app.emit sent its pages
        self.counts: list[int] = []  # every number ui.count was given, oldest first
        self.kinds: dict[tuple[str, int], dict[str, Any]] = {}
        self.plugs: dict[str, Any] = {}
        self.app: Any = None
        self.timezone, self.week_start = timezone, week_start
        self.page_chars = page_chars
        self.web_on = True
        self.vision_on = True
        # Whether the owner has a model on their own machines for the role asked. Off, a
        # call with stay_local (or any call of a private app) is refused, as JimmyHQ does.
        self.local_model = True
        self.search_results: dict[str, list[dict[str, Any]]] = {}
        self.pages: dict[str, str] = {}
        self.embedder: Callable[[str], list[float]] = _letters
        self.embedded: list[list[str]] = []
        self.searched: list[str] = []
        self.read: list[str] = []
        self._ids = itertools.count(1)
        # Other apps' feeds: "app.feed" -> items, and each producer's state.
        self.feeds: dict[str, list[dict[str, Any]]] = {}
        self.producers: dict[str, str] = {}  # app id -> "on", "off" or "removed"
        self.feed_reads: list[dict[str, Any]] = []
        self.inbound_sent: list[dict[str, Any]] = []  # each message core.inbound handed over
        self._feed_seq = itertools.count(1)

    # --- wiring ------------------------------------------------------------------------------

    def attach(self, app: Any) -> None:
        self.app = app

    @contextlib.contextmanager
    def installed(self) -> Iterator[FakeCore]:
        """Every ``App()`` made inside talks to this fake."""
        app_module.use(self)
        try:
            yield self
        finally:
            app_module.use(None)

    # --- the transport the App uses -------------------------------------------------------

    def request(self, method: str, path: str, *, body: Any = None,
                params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        body = json.loads(json.dumps(body)) if body is not None else None  # as on the wire
        if method == "GET" and path == "/hello":
            return {"app": self.app_id, "permissions": self.permissions or [],
                    "private": self.private, "settings": dict(self.settings),
                    "timezone": self.timezone, "week_start": self.week_start}
        if method == "PUT" and path == "/kinds":
            return {"kinds": [self._declare(k) for k in body["kinds"]]}
        if method == "PUT" and path == "/plugs":
            self.plugs = body
            return body
        if method == "GET" and path == "/inbox":
            return {"deliveries": []}
        if method == "POST" and path.startswith("/call/"):
            return self._call(path.removeprefix("/call/"), body.get("args") or {})
        raise CallError("not_found", f"The fake core has no {method} {path}.")

    # --- driving the app, as JimmyHQ does ---------------------------------------------------

    def deliver(self, kind: str, data: dict[str, Any]) -> Any:
        if self.app is None:
            raise RuntimeError("No App() talks to this fake yet: make it inside "
                               "core.installed(), or pass transport=core.")
        answer = self.app.handle({"id": _id(next(self._ids)), "type": kind,
                                  "data": json.loads(json.dumps(data))})
        json.dumps(answer)  # what an app answers must go over the wire
        if not answer["ok"]:
            raise CallError("internal", answer["error"],
                            owner_message=answer.get("owner_message"))
        return answer["result"]

    def run_job(self, name: str) -> Any:
        return self.deliver("job", {"name": name})

    def decide(self, card_id: str, action: str = "approve", *, note: str | None = None,
               reason: str | None = None) -> Any:
        card = self.cards[card_id]
        card["decided"] = action
        return self.deliver("decision", {
            "card_id": card_id, "card_kind": card["kind"], "action": action, "note": note,
            "reason": reason, "subject": card.get("subject"), "filed_by": f"app:{self.app_id}",
            "event_id": _id(next(self._ids)), "ts": 0})

    def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """As the app's page calling ``hq.app.call(name, args)``."""
        return self.deliver("call", {"name": name, "args": args or {}})

    def tool(self, name: str, args: dict[str, Any] | None = None,
             agent: str = "agent:test", role: str | None = None) -> Any:
        record = name in getattr(self.app, "record_tools", ())
        self._in_record = name if record else None
        try:
            return self.deliver("tool", {"name": name, "args": args or {}, "agent": agent,
                                         "role": role, "effect": "record" if record
                                         else "read"})
        finally:
            self._in_record = None

    def inbound(self, name: str, body: dict[str, Any] | list[Any], *,
                key_name: str = "Test key", received_ms: int | None = None,
                query: dict[str, str] | None = None) -> None:
        """As a device sending ``POST /api/in/<app>/<name>?<query>`` with a key the owner
        made."""
        if not self._granted("inbound"):
            raise CallError("not_allowed", "Your app has not been granted \"inbound\", so "
                                           "JimmyHQ refuses every message sent to it.")
        if isinstance(body, list) and name not in self.inbound_arrays:
            raise CallError("invalid", "A JSON list is taken only for a name whose manifest "
                                       "entry says \"array\": true, so JimmyHQ would refuse "
                                       "this one before your app saw it. Make the fake with "
                                       f"inbound_arrays=[\"{name}\"] to play that.")
        if not isinstance(body, dict | list):
            raise CallError("invalid", "A device's message is one JSON object, so JimmyHQ "
                                       "would refuse this one before your app saw it.")
        if query is not None and not (isinstance(query, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in query.items())):
            raise CallError("invalid", "The parameters after \"?\" are names and values of "
                                       "text, such as {\"uid\": \"abc\"}.")
        data: dict[str, Any] = {
            "name": name, "body": body, "key_name": key_name,
            "received_ms": (_SOME_MS + next(self._ids)) if received_ms is None
            else received_ms}
        if query:  # said only when the sender added some, as JimmyHQ does
            data["query"] = dict(query)
        self.inbound_sent.append(json.loads(json.dumps(data)))
        self.deliver("inbound", data)
        return None  # whatever the handler returned stays in the app

    def end_work(self, work_id: str, state: str = "done", *, note: str | None = None) -> Any:
        """As JimmyHQ when a piece of work the app gave ends or changes hands."""
        given = self.work[work_id]
        given["state"] = state
        return self.deliver("work", {"id": work_id, "role": given["role"],
                                     "subject": given.get("subject"), "state": state,
                                     "note": note,
                                     "agent_label": self.roles.get(given["role"])})

    def collect(self, source: str, cursor: str | None = None, limit: int = 50) -> Any:
        return self.deliver("collect", {"source": source, "cursor": cursor, "limit": limit})

    def change_setting(self, key: str, value: Any) -> Any:
        """As the owner changing one of the app's own settings."""
        self.settings[key] = value
        return self.deliver("settings", {"key": key})

    def change_calendar(self, *, timezone: str | None = None,
                        week_start: str | None = None) -> None:
        """As the owner changing their time zone or week start: one delivery for each."""
        if timezone is not None:
            self.timezone = timezone
            self.deliver("settings", {"key": "owner.timezone"})
        if week_start is not None:
            self.week_start = week_start
            self.deliver("settings", {"key": "owner.week_start"})

    # --- the calls ---------------------------------------------------------------------------

    def _granted(self, permission: str) -> bool:
        return self.permissions is None or permission in self.permissions

    def _need(self, call: str) -> None:
        permission = _NEEDS.get(call)
        if permission and not self._granted(permission):
            raise CallError("not_allowed", f"Your app has not been granted \"{permission}\", "
                                           f"so {call} did nothing.")

    def _call(self, name: str, args: dict[str, Any]) -> Any:
        self._need(name)
        if self._in_record and name in _OUTSIDE:
            raise CallError("not_allowed", f"Your app is handling \"{self._in_record}\", a "
                                           "tool declared with the effect \"record\", so "
                                           f"{name} did nothing. Answer the tool first.")
        handler = getattr(self, "_" + name.replace(".", "_"), None)
        if handler is None:
            raise CallError("invalid", f"There is no app API call named \"{name}\".")
        return handler(args)

    def _declare(self, item: dict[str, Any]) -> dict[str, Any]:
        key = (f"app.{self.app_id}.{item['name']}", item.get("v", 1))
        if key in self.kinds and self.kinds[key] != item:
            raise CallError("invalid", f"{key[0]} v{key[1]} was declared before with a "
                                       "different schema or example, and a declared version "
                                       "never changes.")
        new = key not in self.kinds
        self.kinds[key] = item
        return {"name": key[0], "v": key[1], "new": new}

    def _settings_get(self, args: dict[str, Any]) -> Any:
        if "key" not in args:
            return dict(self.settings)
        if args["key"] not in self.settings:
            raise CallError("not_found", f"Your app declares no setting {args['key']!r}.")
        return self.settings[args["key"]]

    def grant_host(self, host: str, secrets: dict[str, str] | None = None) -> None:
        """As the owner adding one of his own hosts on the app's page, with the secrets he
        typed in beside it."""
        if self.owner_hosts_asked is None:
            raise RuntimeError("Make the fake with owner_hosts={...}, as the manifest "
                               "declares it, before granting a host.")
        if host not in self.owner_hosts:
            self.owner_hosts.append(host)
        for name, value in (secrets or {}).items():
            self.host_secrets[(host, name)] = value

    def remove_host(self, host: str) -> None:
        """As the owner removing a host: its secrets go with it."""
        self.owner_hosts = [h for h in self.owner_hosts if h != host]
        self.host_secrets = {k: v for k, v in self.host_secrets.items() if k[0] != host}

    def _secrets_get(self, args: dict[str, Any]) -> str | None:
        host = args.get("host")
        if host is None:
            return self.secrets.get(args["name"])
        if host not in self.owner_hosts:
            raise CallError("not_found", "The owner has not given your app that host, so it "
                                         "has no secret there. Nothing was read.")
        return self.host_secrets.get((host, args["name"]))

    def _ui_limits(self, args: dict[str, Any]) -> dict[str, Any]:
        if args:
            raise CallError("invalid", "ui.limits takes no arguments. Nothing was done.")
        return dict(self.limits)

    def _hosts_list(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.owner_hosts_asked is None:
            raise CallError("not_allowed", "Your app does not declare \"owner_hosts\", so "
                                           "there are no hosts of the owner's to list.")
        return {"hosts": list(self.owner_hosts), **self.owner_hosts_asked}

    def _storage_get(self, args: dict[str, Any]) -> Any:
        return copy.deepcopy(self.storage.get(args["key"]))

    def _storage_set(self, args: dict[str, Any]) -> None:
        if args.get("value") is None:
            self.storage.pop(args["key"], None)
        else:
            self.storage[args["key"]] = args["value"]

    def _storage_delete(self, args: dict[str, Any]) -> bool:
        return self.storage.pop(args["key"], None) is not None

    def _storage_keys(self, args: dict[str, Any]) -> list[str]:
        return sorted(k for k in self.storage if k.startswith(args.get("prefix", "")))

    def _tape_print(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.private:
            raise CallError("not_allowed", "Your app is marked private, so it cannot write "
                                           "on the owner's record.")
        name = f"app.{self.app_id}.{args['kind']}"
        versions = [v for (n, v) in self.kinds if n == name]
        if not versions:
            raise CallError("not_found", f"Your app has not declared the kind \"{name}\", so "
                                         "nothing was written.")
        event = {"seq": len(self.events) + 1, "id": _id(next(self._ids)), "ts": 0,
                 "kind": name, "v": max(versions), "actor": f"app:{self.app_id}",
                 "cause": args.get("cause"), "root": None,
                 "subject": f"app.{self.app_id}:{args['subject']}" if args.get("subject")
                 else None, "payload": args["payload"], "readable": True}
        self.events.append(event)
        return {"id": event["id"], "seq": event["seq"], "kind": name, "v": event["v"]}

    def _tape_read(self, args: dict[str, Any]) -> dict[str, Any]:
        after, limit, kinds = args.get("after", 0), args.get("limit", 100), args.get("kinds")
        whole = self._granted("tape.read") and self.permissions is not None
        found = [e for e in self.events if e["seq"] > after
                 and (whole or e["actor"] == f"app:{self.app_id}")
                 and (not kinds or e["kind"] in kinds)][:limit]
        return {"events": copy.deepcopy(found),
                "next": found[-1]["seq"] if found else max(after, len(self.events))}

    def _check_private_card(self, args: dict[str, Any]) -> None:
        """A private app's card carries a title, and parts that hold none of its words."""
        extra = [name for name in args if name not in _PRIVATE_CARD_TAKES]
        if extra:
            raise CallError("invalid", "Your app is marked private, so a card it files "
                                       "carries a title only, with no text of its own "
                                       f"besides it. Nothing was filed. Leave out "
                                       f"{', '.join(extra)}.")
        actions = args.get("actions")
        if actions is not None and not (isinstance(actions, list)
                                        and all(_is_bench_action(a) for a in actions)):
            raise CallError("invalid", "Your app is marked private, so a card it files may "
                                       "offer only the Bench's own buttons, written exactly "
                                       "as the Bench writes them. Nothing was filed.")
        subject = args.get("subject")
        if subject is not None:
            found = _PRIVATE_SUBJECT.fullmatch(subject) if isinstance(subject, str) else None
            if found is None or found.group(1) not in self.card_subjects:
                raise CallError("invalid", "Your app is marked private, so a card's "
                                           "\"subject\" may be only one of the words your "
                                           "manifest lists under \"card_subjects\", with a "
                                           "number after it if you like, such as "
                                           "\"reminder:12\". Nothing was filed.")

    def _bench_file(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.private:
            self._check_private_card(args)
        self.filed.append(copy.deepcopy(args))
        kind = args.get("kind") or "card"
        if self.card_kinds is not None and kind not in self.card_kinds:
            raise CallError("invalid", f"\"kind\" for bench.file must be one of: "
                                       f"{', '.join(self.card_kinds)}.")
        card_id = _id(next(self._ids))
        self.cards[card_id] = {**args, "card_id": card_id, "kind": f"app.{self.app_id}.{kind}",
                               "subject": f"app.{self.app_id}:{args['subject']}"
                               if args.get("subject") else None,
                               "filed_by": f"app:{self.app_id}", "decided": None}
        return {"card_id": card_id}

    def _bench_update(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.private and set(args) - {"card_id", "title"}:
            raise CallError("invalid", "Your app is marked private, so its cards carry a "
                                       "title only. Nothing was changed. Send only "
                                       "\"card_id\" and \"title\".")
        card = self._own_card(args["card_id"])
        card.update({k: v for k, v in args.items() if k != "card_id"})
        return {"card_id": card["card_id"]}

    def _bench_withdraw(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.private and args.get("note") is not None:
            raise CallError("invalid", "Your app is marked private, so it withdraws a card "
                                       "without a note. Nothing was changed. Leave out "
                                       "\"note\".")
        card = self._own_card(args["card_id"])
        card["withdrawn"] = True
        return {"card_id": card["card_id"]}

    def _own_card(self, card_id: str) -> dict[str, Any]:
        if card_id not in self.cards:
            raise CallError("not_found", "Your app filed no card with that id.")
        return self.cards[card_id]

    def _ladder_check(self, args: dict[str, Any]) -> str:
        return self.ladder.get((args["tool"], args["action"]), "approve")

    def _model_ask(self, args: dict[str, Any]) -> dict[str, Any]:
        for flag in ("stay_local", "off_record"):
            if args.get(flag) is not None and args[flag] is not True:
                raise CallError("invalid", f"\"{flag}\" for model.ask can only be true. "
                                           "Nothing was sent.")
        given = args.get("untrusted")
        if given is not None:  # as the real core: text, or 1 to 20 texts, never an empty list
            pieces = [given] if isinstance(given, str) else given
            if (not isinstance(pieces, list) or not 1 <= len(pieces) <= 20
                    or not all(isinstance(piece, str) for piece in pieces)):
                raise CallError("invalid", "\"untrusted\" for model.ask is text, or a list of "
                                           "at most 20 texts, from outside (a web page, an "
                                           "email). Nothing was sent.")
        if args.get("images"):
            self._check_images(args["images"], args.get("role"))
        local = bool(self.local_model)  # without one, the owner's hosted model answers
        if (args.get("stay_local") or self.private) and not self.local_model:
            raise CallError("unavailable", "This call asked to stay on the owner's own "
                                           "computer, and no model there is set up for this "
                                           "role. Nothing was sent anywhere.")
        self.asked.append(args)
        if not self.replies:
            raise CallError("unavailable", "The fake core has no model reply waiting: add "
                                           "one to core.replies.")
        reply = self.replies.pop(0)
        value = reply(args) if callable(reply) else reply
        if isinstance(value, str):
            return {"text": value, "data": None, "local": local, "model": "fake"}
        return {"text": json.dumps(value), "data": value, "local": local, "model": "fake"}

    def _memory_recall(self, args: dict[str, Any]) -> dict[str, Any]:
        text = self.recalls.pop(0) if self.recalls else ""
        return {"text": text, "passages": [{"text": text}] if text else []}

    def upload(self, data: bytes, name: str = "picture.jpg") -> dict[str, Any]:
        """As the owner choosing a picture on the app's page: kept in the data folder's
        ``uploads``, refused unless its bytes are a picture, and described as the page is
        told."""
        ending = _sniff(data)
        if ending is None:
            raise CallError("invalid", "That file is not a picture JimmyHQ can keep, so "
                                       "nothing was kept.")
        if "/" in name or "\\" in name:
            raise CallError("invalid", "A picture's name cannot hold a slash.")
        folder = self._data_folder() / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        ref = f"uploads/{hashlib.sha256(data + str(next(self._ids)).encode()).hexdigest()[:32]}" \
              f".{ending}"
        (self._data_folder() / ref).write_bytes(data)
        return {"ref": ref, "name": name, "size": len(data), "type": _IMAGE_TYPES[ending]}

    def _data_folder(self) -> Path:
        if self.data_dir is None:
            raise RuntimeError("Give the fake core a data_dir to keep files in.")
        return Path(self.data_dir)

    def _file_bytes(self, ref: Any) -> bytes:
        """A file a fetch names, held to the real rules: inside the data folder, no link,
        not the core's own app.db."""
        if not isinstance(ref, str) or not ref or ref.startswith("/") or "\\" in ref \
                or ":" in ref or any(p in ("", ".", "..") for p in ref.split("/")):
            raise CallError("invalid", "A file for net.fetch must be a path inside your "
                                       "app's own data folder. Nothing was sent.")
        if ref.split("/")[0].startswith("app.db"):
            raise CallError("not_allowed", "That file is JimmyHQ's own store of your app's "
                                           "values and records. Nothing was sent.")
        folder = self._data_folder()
        here = folder
        for part in ref.split("/"):
            here = here / part
            if here.is_symlink():
                raise CallError("not_allowed", "That path goes through a link. Nothing was "
                                               "sent.")
        if not here.is_file():
            raise CallError("not_found", "There is no such file in your app's data folder. "
                                         "Nothing was sent.")
        return here.read_bytes()

    def _files_sent(self, args: dict[str, Any]) -> list[tuple[str, bytes]]:
        refs: list[str] = []
        if args.get("file") is not None:
            refs.append(args["file"])
        for part in args.get("multipart") or []:
            if "file" in part:
                refs.append(part["file"])

        def marks(value: Any) -> None:
            if isinstance(value, dict) and "$file" in value:
                refs.append(value["$file"])
            elif isinstance(value, dict):
                for item in value.values():
                    marks(item)
            elif isinstance(value, list):
                for item in value:
                    marks(item)
        marks(args.get("json"))
        return [(ref, self._file_bytes(ref)) for ref in refs]

    def _net_fetch(self, args: dict[str, Any]) -> dict[str, Any]:
        url = args["url"]
        host = url.split("/")[2] if url.count("/") >= 2 else ""
        if not self._granted(f"net:{host}") and host not in self.owner_hosts:
            raise CallError("not_allowed", "Your app has not been granted that host, so "
                                           "nothing was sent.")
        if sum(args.get(k) is not None for k in ("body", "json", "file", "multipart")) > 1:
            raise CallError("invalid", "net.fetch takes one of \"body\", \"json\", \"file\" "
                                       "or \"multipart\", not more. Nothing was sent.")
        for ref, data in self._files_sent(args):
            self.sent_files.append({"url": url, "ref": ref, "data": data})
        self.fetched.append(args)
        if url not in self.responses:
            raise CallError("unavailable", f"The fake core has no answer for {url}: add one "
                                           "to core.responses.")
        answer = self.responses[url]
        if callable(answer):  # an answer that depends on what was asked
            answer = answer(args)
        if isinstance(answer, CallError):
            raise answer
        return answer

    def _tasks_create(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.private and args.get("notes"):
            raise CallError("invalid", "Your app is marked private, so a task it adds has a "
                                       "title only, and notes are not taken. Nothing was "
                                       "added. Keep the details in app.private_db().")
        task = {**args, "id": _id(next(self._ids)), "status": "open",
                "filed_by": f"app:{self.app_id}"}
        self.tasks.append(task)
        return task

    def _tasks_list(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"tasks": [t for t in self.tasks if t["status"] == "open"]}

    def _tasks_complete(self, args: dict[str, Any]) -> dict[str, Any]:
        for task in self.tasks:
            if task["id"] == args["id"]:
                task["status"] = "done"
                return task
        raise CallError("not_found", "There is no such task.")

    def _app_emit(self, args: dict[str, Any]) -> dict[str, Any]:
        self.emitted.append({"event": args["event"], "data": args.get("data")})
        return {"seq": len(self.emitted)}

    @property
    def count(self) -> int | None:
        """The number beside the app in the sidebar now: the last one ``ui.count`` took."""
        return self.counts[-1] if self.counts else None

    def _ui_count(self, args: dict[str, Any]) -> dict[str, Any]:
        for name in args:
            if name != "n":
                raise CallError("invalid", f"ui.count does not take \"{name}\". Nothing was "
                                           "done. It takes: n.")
        n = args.get("n")
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 999:
            raise CallError("invalid", "\"n\" for ui.count must be a whole number from 0 to "
                                       "999. Nothing was changed. Count what waits on the "
                                       "owner and send at most 999.")
        self.counts.append(n)
        return {"n": n}

    def _vault_put(self, args: dict[str, Any]) -> dict[str, Any]:
        data = base64.b64decode(args["base64"])
        ref = "sha256:" + hashlib.sha256(data).hexdigest()
        self.vault[ref] = data
        return {"ref": ref, "size": len(data)}

    def _roles_get(self, args: dict[str, Any]) -> dict[str, Any]:
        return {role: {"filled": label is not None, "agent_label": label}
                for role, label in self.roles.items()}

    def _work_give(self, args: dict[str, Any]) -> dict[str, Any]:
        role = args.get("role")
        if role not in self.roles:
            raise CallError("invalid", "\"role\" for work.give must be one of your app's own "
                                       f"roles: {', '.join(self.roles)}. No work was given.")
        if self.roles[role] is None:
            raise CallError("not_found", f"Nobody holds the role {role} in your app just now, "
                                         "so no work was given.")
        work_id = _id(next(self._ids))
        self.work[work_id] = {**args, "id": work_id, "state": "open"}
        return {"id": work_id}

    def _work_withdraw(self, args: dict[str, Any]) -> dict[str, Any]:
        given = self.work.get(args["id"])
        if given is None or given["state"] != "open":
            raise CallError("not_found", "There is no open piece of work with that id.")
        given["state"] = "withdrawn"
        return {"id": args["id"]}

    # --- another app's feeds --------------------------------------------------------------

    def offer(self, feed_ref: str, item: dict[str, Any]) -> int:
        """As another app printing one item of its feed. Returns its place."""
        producer = feed_ref.partition(".")[0]
        self.producers.setdefault(producer, "on")
        seq = next(self._feed_seq)
        self.feeds.setdefault(feed_ref, []).append({
            "seq": seq, "id": _id(next(self._ids)), "ts": 0, "v": 1, "erased": False,
            "item": json.loads(json.dumps(item))})
        return seq

    def erase_item(self, feed_ref: str, seq: int) -> None:
        """As the owner having one item erased: it keeps its place and loses its text."""
        for entry in self.feeds.get(feed_ref, []):
            if entry["seq"] == seq:
                entry.update(erased=True, item=None)

    def switch_off(self, app_id: str) -> None:
        self.producers[app_id] = "off"

    def switch_on(self, app_id: str) -> None:
        self.producers[app_id] = "on"

    def remove_app(self, app_id: str) -> None:
        self.producers[app_id] = "removed"

    def stop_offering(self, feed_ref: str) -> None:
        """As an update of the producer that no longer declares the feed."""
        self.feeds.pop(feed_ref, None)
        self.producers.setdefault(feed_ref.partition(".")[0], "on")

    def _feed_read(self, args: dict[str, Any]) -> dict[str, Any]:
        app_id, feed = str(args.get("app") or ""), str(args.get("feed") or "")
        ref = f"{app_id}.{feed}"
        if not self._granted(f"feed:{ref}"):
            raise CallError("not_allowed", f"Your app has not been granted \"feed:{ref}\", so "
                                           "nothing was read.")
        state = self.producers.get(app_id)
        if state is None or state == "removed":
            what = "was removed" if state == "removed" else "is not installed"
            raise CallError("not_found", f"The app called \"{app_id}\" {what}, so its feed "
                                         f"\"{feed}\" cannot be read. Nothing was read. Ask "
                                         "the owner to install it again, or carry on without "
                                         "it.")
        if state == "off":
            raise CallError("unavailable", f"The app \"{app_id}\" is switched off, so its feed "
                                           f"\"{feed}\" cannot be read. Nothing was read. The "
                                           "owner can switch it on from the Apps screen.")
        if ref not in self.feeds:
            raise CallError("not_found", f"The app \"{app_id}\" no longer offers a feed called "
                                         f"\"{feed}\", so nothing was read. Carry on without "
                                         "it, or ask its maker.")
        after = int(args.get("after") or 0)
        limit = min(int(args.get("limit") or _FEED_ITEMS_MOST), _FEED_ITEMS_MOST)
        found = [e for e in self.feeds[ref] if e["seq"] > after][:limit]
        self.feed_reads.append({"app": app_id, "feed": feed, "after": after})
        last = found[-1]["seq"] if found else max(
            [after] + [e["seq"] for e in self.feeds[ref] if e["seq"] <= after])
        return {"app": app_id, "feed": feed, "items": copy.deepcopy(found), "next": last}

    # --- pictures, similarity and the web ----------------------------------------------------

    def _check_images(self, images: list[str], role: str | None) -> None:
        if role not in (None, "vision"):
            raise CallError("invalid", "Pictures go to the owner's 'vision' role. Nothing was "
                                       "sent.")
        if not self.vision_on:
            raise CallError("unavailable", "The owner has not chosen a model that can see "
                                           "pictures, so nothing was sent.")
        if self.data_dir is None:
            raise CallError("not_found", "The fake core has no data_dir, so it holds no "
                                         "pictures.")
        root = Path(self.data_dir).resolve()
        for name in images:
            parts = name.split("/")
            if (not name or "\\" in name or ":" in name or name.startswith("/")
                    or any(p in ("", ".", "..") for p in parts)):
                raise CallError("invalid", "Each picture is a path inside your app's own data "
                                           "folder. Nothing was sent.")
            path = Path(self.data_dir, *parts)
            if any(Path(self.data_dir, *parts[:n]).is_symlink()
                   for n in range(1, len(parts) + 1)):
                raise CallError("not_allowed", "A picture cannot be reached through a link. "
                                               "Nothing was sent.")
            if not path.is_file() or not path.resolve().is_relative_to(root):
                raise CallError("not_found", f"There is no picture {name!r} in your app's "
                                             "data folder. Nothing was sent.")

    def _model_embed(self, args: dict[str, Any]) -> dict[str, Any]:
        texts = args["texts"]
        self.embedded.append(list(texts))
        return {"vectors": [self.embedder(t) for t in texts], "model": "fake/embed",
                "local": True}

    def _web_on(self) -> None:
        if not self.web_on:
            raise CallError("not_allowed", "The owner has web lookup switched off, so nothing "
                                           "was searched or read.")

    def _web_search(self, args: dict[str, Any]) -> dict[str, Any]:
        self._web_on()
        query = args["query"]
        self.searched.append(query)
        found = self.search_results.get(query, self.search_results.get("*", []))
        return {"results": [{"title": "", "url": "", "snippet": "", "text": None, **r}
                            for r in found[:args.get("limit") or len(found)]],
                "untrusted": True}

    def _web_read(self, args: dict[str, Any]) -> dict[str, Any]:
        self._web_on()
        url = args["url"]
        if url not in self.pages:
            raise CallError("unavailable", f"The fake core has no page for {url}: add its "
                                           "text to core.pages.")
        self.read.append(url)
        text = self.pages[url]
        start = min(int(args.get("start") or 0), len(text))
        end = min(len(text), start + int(args.get("chars") or self.page_chars))
        return {"url": url, "final_url": url, "title": "", "text": text[start:end],
                "start": start, "end": end, "total": len(text),
                "next": end if end < len(text) else None, "truncated": False, "links": [],
                "untrusted": True}


def _letters(text: str) -> list[float]:
    """A stand-in vector: how often each letter a to z appears."""
    low = text.lower()
    return [float(low.count(ch)) for ch in "abcdefghijklmnopqrstuvwxyz"] + [0.01]
