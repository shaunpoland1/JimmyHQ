"""The app manifest: the one gate between a stranger's repo and JimmyHQ.

An app's root holds ``jimmyhq.app.json``. ``parse`` turns its text into a ``Manifest`` or
raises ``ManifestError`` with one sentence per mistake, each naming the field and what to
write instead. Nothing is guessed, defaulted from a wrong type, or ignored: an unknown
field is a mistake. Nothing here runs or imports anything from the app.

    manifest = load(folder)                  # read, parse, and check the files it names
    manifest = parse(text_or_dict)           # the manifest alone
    check_alongside(manifest, installed)     # names that would clash with another app
    manifest.to_dict()                       # parses back to an equal manifest

What an app owns follows from its id: the actor ``app:<id>``; kinds, settings and secrets
named ``app.<id>.*``; agent tools and memory sources named ``app_<id>_*``; the feeds it
offers other apps, each one of its own kinds (``feeds``).
"""

from __future__ import annotations

import copy
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jimmyhq.apps import permissions as perms
from jimmyhq.apps.depth import too_deep
from jimmyhq.apps.permissions import FULL, SANDBOXED, TIERS, PermissionRefused
from jimmyhq.models.shapes import ROLES as MODEL_ROLES
from jimmyhq.modules import MODULES
from jimmyhq.settings import TYPES as SETTING_TYPES
from jimmyhq.settings import Setting, SettingError

MANIFEST_FILE = "jimmyhq.app.json"
MANIFEST_VERSION = 1
# The app API this core serves. A minor only adds; a major may remove.
APP_API: tuple[int, int] = (1, 0)
DESCRIBED = "described"
MAX_BYTES = 256 * 1024

# The words Hands uses for what an action does. A test keeps this equal to Hands' own list.
EFFECTS = ("read", "send", "create", "update", "delete", "spend", "access", "change",
           "record")

FIELD_TYPES = ("text", "int", "number", "bool", "date", "choice", "money", "link", "computed")
FILTER_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "contains", "empty", "not_empty")
TOTALS = ("sum", "avg", "min", "max", "count")
PARAM_TYPES = ("string", "integer", "number", "boolean")

_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}\Z")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}\Z")
CARD_SUBJECT_WORD = re.compile(r"^[a-z]{1,12}\Z")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(-((0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(\.(0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(\+([0-9a-zA-Z-]+(\.[0-9a-zA-Z-]+)*))?\Z")
_API = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\Z")
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*\Z")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*\Z")
_PYTHON = re.compile(
    r"^(>=|<=|==|~=|!=|>|<)\d+(\.\d+){0,2}(,(>=|<=|==|~=|!=|>|<)\d+(\.\d+){0,2})*\Z")
_CURRENCY = re.compile(r"^[A-Z]{3}\Z")

_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)})
# Ids that would read as part of the core, or that a file system keeps for itself.
RESERVED_IDS = frozenset(MODULES) | _DEVICE_NAMES | frozenset({
    "app", "apps", "core", "jimmyhq", "owner", "system", "agent", "agents", "token", "tokens",
    "api", "appfiles", "static", "assets", "examples", "setting", "settings", "secret",
    "secrets", "task", "model", "kinds", "tape", "lens", "auth", "web", "vault", "worker",
    "supervisor", "store", "described", "bridge",
})
# Settings the core declares for an app under its own prefix.
RESERVED_SETTING_KEYS = frozenset({"local_models_only", "auto_update"})
RESERVED_MODULES = frozenset({"jimmyhq", "jimmyhq_app"})
# The Village adds this argument to every tool itself.
RESERVED_PARAMS = frozenset({"thought"})

_TOOL_NAME_MAX = 64
_SOURCE_ID_MAX = 40
_LIMITS = {"screens": 12, "dock": 4, "wall": 12, "settings": 80, "secrets": 20, "tables": 30,
           "views": 40, "cards": 20, "memory_sources": 10, "tools": 30, "feeds": 10,
           "inbound": 8, "card_subjects": 10}
_MAX_FIELDS = 60
_MAX_OPTIONS = 50
_MAX_PARAMS = 20

# The pictures an app may choose for its line in the sidebar. The shell draws each one itself:
# an app names a picture, it never supplies one (rule ``an_app_names_an_icon_never_brings_one``).
ICONS = ("book", "calendar", "camera", "chart", "heart", "home", "image", "mail",
         "megaphone", "mic", "newspaper", "package", "pen", "truck", "users", "wallet")

_TOP = ("manifest", "id", "name", "summary", "author", "icon", "licence", "version", "tier", "api",
        "permissions", "screens", "dock", "wall", "settings", "secrets", "tables", "views",
        "server", "cards", "memory_sources", "tools", "private", "roles", "storage_mb",
        "feeds", "owner_hosts", "inbound", "net_expects", "card_subjects")
_REQUIRED = ("manifest", "id", "name", "summary", "licence", "version", "tier", "api",
             "permissions")
FULL_ONLY = ("server", "cards", "memory_sources", "tools", "private", "roles", "feeds",
             "inbound", "card_subjects")


@dataclass(frozen=True)
class Problem:
    field: str  # where: "id", "screens[0].entry", "tables.book.fields.stars.max"
    sentence: str


class ManifestError(ValueError):
    """The manifest was refused. ``problems`` has one sentence per mistake."""

    def __init__(self, problems: Iterable[Problem]):
        self.problems: tuple[Problem, ...] = tuple(problems)
        super().__init__(
            "You cannot use this app as it is, and nothing was installed or changed. Fix "
            f"its {MANIFEST_FILE}: " + " ".join(p.sentence for p in self.problems))

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(p.field for p in self.problems)


@dataclass(frozen=True)
class Screen:
    id: str
    label: str
    entry: str  # a path to an HTML file inside the app, or "described"

    @property
    def described(self) -> bool:
        return self.entry == DESCRIBED


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class AppSetting:
    key: str  # declared to the core as app.<id>.<key>
    type: str
    default: Any
    label: str
    help: str
    min: float | None = None
    max: float | None = None
    options: tuple[Option, ...] | None = None
    portable: bool = True
    advanced: bool = False  # a tuning knob: kept behind "Show advanced settings"


@dataclass(frozen=True)
class Secret:
    name: str  # kept in the secret store as app.<id>.<name>
    label: str
    help: str
    # "owner_host": one of these for each host the owner grants (``owner_hosts``), kept
    # as app.<id>.<name>#<host>. None: one for the app.
    for_each: str | None = None

    @property
    def per_host(self) -> bool:
        return self.for_each == OWNER_HOST


OWNER_HOST = "owner_host"
OWNER_HOSTS_MOST = 5  # the most hosts an app may ask the owner for
OWNER_HOSTS_PURPOSE_MOST = 200
OWNER_HOSTS_NEXT_MOST = 200


@dataclass(frozen=True)
class OwnerHosts:
    """The app may ask the owner for hosts of his own choosing, such as his own site's,
    for one stated purpose and up to ``max`` of them (``owner_hosts.py``). The owner adds
    each one himself; the app can only list them."""

    purpose: str  # one plain sentence, for the owner
    max: int
    # Optional: one plain sentence for the app's own Settings page, saying what to do once
    # an address is added. Never part of what the owner agrees to.
    next: str | None = None


NET_EXPECT_KB_MAX = 100_000  # the most apps.net_fetch_max_kb can be set to
NET_EXPECT_S_MAX = 3600  # the most apps.net_fetch_file_timeout_s can be set to


@dataclass(frozen=True)
class NetExpect:
    """What the app expects of one host it names (``net:<host>``): its largest answer, in
    KB, and its longest wait, in seconds. Shown to the owner at install in plain words; the
    owner's settings stay the ceiling (rule ``net_expects_is_what_a_host_gets``)."""

    host: str
    max_kb: int | None = None
    timeout_s: int | None = None


@dataclass(frozen=True)
class Server:
    entry: str  # "module:attribute"
    python: str | None = None  # a version requirement such as ">=3.12"

    @property
    def module(self) -> str:
        return self.entry.partition(":")[0]


@dataclass(frozen=True)
class Card:
    kind: str  # filed as app.<id>.<kind>
    label: str


@dataclass(frozen=True)
class MemorySource:
    id: str  # registered as app_<id>_<this>
    label: str
    description: str
    default_on: bool


@dataclass(frozen=True)
class Inbound:
    """A kind of message a device or service on the owner's network may send the app, at
    ``POST /api/in/<id>/<name>`` with a key the owner made (``inbound.py``). ``array``: a
    JSON list is taken as well as an object. ``max_kb``: the largest message the app expects,
    shown to the owner at install, and how much is read for the name, within the owner's
    ``apps.inbound_max_kb`` ceiling."""

    name: str
    label: str
    help: str = ""
    array: bool = False
    max_kb: int | None = None


@dataclass(frozen=True)
class Tool:
    name: str  # offered to agents as app_<id>_<name>
    description: str
    params: dict[str, dict[str, Any]]  # name -> {type, description, enum?}
    required: tuple[str, ...]
    effect: str


@dataclass(frozen=True)
class Feed:
    """A stream of items the app offers other apps (``feeds.py``): every event of one of
    its own kinds, ``app.<id>.<kind>``, printed by the app itself. Another app reads it
    only with ``feed:<id>.<name>``, which the owner grants."""

    name: str
    kind: str  # the app's own kind, short: "article.offered" is app.<id>.article.offered
    label: str  # a plain name for the owner: "Articles"
    description: str  # one sentence on what is in it


@dataclass(frozen=True)
class Role:
    """A role an app would like an agent to fill (see ``roles``). Nothing is forced: the
    app must work with it left empty."""

    id: str
    label: str
    purpose: str  # one plain sentence, for the owner
    charter: str  # a path inside the app to plain text or Markdown, for the agent
    tools: tuple[str, ...]  # the app's own tools this role may use
    model: str = "default"  # the model role suggested for an agent created for it
    not_with: tuple[str, ...] = ()  # roles one agent must never hold together with this
    optional: bool = True


@dataclass(frozen=True)
class Manifest:
    manifest: int
    id: str
    name: str
    summary: str
    licence: str
    version: str
    tier: str
    api: tuple[int, int]
    permissions: tuple[str, ...]
    author: str = ""
    icon: str = ""  # one of ICONS, or "" for the shell's own picture for an app
    screens: tuple[Screen, ...] = ()
    dock: tuple[Screen, ...] = ()
    wall: tuple[Screen, ...] = ()
    settings: tuple[AppSetting, ...] = ()
    secrets: tuple[Secret, ...] = ()
    tables: dict[str, Any] = field(default_factory=dict)
    views: tuple[dict[str, Any], ...] = ()
    server: Server | None = None
    cards: tuple[Card, ...] = ()
    memory_sources: tuple[MemorySource, ...] = ()
    tools: tuple[Tool, ...] = ()
    private: bool = False
    roles: tuple[Role, ...] = ()  # the roles it would like filled (roles.py)
    storage_mb: int | None = None  # more storage than the usual limit, asked at install
    feeds: tuple[Feed, ...] = ()  # streams of its own kinds other apps may read (feeds.py)
    owner_hosts: OwnerHosts | None = None  # hosts the owner may grant it (owner_hosts.py)
    inbound: tuple[Inbound, ...] = ()  # messages its devices may send it (inbound.py)
    net_expects: tuple[NetExpect, ...] = ()  # what it expects of the hosts it names
    card_subjects: tuple[str, ...] = ()  # the only subject words a private app's cards may use

    def expects(self, host: str) -> NetExpect | None:
        """What this app said it expects of ``host``, or None."""
        return next((e for e in self.net_expects if e.host == host), None)

    # What the app owns, all derived from its id.

    @property
    def actor(self) -> str:
        return f"app:{self.id}"

    @property
    def prefix(self) -> str:
        """The start of every kind, setting and secret the app owns."""
        return f"app.{self.id}."

    def setting_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def secret_name(self, name: str, host: str | None = None) -> str:
        """Where a secret is kept: ``app.<id>.<name>``, or for a secret kept for each
        owner-granted host, ``app.<id>.<name>#<host>``."""
        return f"{self.prefix}{name}" if host is None else f"{self.prefix}{name}#{host}"

    def secret(self, name: str) -> Secret | None:
        return next((s for s in self.secrets if s.name == name), None)

    def card_kind(self, kind: str) -> str:
        return f"{self.prefix}{kind}"

    def tool_name(self, name: str) -> str:
        return f"app_{self.id}_{name}"

    def source_id(self, source: str) -> str:
        return f"app_{self.id}_{source}"

    def role_capability(self, role: str) -> str:
        """The capability that offers an agent one role's tools: ``app.<id>.<role>``."""
        return f"app.{self.id}.{role}"

    def role(self, role_id: str) -> Role | None:
        return next((r for r in self.roles if r.id == role_id), None)

    def feed(self, name: str) -> Feed | None:
        return next((f for f in self.feeds if f.name == name), None)

    def feed_kind(self, feed: Feed) -> str:
        """The full kind a feed's items are printed as: always the app's own."""
        return f"{self.prefix}{feed.kind}"

    # What it asks for and what it ships.

    @property
    def hosts(self) -> tuple[str, ...]:
        return perms.hosts(self.permissions)

    def allows(self, permission: str, *, host: str | None = None) -> bool:
        return perms.allows(self.permissions, permission, host=host)

    @property
    def described(self) -> bool:
        """True when any screen, dock tab or tile is drawn by the core's own renderer."""
        return any(s.described for s in self.screens + self.dock + self.wall)

    def entry_files(self) -> tuple[str, ...]:
        """Every HTML file the manifest names, as forward-slash paths inside the app."""
        seen = dict.fromkeys(
            s.entry for s in self.screens + self.dock + self.wall if not s.described)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        """The manifest as JSON data. ``parse`` of the result equals this manifest."""
        out: dict[str, Any] = {
            "manifest": self.manifest, "id": self.id, "name": self.name,
            "summary": self.summary,
        }
        if self.author:
            out["author"] = self.author
        if self.icon:
            out["icon"] = self.icon
        out |= {"licence": self.licence, "version": self.version, "tier": self.tier,
                "api": f"{self.api[0]}.{self.api[1]}", "permissions": list(self.permissions)}
        for name in ("screens", "dock", "wall"):
            if getattr(self, name):
                out[name] = [{"id": s.id, "label": s.label, "entry": s.entry}
                             for s in getattr(self, name)]
        if self.settings:
            out["settings"] = [_setting_dict(s) for s in self.settings]
        if self.secrets:
            out["secrets"] = [{"name": s.name, "label": s.label, "help": s.help}
                              | ({"for_each": s.for_each} if s.for_each else {})
                              for s in self.secrets]
        if self.tables:
            out["tables"] = copy.deepcopy(self.tables)
        if self.views:
            out["views"] = copy.deepcopy(list(self.views))
        if self.server is not None:
            out["server"] = {"entry": self.server.entry}
            if self.server.python is not None:
                out["server"]["python"] = self.server.python
        if self.cards:
            out["cards"] = [{"kind": c.kind, "label": c.label} for c in self.cards]
        if self.memory_sources:
            out["memory_sources"] = [
                {"id": s.id, "label": s.label, "description": s.description,
                 "default_on": s.default_on} for s in self.memory_sources]
        if self.tools:
            out["tools"] = [
                {"name": t.name, "description": t.description,
                 "params": copy.deepcopy(t.params), "required": list(t.required),
                 "effect": t.effect} for t in self.tools]
        if self.private:
            out["private"] = True
        if self.roles:
            out["roles"] = [_role_dict(r) for r in self.roles]
        if self.storage_mb is not None:
            out["storage_mb"] = self.storage_mb
        if self.feeds:
            out["feeds"] = [{"name": f.name, "kind": f.kind, "label": f.label,
                             "description": f.description} for f in self.feeds]
        if self.owner_hosts is not None:
            out["owner_hosts"] = {"purpose": self.owner_hosts.purpose,
                                  "max": self.owner_hosts.max}
            if self.owner_hosts.next is not None:
                out["owner_hosts"]["next"] = self.owner_hosts.next
        if self.inbound:
            out["inbound"] = [_inbound_dict(i) for i in self.inbound]
        if self.card_subjects:
            out["card_subjects"] = list(self.card_subjects)
        if self.net_expects:
            out["net_expects"] = {e.host: {k: v for k, v in (("max_kb", e.max_kb),
                                                             ("timeout_s", e.timeout_s))
                                           if v is not None} for e in self.net_expects}
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"


def _inbound_dict(i: Inbound) -> dict[str, Any]:
    out: dict[str, Any] = {"name": i.name, "label": i.label, "help": i.help}
    if i.array:  # said only when set, so a manifest that does not use them reads as before
        out["array"] = True
    if i.max_kb is not None:
        out["max_kb"] = i.max_kb
    return out


def _setting_dict(s: AppSetting) -> dict[str, Any]:
    out: dict[str, Any] = {"key": s.key, "type": s.type, "default": copy.deepcopy(s.default),
                           "label": s.label, "help": s.help}
    if s.min is not None:
        out["min"] = s.min
    if s.max is not None:
        out["max"] = s.max
    if s.options is not None:
        out["options"] = [{"value": o.value, "label": o.label} for o in s.options]
    if not s.portable:
        out["portable"] = False
    if s.advanced:
        out["advanced"] = True
    return out


def core_setting(manifest: Manifest, setting: AppSetting) -> Setting:
    """One of the app's settings as the core's registry declares it, in section
    ``app.<id>``. The caller declares the section first."""
    return _core_setting(manifest.id, setting)


def _core_setting(app_id: str, s: AppSetting) -> Setting:
    pairs = tuple((o.value, o.label) for o in s.options) if s.options is not None else None
    return Setting(
        key=f"app.{app_id}.{s.key}", section=f"app.{app_id}", type=s.type, label=s.label,
        help=s.help, default=copy.deepcopy(s.default), min=s.min, max=s.max,
        choices=pairs if s.type == "choice" else None,
        items=pairs if s.type == "list" else None, portable=s.portable,
        advanced=s.advanced)


# --- small readers --------------------------------------------------------------------------


class _Bad(Exception):
    def __init__(self, where: str, sentence: str):
        super().__init__(sentence)
        self.problem = Problem(where, sentence)


@contextmanager
def _collect(problems: list[Problem]) -> Iterator[None]:
    try:
        yield
    except _Bad as bad:
        problems.append(bad.problem)


_MISSING = object()


def _kind_of(value: Any) -> str:
    if value is None:
        return "nothing (null)"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, int | float):
        return "a number"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "a list"
    return "an object"


def _show(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)
    text = "".join(c if c.isprintable() else "?" for c in text)
    return text if len(text) <= 60 else text[:60] + "..."


def _object(value: Any, where: str, allowed: tuple[str, ...], problems: list[Problem],
            example: str) -> dict[str, Any]:
    """A JSON object with only known keys. Unknown keys are each reported."""
    if not isinstance(value, dict):
        raise _Bad(where, f"\"{where}\" must be an object such as {example}, not "
                          f"{_kind_of(value)}.")
    for key in value:
        if key not in allowed:
            problems.append(Problem(
                f"{where}.{_show(key)}",
                f"\"{where}.{_show(key)}\" is not a field JimmyHQ knows, and unknown fields "
                f"are refused. Remove it; the fields here are: {', '.join(allowed)}."))
    return value


def _need(obj: dict[str, Any], key: str, where: str, example: str) -> Any:
    if key not in obj:
        raise _Bad(f"{where}.{key}" if where else key,
                   f"\"{where + '.' if where else ''}{key}\" is missing. Add it, such as "
                   f"{example}.")
    return obj[key]


def _text(value: Any, where: str, most: int, example: str, *, empty: bool = False) -> str:
    """One line of plain text: trimmed, printable, within its length."""
    if not isinstance(value, str):
        raise _Bad(where, f"\"{where}\" must be text such as \"{example}\", not "
                          f"{_kind_of(value)}.")
    if not value and not empty:
        raise _Bad(where, f"\"{where}\" is empty. Write something such as \"{example}\".")
    if len(value) > most:
        raise _Bad(where, f"\"{where}\" is {len(value)} characters long. Shorten it to "
                          f"{most} or fewer.")
    if value != value.strip():
        raise _Bad(where, f"\"{where}\" starts or ends with a space. Remove the space.")
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise _Bad(where, f"\"{where}\" has a line break or an invisible character in it. "
                          "Write it as one line of ordinary text.")
    return value


def _icon(value: Any) -> str:
    """Rule ``an_app_names_an_icon_never_brings_one``: one of the words in ``ICONS``."""
    if not isinstance(value, str) or value not in ICONS:
        raise _Bad("icon", f"\"icon\" must be one of {', '.join(ICONS)}, not {_show(value)}. "
                           "JimmyHQ draws the picture itself; an app cannot bring its own. "
                           "Leave \"icon\" out to get the usual one.")
    return value


def _name(value: Any, where: str, pattern: re.Pattern[str], example: str) -> str:
    if not isinstance(value, str):
        raise _Bad(where, f"\"{where}\" must be text such as \"{example}\", not "
                          f"{_kind_of(value)}.")
    if not pattern.match(value):
        raise _Bad(where, f"\"{where}\" is \"{_show(value)}\", which cannot be used as a "
                          "name. Write a short lower-case name of letters, digits and "
                          f"underscores that starts with a letter, such as \"{example}\".")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise _Bad(where, f"\"{where}\" must be true or false, not {_kind_of(value)}.")
    return value


def _number(value: Any, where: str, *, whole: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Bad(where, f"\"{where}\" must be a number, not {_kind_of(value)}.")
    if isinstance(value, float) and not math.isfinite(value):
        raise _Bad(where, f"\"{where}\" must be an ordinary number.")
    if abs(value) > 2**53:
        raise _Bad(where, f"\"{where}\" is too large. Use a number below 9007199254740992.")
    if whole and not isinstance(value, int):
        raise _Bad(where, f"\"{where}\" must be a whole number such as 5, not {value}.")
    return value


def _list(value: Any, where: str, most: int, example: str) -> list[Any]:
    if not isinstance(value, list):
        raise _Bad(where, f"\"{where}\" must be a list such as {example}, not "
                          f"{_kind_of(value)}.")
    if len(value) > most:
        raise _Bad(where, f"\"{where}\" has {len(value)} entries. Keep it to {most} or fewer.")
    return value


def _unique(items: list[Any], attr: str, where: str, problems: list[Problem]) -> None:
    seen: set[str] = set()
    for item in items:
        value = getattr(item, attr)
        if value in seen:
            problems.append(Problem(where, f"\"{where}\" has two entries with the {attr} "
                                           f"\"{value}\". Give each its own {attr}."))
        seen.add(value)


# --- paths ----------------------------------------------------------------------------------


def check_path(value: Any, where: str = "path") -> str:
    """A forward-slash path that stays inside the app folder on every system, or
    ``ManifestError``. Nothing on disk is touched."""
    try:
        return _path(value, where)
    except _Bad as bad:
        raise ManifestError([bad.problem]) from None


def _path(value: Any, where: str) -> str:
    example = "index.html"
    if not isinstance(value, str) or not value:
        raise _Bad(where, f"\"{where}\" must be a path to a file in the app, such as "
                          f"\"{example}\", not {_kind_of(value) if value != '' else 'empty'}.")
    shown = _show(value)

    def refuse(why: str) -> _Bad:
        return _Bad(where, f"\"{where}\" is \"{shown}\", which {why} Write a path inside the "
                           f"app folder with forward slashes, such as \"{example}\".")

    if len(value) > 200:
        raise refuse("is longer than 200 characters.")
    if "\\" in value:
        raise refuse("has a backslash in it.")
    if value.startswith("/") or ":" in value:
        raise refuse("is a full address or path, not a path inside the app.")
    for segment in value.split("/"):
        if segment in ("", ".", ".."):
            raise refuse("leaves the app folder or has an empty step in it.")
        if not _SEGMENT.match(segment) or segment.endswith("."):
            raise refuse("has a step that is not made of letters, digits, dots, hyphens and "
                         "underscores, or that starts or ends with a dot.")
        if segment.split(".")[0].lower() in _DEVICE_NAMES:
            raise refuse("uses a name Windows keeps for a device.")
    return value


def _entry(value: Any, where: str) -> str:
    if value == DESCRIBED:
        return DESCRIBED
    path = _path(value, where)
    if not path.lower().endswith(".html"):
        raise _Bad(where, f"\"{where}\" is \"{_show(path)}\", which is not an HTML file. "
                          f"Name a file ending in .html, or write \"{DESCRIBED}\" for a "
                          "screen JimmyHQ draws from your tables and views.")
    return path


def inside(folder: Path, relative: str, where: str = "path") -> Path:
    """The real file a manifest path names, proven to be an ordinary file inside the
    folder with no link on the way, or ``ManifestError``."""
    relative = check_path(relative, where)
    root = folder.resolve()
    here = folder
    for segment in relative.split("/"):
        here = here / segment
        if here.is_symlink():
            raise ManifestError([Problem(where, (
                f"\"{where}\" is \"{relative}\", which goes through a link. Links are "
                "refused; put the real file in the app folder."))])
    if not here.is_file():
        raise ManifestError([Problem(where, (
            f"\"{where}\" names \"{relative}\", and there is no such file in the app. Add "
            "the file, or correct the path."))])
    real = here.resolve()
    if not real.is_relative_to(root):
        raise ManifestError([Problem(where, (
            f"\"{where}\" is \"{relative}\", which leads outside the app folder. Keep every "
            "file inside it."))])
    return real


# --- each field -----------------------------------------------------------------------------


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise _Bad("id", f"\"id\" is {_show(value) if isinstance(value, str) else _kind_of(value)}"
                         ", which cannot be used. Write 2 to 31 lower-case letters, digits "
                         "or underscores, starting with a letter, such as \"water\".")
    if value in RESERVED_IDS:
        raise _Bad("id", f"\"id\" is \"{value}\", a name JimmyHQ keeps for itself. Choose "
                         f"another, such as \"my_{value}\".")
    return value


def _version(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 64 or not _SEMVER.match(value):
        raise _Bad("version", "\"version\" must be three numbers with dots, such as "
                              f"\"1.0.0\", not {_show(value)}.")
    return value


def _api(value: Any, core: tuple[int, int]) -> tuple[int, int]:
    if not isinstance(value, str) or not _API.match(value):
        raise _Bad("api", "\"api\" must be the app API it was written for as "
                          f"\"major.minor\", such as \"{core[0]}.{core[1]}\", not "
                          f"{_show(value)}.")
    major, minor = (int(part) for part in value.split("."))
    have = f"{core[0]}.{core[1]}"
    if major > core[0] or (major == core[0] and minor > core[1]):
        raise _Bad("api", f"\"api\" is \"{value}\": this app needs a newer JimmyHQ, one with "
                          f"app API {value} or newer, and this one has {have}. Update "
                          "JimmyHQ, then try again.")
    if major < core[0]:
        raise _Bad("api", f"\"api\" is \"{value}\": this app was written for an older app "
                          f"API that this JimmyHQ no longer serves (it has {have}). Ask its "
                          f"author for a version written for API {core[0]}.")
    return major, minor


def _screens(value: Any, where: str, problems: list[Problem]) -> tuple[Screen, ...]:
    example = '[{"id": "main", "label": "Water", "entry": "index.html"}]'
    out: list[Screen] = []
    for n, raw in enumerate(_list(value, where, _LIMITS[where], example)):
        at = f"{where}[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("id", "label", "entry"), problems, example[1:-1])
            out.append(Screen(
                id=_name(_need(obj, "id", at, '"main"'), f"{at}.id", _NAME, "main"),
                label=_text(_need(obj, "label", at, '"Water"'), f"{at}.label", 40, "Water"),
                entry=_entry(_need(obj, "entry", at, '"index.html"'), f"{at}.entry")))
    _unique(out, "id", where, problems)
    return tuple(out)


def _options(value: Any, where: str, problems: list[Problem]) -> tuple[Option, ...]:
    example = '[{"value": "km", "label": "Kilometres"}]'
    out: list[Option] = []
    raw_list = _list(value, where, _MAX_OPTIONS, example)
    if not raw_list:
        raise _Bad(where, f"\"{where}\" is empty. List at least one, such as {example}.")
    for n, raw in enumerate(raw_list):
        at = f"{where}[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("value", "label"), problems, example[1:-1])
            out.append(Option(
                value=_text(_need(obj, "value", at, '"km"'), f"{at}.value", 60, "km"),
                label=_text(_need(obj, "label", at, '"Kilometres"'), f"{at}.label", 60,
                            "Kilometres")))
    _unique(out, "value", where, problems)
    return tuple(out)


def _setting(raw: Any, at: str, app_id: str, problems: list[Problem]) -> AppSetting:
    example = ('{"key": "goal", "type": "int", "default": 8, "label": "Glasses a day", '
               '"help": "Your daily goal."}')
    obj = _object(raw, at, ("key", "type", "default", "label", "help", "min", "max",
                            "options", "portable", "advanced"), problems, example)
    key = _name(_need(obj, "key", at, '"goal"'), f"{at}.key", _KEY, "goal")
    if key in RESERVED_SETTING_KEYS:
        raise _Bad(f"{at}.key", f"\"{at}.key\" is \"{key}\", a setting JimmyHQ adds for an "
                                "app itself. Choose another key.")
    kind = _need(obj, "type", at, '"int"')
    if kind not in SETTING_TYPES:
        raise _Bad(f"{at}.type", f"\"{at}.type\" must be one of: {', '.join(SETTING_TYPES)}. "
                                 f"It is {_show(kind)}.")
    label = _text(_need(obj, "label", at, '"Glasses a day"'), f"{at}.label", 60,
                  "Glasses a day")
    help_ = _text(_need(obj, "help", at, '"Your daily goal."'), f"{at}.help", 300,
                  "Your daily goal.")
    low = high = None
    for name in ("min", "max"):
        if name in obj:
            if kind not in ("int", "float"):
                raise _Bad(f"{at}.{name}", f"\"{at}.{name}\" only applies to a number "
                                           f"setting, and this one is \"{kind}\". Remove it.")
            number = _number(obj[name], f"{at}.{name}", whole=kind == "int")
            low, high = (number, high) if name == "min" else (low, number)
    if low is not None and high is not None and low > high:
        raise _Bad(f"{at}.min", f"\"{at}.min\" is {low:g}, which is more than its \"max\" "
                                f"of {high:g}. Swap them or correct one.")
    options = None
    if "options" in obj:
        if kind not in ("choice", "list"):
            raise _Bad(f"{at}.options", f"\"{at}.options\" only applies to a \"choice\" or "
                                        f"\"list\" setting, and this one is \"{kind}\". "
                                        "Remove it.")
        options = _options(obj["options"], f"{at}.options", problems)
    elif kind == "choice":
        raise _Bad(f"{at}.options", f"\"{at}.options\" is missing: a \"choice\" setting "
                                    "lists what can be chosen, such as "
                                    '[{"value": "km", "label": "Kilometres"}].')
    portable = _bool(obj["portable"], f"{at}.portable") if "portable" in obj else True
    advanced = _bool(obj["advanced"], f"{at}.advanced") if "advanced" in obj else False
    default = _need(obj, "default", at, "8")
    setting = AppSetting(key=key, type=kind, default=copy.deepcopy(default), label=label,
                         help=help_, min=low, max=high, options=options, portable=portable,
                         advanced=advanced)
    try:
        checked = _core_setting(app_id, setting).check(default)
    except SettingError as error:
        raise _Bad(f"{at}.default", f"\"{at}.default\" does not fit the setting's own "
                                    f"rules: {error} Write a default that does.") from None
    return AppSetting(key=key, type=kind, default=checked, label=label, help=help_, min=low,
                      max=high, options=options, portable=portable, advanced=advanced)


def _settings(value: Any, app_id: str, problems: list[Problem]) -> tuple[AppSetting, ...]:
    out: list[AppSetting] = []
    for n, raw in enumerate(_list(value, "settings", _LIMITS["settings"], "[{...}]")):
        with _collect(problems):
            out.append(_setting(raw, f"settings[{n}]", app_id, problems))
    _unique(out, "key", "settings", problems)
    return tuple(out)


def _secrets(value: Any, problems: list[Problem]) -> tuple[Secret, ...]:
    example = '[{"name": "booking_key", "label": "Booking key", "help": "From your account."}]'
    out: list[Secret] = []
    for n, raw in enumerate(_list(value, "secrets", _LIMITS["secrets"], example)):
        at = f"secrets[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("name", "label", "help", "for_each"), problems,
                          example[1:-1])
            out.append(Secret(
                name=_name(_need(obj, "name", at, '"booking_key"'), f"{at}.name", _KEY,
                           "booking_key"),
                label=_text(_need(obj, "label", at, '"Booking key"'), f"{at}.label", 60,
                            "Booking key"),
                help=_text(_need(obj, "help", at, '"From your account page."'), f"{at}.help",
                           300, "From your account page."),
                for_each=_for_each(obj.get("for_each"), f"{at}.for_each")))
    _unique(out, "name", "secrets", problems)
    return tuple(out)


def _inbound(value: Any, problems: list[Problem]) -> tuple[Inbound, ...]:
    example = ('[{"name": "event", "label": "Home events", "help": "What your home sends '
               'when something happens."}]')
    out: list[Inbound] = []
    for n, raw in enumerate(_list(value, "inbound", _LIMITS["inbound"], example)):
        at = f"inbound[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("name", "label", "help", "array", "max_kb"), problems,
                          example[1:-1])
            out.append(Inbound(
                name=_name(_need(obj, "name", at, '"event"'), f"{at}.name", _NAME, "event"),
                label=_text(_need(obj, "label", at, '"Home events"'), f"{at}.label", 60,
                            "Home events"),
                help=_text(obj.get("help", ""), f"{at}.help", 300,
                           "What your home sends.", empty=True),
                array=_bool(obj["array"], f"{at}.array") if "array" in obj else False,
                max_kb=_inbound_kb(obj["max_kb"], f"{at}.max_kb") if "max_kb" in obj
                else None))
    _unique(out, "name", "inbound", problems)
    return tuple(out)


INBOUND_KB_MAX = 1024  # the most apps.inbound_max_kb can be set to


def _inbound_kb(value: Any, where: str) -> int:
    """Rule ``inbound_max_kb_is_what_the_name_expects``: the largest message one inbound name
    expects, in KB. It is how much is read for that name, within the owner's
    ``apps.inbound_max_kb`` ceiling."""
    kb = _number(value, where, whole=True)
    if not 1 <= kb <= INBOUND_KB_MAX:
        raise _Bad(where, f"\"{where}\" is the largest message this name expects, in KB, "
                          f"from 1 to {INBOUND_KB_MAX}, such as 64.")
    return int(kb)


def _for_each(value: Any, where: str) -> str | None:
    """Rule ``secret_for_each_owner_host``: a secret is one for the app, or, written
    ``"for_each": "owner_host"``, one for each host the owner grants it."""
    if value is None:
        return None
    if value != OWNER_HOST:
        raise _Bad(where, f"\"{where}\" can only be \"{OWNER_HOST}\": one secret for each "
                          "host the owner grants the app. Leave it out for one secret for "
                          "the whole app.")
    return value


def check_owner_hosts(value: Any) -> OwnerHosts:
    """Rule ``owner_hosts_purpose_and_max``: ``{"purpose": "<one sentence>", "max": n}``,
    with n from 1 to ``OWNER_HOSTS_MOST``, and optionally ``"next"``: one plain sentence
    saying what to do once an address is added (shown on the app's Settings page only)."""
    example = ('"owner_hosts": {"purpose": "Publish drafts to your own site.", '
               '"max": 3}')
    if not isinstance(value, dict) or not {"purpose", "max"} <= set(value) \
            or not set(value) <= {"purpose", "max", "next"}:
        raise _Bad("owner_hosts", "\"owner_hosts\" says why the app asks you for hosts of "
                                  "your own and how many at most, and optionally what to "
                                  f"do next, and nothing else, such as {example}.")
    purpose = _text(value["purpose"], "owner_hosts.purpose", OWNER_HOSTS_PURPOSE_MOST,
                    "Publish drafts to your own site.")
    most = value["max"]
    if isinstance(most, bool) or not isinstance(most, int) \
            or not 1 <= most <= OWNER_HOSTS_MOST:
        raise _Bad("owner_hosts.max", "\"owner_hosts.max\" is how many hosts the owner may "
                                      f"grant, a whole number from 1 to {OWNER_HOSTS_MOST}.")
    then = None
    if "next" in value:
        then = _text(value["next"], "owner_hosts.next", OWNER_HOSTS_NEXT_MOST,
                     "Then open the app and choose it.")
    return OwnerHosts(purpose=purpose, max=most, next=then)


def _net_expects(value: Any) -> tuple[NetExpect, ...]:
    """Rule ``net_expects_is_what_a_host_gets``: for hosts the app names, the largest answer
    (KB) and the longest wait (seconds) it expects, such as
    ``{"api.example.com": {"max_kb": 20480, "timeout_s": 180}}``. Each host must be one its
    ``permissions`` name (checked with the other fields). Never more than the owner allows."""
    example = '"net_expects": {"api.example.com": {"max_kb": 20480, "timeout_s": 180}}'
    if not isinstance(value, dict) or not value:
        raise _Bad("net_expects", "\"net_expects\" is an object of the hosts your app names, "
                                  "each with what it expects of that host, such as "
                                  f"{example}.")
    if len(value) > perms.MAX_HOSTS:
        raise _Bad("net_expects", f"\"net_expects\" names {len(value)} hosts. Keep it to "
                                  f"{perms.MAX_HOSTS} or fewer.")
    out: list[NetExpect] = []
    for host, said in value.items():
        at = f"net_expects.{_show(host)}"
        if not isinstance(said, dict) or not said or not set(said) <= {"max_kb", "timeout_s"}:
            raise _Bad(at, f"\"{at}\" says \"max_kb\", \"timeout_s\" or both, and nothing "
                           "else, such as {\"max_kb\": 20480, \"timeout_s\": 180}.")
        kb = said.get("max_kb")
        wait = said.get("timeout_s")
        if kb is not None:
            kb = _number(kb, f"{at}.max_kb", whole=True)
            if not 1 <= kb <= NET_EXPECT_KB_MAX:
                raise _Bad(f"{at}.max_kb", f"\"{at}.max_kb\" is the largest answer your app "
                                           "expects from this host, in KB, from 1 to "
                                           f"{NET_EXPECT_KB_MAX}, such as 20480.")
        if wait is not None:
            wait = _number(wait, f"{at}.timeout_s", whole=True)
            if not 1 <= wait <= NET_EXPECT_S_MAX:
                raise _Bad(f"{at}.timeout_s", f"\"{at}.timeout_s\" is the longest your app "
                                              "expects to wait for this host, in seconds, "
                                              f"from 1 to {NET_EXPECT_S_MAX}, such as 180.")
        out.append(NetExpect(host=str(host), max_kb=None if kb is None else int(kb),
                             timeout_s=None if wait is None else int(wait)))
    return tuple(out)


STORAGE_MB_MOST = 100_000


def _storage_mb(value: Any) -> int:
    """How much storage the app asks for, in megabytes: shown to the owner at install,
    and given only if they agree. The owner can change it afterwards."""
    if isinstance(value, bool) or not isinstance(value, int) \
            or not 1 <= value <= STORAGE_MB_MOST:
        raise _Bad("storage_mb", "\"storage_mb\" is the storage the app needs, as a whole "
                                 f"number of megabytes from 1 to {STORAGE_MB_MOST}, such as "
                                 "\"storage_mb\": 500.")
    return value


def _server(value: Any, problems: list[Problem]) -> Server:
    example = '{"entry": "posts.main:app", "python": ">=3.12"}'
    obj = _object(value, "server", ("entry", "python"), problems, example)
    entry = _need(obj, "entry", "server", '"posts.main:app"')
    if not isinstance(entry, str) or len(entry) > 120 or not _MODULE.match(entry):
        raise _Bad("server.entry", "\"server.entry\" must name a Python module and the app "
                                   "object in it as \"module:name\", such as "
                                   f"\"posts.main:app\", not {_show(entry)}.")
    top = entry.partition(":")[0].split(".")[0]
    if top in RESERVED_MODULES:
        raise _Bad("server.entry", f"\"server.entry\" starts with \"{top}\", a module name "
                                   "JimmyHQ keeps for itself. Put your code in a module "
                                   "with its own name.")
    python = None
    if "python" in obj:
        python = obj["python"]
        if not isinstance(python, str) or len(python) > 60 or not _PYTHON.match(python):
            raise _Bad("server.python", "\"server.python\" must be a version requirement "
                                        "with no spaces, such as \">=3.12\" or "
                                        f"\">=3.12,<3.14\", not {_show(python)}.")
    return Server(entry=entry, python=python)


def _cards(value: Any, problems: list[Problem]) -> tuple[Card, ...]:
    example = '[{"kind": "draft", "label": "Post draft"}]'
    out: list[Card] = []
    for n, raw in enumerate(_list(value, "cards", _LIMITS["cards"], example)):
        at = f"cards[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("kind", "label"), problems, example[1:-1])
            out.append(Card(
                kind=_name(_need(obj, "kind", at, '"draft"'), f"{at}.kind", _NAME, "draft"),
                label=_text(_need(obj, "label", at, '"Post draft"'), f"{at}.label", 60,
                            "Post draft")))
    _unique(out, "kind", "cards", problems)
    return tuple(out)


def _card_subjects(value: Any) -> tuple[str, ...]:
    """Rule ``a_private_cards_subject_is_a_declared_word``: the short words a private app's
    cards may carry as their subject (``reminder`` in ``reminder:12``)."""
    example = '["reminder", "package"]'
    items = _list(value, "card_subjects", _LIMITS["card_subjects"], example)
    for n, item in enumerate(items):
        if not isinstance(item, str) or not CARD_SUBJECT_WORD.match(item):
            raise _Bad(f"card_subjects[{n}]", (
                f"\"card_subjects[{n}]\" is {_show(item)}, which cannot be a card subject. "
                "Write a lower-case word of at most 12 letters, such as \"reminder\". "
                f"For example \"card_subjects\": {example}."))
    if len(set(items)) != len(items):
        raise _Bad("card_subjects", "\"card_subjects\" names the same word twice. Name each "
                                    "once.")
    return tuple(items)


def _memory_sources(value: Any, problems: list[Problem]) -> tuple[MemorySource, ...]:
    example = ('[{"id": "notes", "label": "Notes", "description": "The notes you write in '
               'this app.", "default_on": false}]')
    out: list[MemorySource] = []
    for n, raw in enumerate(_list(value, "memory_sources", _LIMITS["memory_sources"], example)):
        at = f"memory_sources[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("id", "label", "description", "default_on"), problems,
                          example[1:-1])
            out.append(MemorySource(
                id=_name(_need(obj, "id", at, '"notes"'), f"{at}.id", _NAME, "notes"),
                label=_text(_need(obj, "label", at, '"Notes"'), f"{at}.label", 60, "Notes"),
                description=_text(_need(obj, "description", at, '"The notes you write."'),
                                  f"{at}.description", 300, "The notes you write."),
                default_on=_bool(_need(obj, "default_on", at, "false"), f"{at}.default_on")))
    _unique(out, "id", "memory_sources", problems)
    return tuple(out)


def _params(value: Any, where: str, problems: list[Problem]) -> dict[str, dict[str, Any]]:
    example = '{"city": {"type": "string", "description": "The city to look up."}}'
    if not isinstance(value, dict):
        raise _Bad(where, f"\"{where}\" must be an object such as {example}, not "
                          f"{_kind_of(value)}. Write {{}} for a tool that takes nothing.")
    if len(value) > _MAX_PARAMS:
        raise _Bad(where, f"\"{where}\" has {len(value)} entries. Keep it to {_MAX_PARAMS} "
                          "or fewer.")
    out: dict[str, dict[str, Any]] = {}
    for name, raw in value.items():
        at = f"{where}.{_show(name)}"
        with _collect(problems):
            _name(name, at, _KEY, "city")
            if name in RESERVED_PARAMS:
                raise _Bad(at, f"\"{at}\" uses a name JimmyHQ adds to every tool itself. "
                               "Choose another name.")
            obj = _object(raw, at, ("type", "description", "enum"), problems,
                          '{"type": "string", "description": "The city to look up."}')
            kind = _need(obj, "type", at, '"string"')
            if kind not in PARAM_TYPES:
                raise _Bad(f"{at}.type", f"\"{at}.type\" must be one of: "
                                         f"{', '.join(PARAM_TYPES)}. It is {_show(kind)}.")
            param: dict[str, Any] = {
                "type": kind,
                "description": _text(_need(obj, "description", at, '"The city to look up."'),
                                     f"{at}.description", 300, "The city to look up.")}
            if "enum" in obj:
                if kind != "string":
                    raise _Bad(f"{at}.enum", f"\"{at}.enum\" only applies to a \"string\", "
                                             f"and this is \"{kind}\". Remove it.")
                values = _list(obj["enum"], f"{at}.enum", _MAX_OPTIONS, '["metric", "imperial"]')
                if not values:
                    raise _Bad(f"{at}.enum", f"\"{at}.enum\" is empty. List the values it "
                                             "can take, or remove it.")
                param["enum"] = [_text(v, f"{at}.enum[{i}]", 60, "metric")
                                 for i, v in enumerate(values)]
                if len(set(param["enum"])) != len(param["enum"]):
                    raise _Bad(f"{at}.enum", f"\"{at}.enum\" lists a value twice. List each "
                                             "once.")
            out[name] = param
    return out


def _tools(value: Any, problems: list[Problem]) -> tuple[Tool, ...]:
    example = ('[{"name": "lookup", "description": "Look up a booking.", "params": {}, '
               '"required": [], "effect": "read"}]')
    out: list[Tool] = []
    for n, raw in enumerate(_list(value, "tools", _LIMITS["tools"], example)):
        at = f"tools[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("name", "description", "params", "required", "effect"),
                          problems, example[1:-1])
            name = _name(_need(obj, "name", at, '"lookup"'), f"{at}.name", _NAME, "lookup")
            description = _text(_need(obj, "description", at, '"Look up a booking."'),
                                f"{at}.description", 500, "Look up a booking.")
            before = len(problems)
            params = _params(_need(obj, "params", at, "{}"), f"{at}.params", problems)
            required = _list(_need(obj, "required", at, "[]"), f"{at}.required", _MAX_PARAMS,
                             '["city"]')
            for item in required:
                if not isinstance(item, str) or item not in params:
                    if len(problems) > before:
                        break  # the parameter itself was refused above
                    raise _Bad(f"{at}.required", f"\"{at}.required\" names {_show(item)}, "
                                                 f"which is not one of this tool's params. "
                                                 "List only names from \"params\".")
            if len(set(map(str, required))) != len(required):
                raise _Bad(f"{at}.required", f"\"{at}.required\" names a parameter twice. "
                                             "List each once.")
            effect = _need(obj, "effect", at, '"read"')
            if effect not in EFFECTS:
                raise _Bad(f"{at}.effect", f"\"{at}.effect\" must say what the tool does, as "
                                           f"one of: {', '.join(EFFECTS)}. It is "
                                           f"{_show(effect)}.")
            if len(problems) == before:
                out.append(Tool(name=name, description=description, params=params,
                                required=tuple(required), effect=effect))
    _unique(out, "name", "tools", problems)
    return tuple(out)


# --- feeds (a full app offering a stream of its own kind to other apps) ----------------------

_FEED_KIND = re.compile(r"^[a-z][a-z0-9_]{0,40}(\.[a-z][a-z0-9_]{0,40}){0,3}\Z")


def check_feed_kind(value: Any, where: str) -> str:
    """Rule ``feed_kind_is_own``: a feed carries one of the app's own kinds, named short.
    JimmyHQ puts ``app.<id>.`` in front itself, so a feed can never be another app's
    kind or one of JimmyHQ's; a name that tries (``app.posts.posted``) is refused."""
    if not isinstance(value, str) or not _FEED_KIND.match(value):
        raise _Bad(where, f"\"{where}\" must be the short name of one of your own kinds of "
                          "event, such as \"article.offered\" (letters, digits and _, with at "
                          f"most three dots), not {_show(value)}.")
    if value == "app" or value.startswith("app."):
        raise _Bad(where, f"\"{where}\" is \"{value}\", which reads as the full name of a "
                          "kind. A feed can only carry your app's own kind: write its short "
                          "name, such as \"article.offered\"; JimmyHQ adds \"app.<your id>.\" "
                          "itself.")
    return value


def _feeds(value: Any, problems: list[Problem]) -> tuple[Feed, ...]:
    example = ('[{"name": "articles", "kind": "article.offered", "label": "Articles", '
               '"description": "Articles you chose to offer, with their address and topic."}]')
    out: list[Feed] = []
    for n, raw in enumerate(_list(value, "feeds", _LIMITS["feeds"], example)):
        at = f"feeds[{n}]"
        with _collect(problems):
            obj = _object(raw, at, ("name", "kind", "label", "description"), problems,
                          example[1:-1])
            out.append(Feed(
                name=_name(_need(obj, "name", at, '"articles"'), f"{at}.name", _NAME,
                           "articles"),
                kind=check_feed_kind(_need(obj, "kind", at, '"article.offered"'),
                                     f"{at}.kind"),
                label=_text(_need(obj, "label", at, '"Articles"'), f"{at}.label", 40,
                            "Articles"),
                description=_text(_need(obj, "description", at,
                                        '"Articles you chose to offer."'),
                                  f"{at}.description", 300, "Articles you chose to offer.")))
    _unique(out, "name", "feeds", problems)
    return tuple(out)


def _feeds_consistency(got: dict[str, Any]) -> list[Problem]:
    """Rules ``private_offers_no_feed`` and ``no_feed_of_its_own``."""
    out: list[Problem] = []
    if got.get("private") and got.get("feeds"):
        out.append(Problem("feeds", (
            "\"private\" is true, and a private app keeps what it holds off the record, so "
            "it has nothing other apps could read. Remove \"feeds\", or the private "
            "promise.")))
    for n, text in enumerate(got["permissions"]):
        found = perms.feed_ref(text)
        if found is not None and found[0] == got["id"]:
            out.append(Problem(f"permissions[{n}]", (
                f"\"permissions[{n}]\" asks to read \"{text}\", a feed of this app itself. "
                "An app reads what it wrote with tape.read; remove the permission.")))
    return out


# --- roles (a full app that works with agents) ------------------------------------------------
# What an app may say about the agents it would like: see ``roles.py`` for what JimmyHQ does
# with them. Every mistake is its own sentence.

ROLE_MODELS = tuple(r for r in MODEL_ROLES if r != "embeddings")
CHARTER_SUFFIXES = (".md", ".txt")
CHARTER_MAX_BYTES = 8000  # the most a standing priority's details hold
_ROLE_KEYS = ("id", "label", "purpose", "charter", "tools", "model", "not_with", "optional")
_ROLE_CAPABILITY_MAX = 64  # what the Village takes as a capability's name
_ROLES_MAX = 10


def _role_dict(r: Role) -> dict[str, Any]:
    return {"id": r.id, "label": r.label, "purpose": r.purpose, "charter": r.charter,
            "tools": list(r.tools), "model": r.model, "not_with": list(r.not_with),
            "optional": r.optional}


def _charter_path(value: Any, where: str) -> str:
    """Rule ``charter_inside_the_app``: a charter is a plain text or Markdown file inside
    the app's folder."""
    path = _path(value, where)
    if not path.lower().endswith(CHARTER_SUFFIXES):
        raise _Bad(where, f"\"{where}\" is \"{_show(path)}\", which is not a plain text or "
                          "Markdown file. Name a file ending in .md or .txt, such as "
                          "\"roles/writer.md\".")
    return path


def _names_list(value: Any, where: str, most: int, example: str) -> list[str]:
    items = _list(value, where, most, example)
    for n, item in enumerate(items):
        _name(item, f"{where}[{n}]", _NAME, "draft")
    if len(set(items)) != len(items):
        raise _Bad(where, f"\"{where}\" names the same thing twice. Name each once.")
    return items


def _roles(value: Any, problems: list[Problem]) -> tuple[Role, ...]:
    example = ('[{"id": "writer", "label": "Writer", "purpose": "Drafts posts from articles.", '
               '"charter": "roles/writer.md", "tools": ["submit_draft"]}]')
    out: list[Role] = []
    for n, raw in enumerate(_list(value, "roles", _ROLES_MAX, example)):
        at = f"roles[{n}]"
        with _collect(problems):
            obj = _object(raw, at, _ROLE_KEYS, problems, example[1:-1])
            role_id = _name(_need(obj, "id", at, '"writer"'), f"{at}.id", _NAME, "writer")
            label = _text(_need(obj, "label", at, '"Writer"'), f"{at}.label", 40, "Writer")
            purpose = _text(_need(obj, "purpose", at, '"Drafts posts from articles."'),
                            f"{at}.purpose", 200, "Drafts posts from articles.")
            charter = _charter_path(_need(obj, "charter", at, '"roles/writer.md"'),
                                    f"{at}.charter")
            tools = _names_list(_need(obj, "tools", at, '["submit_draft"]'), f"{at}.tools",
                                _LIMITS["tools"], '["submit_draft"]')
            model = obj.get("model", "default")
            if model not in ROLE_MODELS:
                raise _Bad(f"{at}.model", f"\"{at}.model\" must be one of the model roles: "
                                          f"{', '.join(ROLE_MODELS)}. It is {_show(model)}.")
            not_with = _names_list(obj.get("not_with", []), f"{at}.not_with", _ROLES_MAX,
                                   '["checker"]')
            optional = _bool(obj.get("optional", True), f"{at}.optional")
            out.append(Role(id=role_id, label=label, purpose=purpose, charter=charter,
                            tools=tuple(tools), model=model, not_with=tuple(not_with),
                            optional=optional))
    _unique(out, "id", "roles", problems)
    return tuple(out)


def _roles_consistency(got: dict[str, Any]) -> list[Problem]:
    """The roles against the rest of the manifest: rules ``role_tools_are_offered``,
    ``not_with_names_a_role``, ``roles_need_agents`` and ``role_capability_fits``."""
    out: list[Problem] = []
    roles = got.get("roles", ())
    if not roles:
        return out
    if "agents.tools" not in got["permissions"]:
        out.append(Problem("roles", (
            "\"roles\" is declared, and \"permissions\" does not ask for \"agents.tools\": a "
            "role is filled by one of the owner's agents, through your tools. Add "
            "\"agents.tools\" to \"permissions\", or remove \"roles\".")))
    offered = {t.name for t in got.get("tools", ())}
    ids = {r.id for r in roles}
    for n, role in enumerate(roles):
        for k, tool in enumerate(role.tools):
            if tool not in offered:
                out.append(Problem(f"roles[{n}].tools[{k}]", (
                    f"\"roles[{n}].tools[{k}]\" names \"{tool}\", and your app offers no tool "
                    "by that name. List only names from \"tools\", or add the tool there.")))
        for k, other in enumerate(role.not_with):
            if other == role.id:
                out.append(Problem(f"roles[{n}].not_with[{k}]", (
                    f"\"roles[{n}].not_with[{k}]\" names the role itself. Name the other roles "
                    "one agent must not hold together with this one.")))
            elif other not in ids:
                out.append(Problem(f"roles[{n}].not_with[{k}]", (
                    f"\"roles[{n}].not_with[{k}]\" names \"{other}\", and your app has no role "
                    "by that name. Name a role from \"roles\" in this manifest: a role of "
                    "another app cannot be named here.")))
        full = f"app.{got['id']}.{role.id}"
        if len(full) > _ROLE_CAPABILITY_MAX:
            out.append(Problem(f"roles[{n}].id", (
                f"\"roles[{n}].id\" makes the capability \"{full}\", which is longer than "
                f"{_ROLE_CAPABILITY_MAX} characters. Shorten the role's id.")))
    return out


def check_charters(folder: Path, manifest: Manifest) -> list[Problem]:
    """Every role's charter is an ordinary file inside the app (no link on the way), no
    larger than ``CHARTER_MAX_BYTES``, and readable as UTF-8 text."""
    problems: list[Problem] = []
    for n, role in enumerate(manifest.roles):
        where = f"roles[{n}].charter"
        try:
            real = inside(folder, role.charter, where)
        except ManifestError as error:
            problems.extend(error.problems)
            continue
        size = real.stat().st_size
        if size > CHARTER_MAX_BYTES:
            problems.append(Problem(where, (
                f"\"{where}\" is {size} bytes, and a charter can be at most "
                f"{CHARTER_MAX_BYTES}. Shorten it: it is read by the agent every turn.")))
            continue
        try:
            real.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            problems.append(Problem(where, (
                f"\"{where}\" is not readable as UTF-8 text. Save it as plain UTF-8 text.")))
    return problems


# --- tables and views (a described app) -----------------------------------------------------

_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "text": (), "int": ("min", "max"), "number": ("min", "max"), "bool": (), "date": (),
    "choice": ("options",), "money": ("currency", "min", "max"), "link": ("table",),
    "computed": ("formula",),
}
_FIELD_COMMON = ("type", "label", "required", "was")


def _was(value: Any, where: str, own: str) -> list[str]:
    names = _list(value, where, 10, '["old_name"]')
    out = [_name(v, f"{where}[{i}]", _NAME, "old_name") for i, v in enumerate(names)]
    if len(set(out)) != len(out) or own in out:
        raise _Bad(where, f"\"{where}\" lists a name twice or lists the current name. List "
                          "each earlier name once.")
    return out


def _field(raw: Any, at: str, name: str, problems: list[Problem]) -> dict[str, Any]:
    example = '{"type": "text", "required": true}'
    if not isinstance(raw, dict):
        raise _Bad(at, f"\"{at}\" must be an object such as {example}, not {_kind_of(raw)}.")
    kind = _need(raw, "type", at, '"text"')
    if kind not in FIELD_TYPES:
        raise _Bad(f"{at}.type", f"\"{at}.type\" must be one of: {', '.join(FIELD_TYPES)}. "
                                 f"It is {_show(kind)}.")
    obj = _object(raw, at, _FIELD_COMMON + _FIELD_KEYS[kind], problems, example)
    out: dict[str, Any] = {"type": kind}
    if "label" in obj:
        out["label"] = _text(obj["label"], f"{at}.label", 60, "Title")
    if "required" in obj:
        out["required"] = _bool(obj["required"], f"{at}.required")
        if kind == "computed":
            raise _Bad(f"{at}.required", f"\"{at}.required\" does not apply to a computed "
                                         "field, which is never typed in. Remove it.")
    if "was" in obj:
        out["was"] = _was(obj["was"], f"{at}.was", name)
    whole = kind in ("int", "money")
    for bound in ("min", "max"):
        if bound in obj and bound in _FIELD_KEYS[kind]:
            out[bound] = _number(obj[bound], f"{at}.{bound}", whole=whole)
    if "min" in out and "max" in out and out["min"] > out["max"]:
        raise _Bad(f"{at}.min", f"\"{at}.min\" is {out['min']:g}, which is more than its "
                                f"\"max\" of {out['max']:g}. Swap them or correct one.")
    if kind == "choice":
        values = _list(_need(obj, "options", at, '["new", "done"]'), f"{at}.options",
                       _MAX_OPTIONS, '["new", "done"]')
        out["options"] = [_text(v, f"{at}.options[{i}]", 60, "new")
                          for i, v in enumerate(values)]
        if not values or len(set(out["options"])) != len(values):
            raise _Bad(f"{at}.options", f"\"{at}.options\" must list at least one choice, "
                                        "each once.")
    if kind == "money":
        currency = _need(obj, "currency", at, '"EUR"')
        if not isinstance(currency, str) or not _CURRENCY.match(currency):
            raise _Bad(f"{at}.currency", f"\"{at}.currency\" must be a three-letter currency "
                                         f"code in capitals, such as \"EUR\", not "
                                         f"{_show(currency)}. Amounts are whole counts of "
                                         "its smallest unit.")
        out["currency"] = currency
    if kind == "link":
        out["table"] = _name(_need(obj, "table", at, '"book"'), f"{at}.table", _NAME, "book")
    if kind == "computed":
        out["formula"] = _text(_need(obj, "formula", at, '"price * count"'), f"{at}.formula",
                               300, "price * count")
    return out


def _tables(value: Any, problems: list[Problem]) -> dict[str, Any]:
    example = '{"book": {"fields": {"title": {"type": "text", "required": true}}}}'
    if not isinstance(value, dict):
        raise _Bad("tables", f"\"tables\" must be an object such as {example}, not "
                             f"{_kind_of(value)}.")
    if len(value) > _LIMITS["tables"]:
        raise _Bad("tables", f"\"tables\" has {len(value)} tables. Keep it to "
                             f"{_LIMITS['tables']} or fewer.")
    out: dict[str, Any] = {}
    for name, raw in value.items():
        at = f"tables.{_show(name)}"
        with _collect(problems):
            _name(name, at, _NAME, "book")
            obj = _object(raw, at, ("label", "was", "fields"), problems, example[9:-1])
            table: dict[str, Any] = {}
            if "label" in obj:
                table["label"] = _text(obj["label"], f"{at}.label", 60, "Books")
            if "was" in obj:
                table["was"] = _was(obj["was"], f"{at}.was", name)
            fields = _need(obj, "fields", at, '{"title": {"type": "text"}}')
            if not isinstance(fields, dict) or not fields:
                raise _Bad(f"{at}.fields", f"\"{at}.fields\" must be an object with at least "
                                           "one field, such as {\"title\": {\"type\": "
                                           "\"text\"}}.")
            if len(fields) > _MAX_FIELDS:
                raise _Bad(f"{at}.fields", f"\"{at}.fields\" has {len(fields)} fields. Keep "
                                           f"it to {_MAX_FIELDS} or fewer.")
            table["fields"] = {}
            before = len(problems)
            for field_name, field_raw in fields.items():
                field_at = f"{at}.fields.{_show(field_name)}"
                with _collect(problems):
                    _name(field_name, field_at, _NAME, "title")
                    if field_name == "id":
                        raise _Bad(field_at, f"\"{field_at}\" cannot be called \"id\": every "
                                             "record has one already. Choose another name.")
                    table["fields"][field_name] = _field(field_raw, field_at, field_name,
                                                         problems)
            if len(problems) == before:
                out[name] = table
    for name, table in out.items():
        for field_name, spec in table["fields"].items():
            if spec["type"] == "link" and spec["table"] not in value:
                at = f"tables.{name}.fields.{field_name}.table"
                problems.append(Problem(at, (
                    f"\"{at}\" is \"{spec['table']}\", and there is no table of that name. "
                    f"Use one of: {', '.join(sorted(map(str, value)))}.")))
    return out


def _view(raw: Any, at: str, tables: dict[str, Any], all_tables: Any,
          problems: list[Problem]) -> dict[str, Any] | None:
    example = '{"id": "all", "label": "All", "table": "book"}'
    obj = _object(raw, at, ("id", "label", "table", "fields", "filter", "sort", "group",
                            "totals"), problems, example)
    out: dict[str, Any] = {
        "id": _name(_need(obj, "id", at, '"all"'), f"{at}.id", _NAME, "all"),
        "label": _text(_need(obj, "label", at, '"All"'), f"{at}.label", 60, "All"),
        "table": _name(_need(obj, "table", at, '"book"'), f"{at}.table", _NAME, "book"),
    }
    if out["table"] not in tables:
        if isinstance(all_tables, dict) and out["table"] in all_tables:
            return None  # the table itself was refused above; one sentence is enough
        known = ", ".join(sorted(tables)) or "none yet: add it under \"tables\""
        raise _Bad(f"{at}.table", f"\"{at}.table\" is \"{out['table']}\", and there is no "
                                  f"table of that name. The tables are: {known}.")
    fields = tables[out["table"]]["fields"]

    def known_field(value: Any, where: str) -> str:
        if not isinstance(value, str) or value not in fields:
            raise _Bad(where, f"\"{where}\" names {_show(value)}, which is not a field of "
                              f"the table \"{out['table']}\". Its fields are: "
                              f"{', '.join(fields)}.")
        return value

    if "fields" in obj:
        names = _list(obj["fields"], f"{at}.fields", _MAX_FIELDS, '["title"]')
        out["fields"] = [known_field(v, f"{at}.fields[{i}]") for i, v in enumerate(names)]
        if not names or len(set(out["fields"])) != len(names):
            raise _Bad(f"{at}.fields", f"\"{at}.fields\" must list at least one field, each "
                                       "once. Remove it to show every field.")
    if "filter" in obj:
        out["filter"] = []
        rules = _list(obj["filter"], f"{at}.filter", 10,
                      '[{"field": "stars", "op": "ge", "value": 4}]')
        for i, rule in enumerate(rules):
            rule_at = f"{at}.filter[{i}]"
            rule_obj = _object(rule, rule_at, ("field", "op", "value"), problems,
                               '{"field": "stars", "op": "ge", "value": 4}')
            one: dict[str, Any] = {
                "field": known_field(_need(rule_obj, "field", rule_at, '"stars"'),
                                     f"{rule_at}.field")}
            op = _need(rule_obj, "op", rule_at, '"eq"')
            if op not in FILTER_OPS:
                raise _Bad(f"{rule_at}.op", f"\"{rule_at}.op\" must be one of: "
                                            f"{', '.join(FILTER_OPS)}. It is {_show(op)}.")
            one["op"] = op
            if op in ("empty", "not_empty"):
                if "value" in rule_obj:
                    raise _Bad(f"{rule_at}.value", f"\"{rule_at}.value\" does not apply to "
                                                   f"\"{op}\". Remove it.")
            else:
                value = _need(rule_obj, "value", rule_at, "4")
                if isinstance(value, bool):
                    pass
                elif isinstance(value, str):
                    _text(value, f"{rule_at}.value", 200, "done", empty=True)
                else:
                    _number(value, f"{rule_at}.value")
                one["value"] = value
            out["filter"].append(one)
    if "sort" in obj:
        keys = _list(obj["sort"], f"{at}.sort", 5, '["-finished"]')
        out["sort"] = []
        for i, key in enumerate(keys):
            bare = key[1:] if isinstance(key, str) and key.startswith("-") else key
            known_field(bare, f"{at}.sort[{i}]")
            out["sort"].append(key)
    if "group" in obj:
        out["group"] = known_field(obj["group"], f"{at}.group")
    if "totals" in obj:
        totals = obj["totals"]
        if not isinstance(totals, dict) or len(totals) > _MAX_FIELDS:
            raise _Bad(f"{at}.totals", f"\"{at}.totals\" must be an object of field and "
                                       "total, such as {\"stars\": \"avg\"}.")
        out["totals"] = {}
        for name, how in totals.items():
            known_field(name, f"{at}.totals.{_show(name)}")
            if how not in TOTALS:
                raise _Bad(f"{at}.totals.{name}", f"\"{at}.totals.{name}\" must be one of: "
                                                  f"{', '.join(TOTALS)}. It is {_show(how)}.")
            out["totals"][name] = how
    return out


def _views(value: Any, tables: dict[str, Any], all_tables: Any,
           problems: list[Problem]) -> tuple[dict[str, Any], ...]:
    out: list[dict[str, Any]] = []
    example = '[{"id": "all", "label": "All", "table": "book"}]'
    for n, raw in enumerate(_list(value, "views", _LIMITS["views"], example)):
        before = len(problems)
        with _collect(problems):
            view = _view(raw, f"views[{n}]", tables, all_tables, problems)
            if view is not None and len(problems) == before:
                out.append(view)
    ids = [v["id"] for v in out]
    for view_id in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(Problem("views", f"\"views\" has two entries with the id "
                                         f"\"{view_id}\". Give each its own id."))
    return tuple(out)


# --- the whole manifest ---------------------------------------------------------------------


def _decode(source: Any) -> dict[str, Any]:
    """The manifest's top-level object from text or bytes, strictly read."""
    if isinstance(source, dict):
        return source
    if isinstance(source, bytes | bytearray):
        if len(source) > MAX_BYTES:
            raise _Bad("", f"The manifest is larger than {MAX_BYTES // 1024} KB. A manifest "
                           "describes the app; move anything else into the app's own files.")
        try:
            source = bytes(source).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise _Bad("", "The manifest is not UTF-8 text. Save it as UTF-8.") from None
    if not isinstance(source, str):
        raise _Bad("", "The manifest must be JSON text holding one object.")
    if len(source) > MAX_BYTES:
        raise _Bad("", f"The manifest is larger than {MAX_BYTES // 1024} KB. A manifest "
                       "describes the app; move anything else into the app's own files.")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise _Bad(_show(key), f"\"{_show(key)}\" is written twice in the same "
                                       "object. Keep one.")
            out[key] = value
        return out

    def constant(name: str) -> Any:
        raise _Bad("", f"The manifest holds {name}, which is not a JSON number. Write an "
                       "ordinary number.")

    if too_deep(source):
        raise _Bad("", "The manifest is nested too deeply to read. Flatten it.")
    try:
        data = json.loads(source.removeprefix("﻿"), object_pairs_hook=pairs,
                          parse_constant=constant)
    except json.JSONDecodeError as error:
        raise _Bad("", f"The manifest is not valid JSON (line {error.lineno}, column "
                       f"{error.colno}: {error.msg}). Correct it there.") from None
    except RecursionError:
        raise _Bad("", "The manifest is nested too deeply to read. Flatten it.") from None
    if not isinstance(data, dict):
        raise _Bad("", "The manifest must be one JSON object, starting {\"manifest\": 1, "
                       f"...}}, not {_kind_of(data)}.")
    return data


def parse(source: dict[str, Any] | str | bytes, *, api: tuple[int, int] = APP_API) -> Manifest:
    """Read a manifest from JSON text, bytes or already-decoded data.

    Raises ``ManifestError`` listing every mistake found. ``api`` is the app API the core
    serves. Nothing on disk is read: use ``load`` to check the files a manifest names."""
    problems: list[Problem] = []
    try:
        data = _decode(source)
    except _Bad as bad:
        raise ManifestError([bad.problem]) from None

    got: dict[str, Any] = {}
    tier = data.get("tier") if data.get("tier") in TIERS else None

    for key in data:
        if not isinstance(key, str) or key not in _TOP:
            problems.append(Problem(_show(key), (
                f"\"{_show(key)}\" is not a field JimmyHQ knows, and unknown fields are "
                f"refused. Remove it; the fields of a manifest are: {', '.join(_TOP)}.")))
    for key in _REQUIRED:
        if key not in data:
            problems.append(Problem(key, _MISSING_SENTENCES[key]))

    def read(key: str, fn: Any) -> None:
        if key in data:
            with _collect(problems):
                got[key] = fn(data[key])

    def version_number(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _Bad("manifest", "\"manifest\" must be the format version as a whole "
                                   f"number, {MANIFEST_VERSION}, not {_kind_of(value)}.")
        if value != MANIFEST_VERSION:
            raise _Bad("manifest", f"\"manifest\" is {value}, and this JimmyHQ reads format "
                                   f"{MANIFEST_VERSION} only. Write {MANIFEST_VERSION}, or "
                                   "update JimmyHQ if the app needs a newer format.")
        return value

    def tier_of(value: Any) -> str:
        if value not in TIERS:
            raise _Bad("tier", f"\"tier\" must be \"{SANDBOXED}\" or \"{FULL}\", not "
                               f"{_show(value)}.")
        return value

    read("manifest", version_number)
    read("id", _id)
    read("name", lambda v: _text(v, "name", 60, "Water"))
    read("summary", lambda v: _text(v, "summary", 200, "Count glasses of water."))
    read("author", lambda v: _text(v, "author", 100, "A. Writer", empty=True))
    read("icon", _icon)
    read("licence", lambda v: _text(v, "licence", 60, "MIT"))
    read("version", _version)
    read("tier", tier_of)
    read("api", lambda v: _api(v, api))

    if tier is not None:
        for key in FULL_ONLY:
            if key in data and tier != FULL:
                problems.append(Problem(key, (
                    f"\"{key}\" is only for a full app, and this app is {tier}: a "
                    f"{tier} app has no code or plug points on the server. Remove it, or "
                    f"set \"tier\" to \"{FULL}\".")))

    def permissions(value: Any) -> tuple[str, ...]:
        items = _list(value, "permissions", 200, '["tasks.read"]')
        if tier is None:
            return ()
        out: list[str] = []
        for n, text in enumerate(items):
            at = f"permissions[{n}]"
            try:
                grant = perms.parse_permission(text)
                perms.check_for_tier(grant, tier)
            except PermissionRefused as refused:
                problems.append(Problem(at, f"\"{at}\": {refused}"))
                continue
            if grant.text in out:
                problems.append(Problem(at, f"\"{at}\": \"{grant.text}\" is listed twice. "
                                            "List it once."))
                continue
            out.append(grant.text)
        try:
            perms.check_all(out, tier)
        except PermissionRefused as refused:
            raise _Bad("permissions", f"\"permissions\": {refused}") from None
        return tuple(out)

    read("permissions", permissions)
    for name in ("screens", "dock", "wall"):
        read(name, lambda v, name=name: _screens(v, name, problems))
    if "id" in got:
        read("settings", lambda v: _settings(v, got["id"], problems))
    read("secrets", lambda v: _secrets(v, problems))
    read("tables", lambda v: _tables(v, problems))
    if "views" in data:
        if "tables" in got or "tables" not in data:
            read("views", lambda v: _views(v, got.get("tables", {}), data.get("tables"),
                                           problems))
    if tier == FULL:
        read("server", lambda v: _server(v, problems))
        read("cards", lambda v: _cards(v, problems))
        read("memory_sources", lambda v: _memory_sources(v, problems))
        read("tools", lambda v: _tools(v, problems))
        read("private", lambda v: _bool(v, "private"))
        read("card_subjects", _card_subjects)
        read("roles", lambda v: _roles(v, problems))
        read("feeds", lambda v: _feeds(v, problems))
        read("inbound", lambda v: _inbound(v, problems))

    read("storage_mb", _storage_mb)
    read("owner_hosts", check_owner_hosts)
    read("net_expects", _net_expects)

    if not problems:
        problems.extend(_consistency(got))
    if problems:
        raise ManifestError(problems)
    return Manifest(**got)


_MISSING_SENTENCES = {
    "manifest": f"\"manifest\" is missing. Add the format version: \"manifest\": "
                f"{MANIFEST_VERSION}.",
    "id": "\"id\" is missing. Add a short lower-case id, such as \"id\": \"water\".",
    "name": "\"name\" is missing. Add the app's name, such as \"name\": \"Water\".",
    "summary": "\"summary\" is missing. Add one line that says what the app does, such as "
               "\"summary\": \"Count glasses of water.\".",
    "licence": "\"licence\" is missing. Add the licence the app is shared under, such as "
               "\"licence\": \"MIT\".",
    "version": "\"version\" is missing. Add it, such as \"version\": \"1.0.0\".",
    "tier": f"\"tier\" is missing. Add \"tier\": \"{SANDBOXED}\" for an app that runs in "
            f"the page only, or \"tier\": \"{FULL}\" for one with its own server code.",
    "api": f"\"api\" is missing. Add the app API it was written for, such as \"api\": "
           f"\"{APP_API[0]}.{APP_API[1]}\".",
    "permissions": "\"permissions\" is missing. List what the app asks for, such as "
                   "\"permissions\": [\"tasks.read\"], or [] if it asks for nothing.",
}


def _consistency(got: dict[str, Any]) -> list[Problem]:
    """Rules that span fields. Each is a sentence about the pair that disagrees."""
    out: list[Problem] = []
    tier, app_id, asked = got["tier"], got["id"], got["permissions"]
    shown = got.get("screens", ()) + got.get("dock", ()) + got.get("wall", ())

    if tier == SANDBOXED and not shown:
        out.append(Problem("screens", (
            "\"screens\" is missing: a sandboxed app only runs in a page, so with no "
            "screen, dock tab or tile it would do nothing. Add one, such as \"screens\": "
            "[{\"id\": \"main\", \"label\": \"Water\", \"entry\": \"index.html\"}].")))
    if tier == FULL and "server" not in got:
        out.append(Problem("server", (
            "\"server\" is missing: a full app is one with its own server code. Add it, "
            "such as \"server\": {\"entry\": \"posts.main:app\"}, or set \"tier\" to "
            f"\"{SANDBOXED}\" if the app has none.")))

    described = any(s.described for s in shown)
    if described and not (got.get("tables") and got.get("views")):
        out.append(Problem("views", (
            f"A screen has \"entry\": \"{DESCRIBED}\", which JimmyHQ draws from \"tables\" "
            "and \"views\", and this manifest does not have both. Add at least one table "
            "and one view, or point the screen at an HTML file.")))
    if got.get("views") and not got.get("tables"):
        out.append(Problem("views", "\"views\" needs \"tables\" to show. Add the tables, or "
                                    "remove \"views\"."))

    pairs = (("tools", "agents.tools"), ("memory_sources", "memory.source"),
             ("inbound", "inbound"))
    for name, permission in pairs:
        if got.get(name) and permission not in asked:
            out.append(Problem(name, (
                f"\"{name}\" is declared, and \"permissions\" does not ask for "
                f"\"{permission}\". Add \"{permission}\" to \"permissions\", or remove "
                f"\"{name}\".")))
        if permission in asked and not got.get(name):
            out.append(Problem("permissions", (
                f"\"permissions\" asks for \"{permission}\", and \"{name}\" declares "
                f"nothing to offer. List them under \"{name}\", or remove the permission.")))
    if got.get("cards") and "bench.file" not in asked:
        out.append(Problem("cards", (
            "\"cards\" is declared, and \"permissions\" does not ask for \"bench.file\". "
            "Add \"bench.file\" to \"permissions\", or remove \"cards\".")))
    if got.get("card_subjects") and not (got.get("private") and got.get("cards")):
        out.append(Problem("card_subjects", (
            "\"card_subjects\" is declared, and it is only for a private app that files "
            "cards: it names the words such an app's cards may carry as a subject. Set "
            "\"private\" to true and declare \"cards\", or remove \"card_subjects\".")))
    clash = [p for p in ("memory.source", "agents.tools") if p in asked]
    if got.get("private") and clash:
        listed = " and ".join(f"\"{p}\"" for p in clash)
        out.append(Problem("private", (
            "\"private\" is true, and a private app's data is never offered to Memory or "
            f"shown to your agents, so it cannot also ask for {listed}. Remove one or the "
            "other.")))

    out.extend(_roles_consistency(got))
    out.extend(_feeds_consistency(got))
    named = perms.hosts(asked)
    for expect in got.get("net_expects", ()):
        if expect.host not in named:
            out.append(Problem(f"net_expects.{_show(expect.host)}", (
                f"\"net_expects\" says what the app expects of {_show(expect.host)}, and "
                f"\"permissions\" does not ask for \"net:{_show(expect.host)}\". Add it to "
                "\"permissions\", or remove the host from \"net_expects\".")))
    for n, secret in enumerate(got.get("secrets", ())):
        if secret.per_host and got.get("owner_hosts") is None:
            out.append(Problem(f"secrets[{n}].for_each", (
                f"\"secrets[{n}]\" is kept for each host the owner grants, and the app "
                "does not declare \"owner_hosts\", so there would be none. Add "
                "\"owner_hosts\", or remove \"for_each\".")))
    for n, tool in enumerate(got.get("tools", ())):
        full = f"app_{app_id}_{tool.name}"
        if len(full) > _TOOL_NAME_MAX:
            out.append(Problem(f"tools[{n}].name", (
                f"\"tools[{n}].name\" makes the tool \"{full}\", which is longer than "
                f"{_TOOL_NAME_MAX} characters. Shorten the name to "
                f"{_TOOL_NAME_MAX - len(full) + len(tool.name)} characters or fewer.")))
    for n, source in enumerate(got.get("memory_sources", ())):
        full = f"app_{app_id}_{source.id}"
        if len(full) > _SOURCE_ID_MAX:
            room = _SOURCE_ID_MAX - len(full) + len(source.id)
            fix = (f"Shorten the id to {room} characters or fewer." if room >= 1
                   else "Shorten the app's \"id\".")
            out.append(Problem(f"memory_sources[{n}].id", (
                f"\"memory_sources[{n}].id\" makes the source \"{full}\", which is longer "
                f"than {_SOURCE_ID_MAX} characters. {fix}")))
    return out


def check_alongside(manifest: Manifest, installed: Iterable[Manifest]) -> None:
    """Refuse an app whose names would clash with another installed app's.

    An installed app with the same id is the one being updated and is skipped. Kinds,
    settings and secrets cannot clash (``app.<id>.`` ends in a dot), but tools and memory
    sources are joined with underscores, which an id may also contain."""
    problems: list[Problem] = []
    mine = {manifest.tool_name(t.name): f"tools[{n}].name"
            for n, t in enumerate(manifest.tools)}
    mine_sources = {manifest.source_id(s.id): f"memory_sources[{n}].id"
                    for n, s in enumerate(manifest.memory_sources)}
    for other in installed:
        if other.id == manifest.id:
            continue
        theirs = {other.tool_name(t.name) for t in other.tools}
        for full in sorted(mine.keys() & theirs):
            problems.append(Problem(mine[full], (
                f"\"{mine[full]}\" makes the tool \"{full}\", which the installed app "
                f"\"{other.name}\" ({other.id}) already offers. Rename the tool.")))
        their_sources = {other.source_id(s.id) for s in other.memory_sources}
        for full in sorted(mine_sources.keys() & their_sources):
            problems.append(Problem(mine_sources[full], (
                f"\"{mine_sources[full]}\" makes the source \"{full}\", which the installed "
                f"app \"{other.name}\" ({other.id}) already offers. Rename the source.")))
    if problems:
        raise ManifestError(problems)


def load(folder: Path, *, api: tuple[int, int] = APP_API) -> Manifest:
    """Read and parse the manifest in an app folder, then check that every file it names
    is an ordinary file inside that folder. Nothing in the folder is run or imported."""
    folder = Path(folder)
    path = folder / MANIFEST_FILE
    if path.is_symlink():
        raise ManifestError([Problem("", (
            f"{MANIFEST_FILE} is a link, and links are refused. Put the real file in the "
            "app folder."))])
    if not path.is_file():
        raise ManifestError([Problem("", (
            f"There is no {MANIFEST_FILE} in this folder, so it is not a JimmyHQ app. Point "
            "at the folder that holds it."))])
    if path.stat().st_size > MAX_BYTES:
        raise ManifestError([Problem("", (
            f"{MANIFEST_FILE} is larger than {MAX_BYTES // 1024} KB. A manifest describes "
            "the app; move anything else into the app's own files."))])
    manifest = parse(path.read_bytes(), api=api)
    problems: list[Problem] = []
    for name in ("screens", "dock", "wall"):
        for n, screen in enumerate(getattr(manifest, name)):
            if not screen.described:
                try:
                    inside(folder, screen.entry, f"{name}[{n}].entry")
                except ManifestError as error:
                    problems.extend(error.problems)
    problems.extend(check_charters(folder, manifest))
    if manifest.server is not None:
        base = manifest.server.module.replace(".", "/")
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            try:
                inside(folder, candidate, "server.entry")
                break
            except ManifestError:
                continue
        else:
            problems.append(Problem("server.entry", (
                f"\"server.entry\" names the module \"{manifest.server.module}\", and there "
                f"is no \"{base}.py\" in the app (or it is a link, which is refused). Add "
                "the file, or correct the entry.")))
    if problems:
        raise ManifestError(problems)
    return manifest
