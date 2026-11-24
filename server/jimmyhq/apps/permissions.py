"""What an app may ask for, and the words the owner reads before saying yes.

Permissions are data. ``TABLE`` has one row per permission: its id, the tiers that may ask
for it, whether it takes a host, and the sentence the install dialog shows. Everything
else here reads that table:

    lines = describe(manifest)        # the dialog's sentences, most sensitive first
    change = diff(old, new)           # what an update adds or widens
    if change.needs_consent: ...      # ask again, showing change.ask
    mark = fingerprint(manifest)      # compare with what the owner agreed to

A manifest writes a permission as its id (``"tasks.write"``), or as ``net:<host>`` or
``feed:<app>.<feed>`` for the two permissions that name something. A host is one exact
public name: no wildcard, no address, no port, nothing local. A feed is one feed of one
other app (``feeds.py``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol

SANDBOXED = "sandboxed"
FULL = "full"
TIERS = (SANDBOXED, FULL)

MAX_PERMISSIONS = 32
MAX_HOSTS = 16

# Lines the dialog always shows. An app cannot ask for these: JimmyHQ adds them itself.
OWN_DATA = "always.data"
RUNS_AS_YOU = "always.full"
# Shown only by ``diff``, when an update drops the manifest's ``private`` promise.
PRIVATE_OFF = "private.off"
# The line for a manifest's ``owner_hosts``: never written in ``permissions``.
OWNER_HOSTS = "owner_hosts"


class PermissionRefused(ValueError):
    """A permission as written cannot be accepted. The message is a sentence that says
    what to write instead."""


@dataclass(frozen=True)
class Permission:
    id: str
    tiers: tuple[str, ...]
    sentence: str  # "{host}" marks where the named host goes
    param: str | None = None  # "host" or "feed" for a permission that names one
    always: bool = False  # shown for every app of its tiers; never asked for
    strong: bool = False  # the dialog sets this line apart as a warning


# Ordered from most to least sensitive: the dialog shows lines in this order.
TABLE: tuple[Permission, ...] = (
    Permission(RUNS_AS_YOU, (FULL,),
               "Runs as you on this computer. It can read and change your files and use "
               "the network. JimmyHQ cannot limit this.", always=True, strong=True),
    Permission("tape.read", (FULL,), "Read your whole record, not only its own events."),
    Permission("feed", (FULL,),
               "Read the feed \"{feed}\" that the app \"{app}\" offers, and nothing else of "
               "its or on your record.", param="feed"),
    Permission("net", TIERS, "Send data to and read data from {host}.", param="host"),
    Permission("web", TIERS,
               "Search the web and read public pages, under your web lookup settings. What "
               "it searches for, and the addresses it reads, leave this computer."),
    Permission("model.ask", TIERS,
               "Ask your AI models, within your roles and limits, and show them pictures "
               "from its own data. If a role uses a hosted model, what it asks leaves this "
               "computer."),
    Permission("memory.recall", TIERS, "Search what JimmyHQ remembers about you."),
    Permission("speech.dictate", TIERS,
               "Ask you to dictate: you see when it is recording, and it gets only the "
               "words."),
    Permission("agents.tools", (FULL,), "Offer tools to your agents. You choose which agents."),
    Permission("memory.source", (FULL,),
               "Offer what it collects to Memory. You switch each source on yourself."),
    Permission("inbound", (FULL,),
               "Take messages from devices and services on your network, such as your "
               "home's hub. Each one needs a key you make for it on the app's page."),
    Permission("jobs", (FULL,), "Work in the background on a schedule."),
    Permission("bench.file", TIERS,
               "Put cards on your Bench for you to decide. It cannot decide for you."),
    Permission("tasks.write", TIERS, "Add tasks, and mark the ones it added as done."),
    Permission("tasks.read", TIERS, "See your tasks."),
    Permission(OWN_DATA, TIERS, "Keep its own data on this computer.", always=True),
)

BY_ID: dict[str, Permission] = {p.id: p for p in TABLE}
_RANK: dict[str, int] = {p.id: n for n, p in enumerate(TABLE)}
ASKABLE: tuple[str, ...] = tuple(p.id for p in TABLE if not p.always)
# The sentence for a manifest's ``owner_hosts``. It is not a row of ``TABLE``: an app does
# not write it in "permissions", and the dialog shows it just after the hosts it names.
OWNER_HOSTS_SENTENCE = (
    "It may ask you for the address of your own site, and will be able to send to and "
    "read from the addresses you give it, {most} at most. It says why: \"{purpose}\"")

_PRIVATE_OFF_SENTENCE = (
    "Stop treating what it holds as private. It may write to your record, and its model "
    "calls may keep their text."
)


@dataclass(frozen=True)
class Grant:
    """One permission as a manifest writes it: the table row and the host, if it names one."""

    permission: str
    host: str | None = None  # what it names: a host, or ``<app>.<feed>`` for a feed

    @property
    def text(self) -> str:
        return self.permission if self.host is None else f"{self.permission}:{self.host}"


@dataclass(frozen=True)
class Line:
    """One sentence of the consent dialog."""

    id: str  # as the manifest writes it ("net:api.example.com"), or an always-line id
    permission: str  # the table row: "net", "tasks.read", "always.full"
    host: str | None
    sentence: str
    strong: bool = False
    always: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Diff:
    """What an update changes in what the owner agreed to."""

    added: tuple[Line, ...]  # new lines, a newly named host included
    widened: tuple[Line, ...]  # a promise the old version made and the new one drops
    removed: tuple[Line, ...]
    tier: tuple[str, str] | None  # (old, new) when the tier changes

    @property
    def ask(self) -> tuple[Line, ...]:
        """The lines the update dialog lists: only what is new, most sensitive first."""
        return tuple(sorted(self.added + self.widened, key=_order))

    @property
    def needs_consent(self) -> bool:
        return bool(self.added or self.widened or self.tier)

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": [x.as_dict() for x in self.added],
            "widened": [x.as_dict() for x in self.widened],
            "removed": [x.as_dict() for x in self.removed],
            "tier": list(self.tier) if self.tier else None,
            "ask": [x.as_dict() for x in self.ask],
            "needs_consent": self.needs_consent,
        }


class Asks(Protocol):
    """What these functions need of a manifest. ``owner_hosts`` (its ``purpose`` and
    ``max``, or None) is read where it is there."""

    id: str
    tier: str
    permissions: tuple[str, ...]
    private: bool


# --- hosts ----------------------------------------------------------------------------------

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\Z")
_TLD = re.compile(r"^([a-z]{2,63}|xn--[a-z0-9-]{1,59})\Z")
# Names that only mean something on one network or one computer. "example" is not here:
# it is reserved for documentation and reaches nothing.
_LOCAL_SUFFIXES = (
    "localhost", "local", "localdomain", "lan", "home", "internal", "intranet", "corp",
    "private", "arpa", "onion", "test", "invalid", "ts.net",
)
_EXAMPLE_HOST = "api.example.com"


def _host_refusal(host: str, why: str) -> PermissionRefused:
    return PermissionRefused(
        f"\"net:{host}\" cannot be used: {why} Write one exact public host name, such as "
        f"\"net:{_EXAMPLE_HOST}\".")


def check_host(host: str) -> str:
    """The host if it is one exact public name, or ``PermissionRefused``."""
    if not host:
        raise PermissionRefused(
            "\"net\" must name the host it reaches. Write one exact public host name after a "
            f"colon, such as \"net:{_EXAMPLE_HOST}\".")
    if len(host) > 253:
        raise _host_refusal(host[:40] + "...", "a host name is at most 253 characters.")
    if "*" in host:
        raise _host_refusal(host, "a wildcard is not allowed. List each host on its own line.")
    if "://" in host or "/" in host or "?" in host or "#" in host or "@" in host:
        raise _host_refusal(host, "it is an address, not a host name. Leave out the scheme, "
                                  "the path and any sign-in part; https is always used.")
    if "[" in host or "]" in host or host.count(":") > 0:
        raise _host_refusal(host, "a port or a numeric address is not allowed. The standard "
                                  "https port is always used.")
    if host != host.lower():
        raise _host_refusal(host, "a host name is written in lower case.")
    if not host.isascii():
        raise _host_refusal(host, "it has letters outside a to z. Write the name in its "
                                  "\"xn--\" form.")
    if host.endswith("."):
        raise _host_refusal(host, "it ends with a dot.")
    labels = host.split(".")
    if len(labels) < 2:
        raise _host_refusal(host, "a name with no dot only works on a local network.")
    if any(host == s or host.endswith("." + s) for s in _LOCAL_SUFFIXES):
        raise _host_refusal(host, "it is a local or private name, which an app may not reach.")
    if not _TLD.match(labels[-1]):
        raise _host_refusal(host, "it is a numeric address or ends in something that is not "
                                  "a public domain.")
    if not all(_LABEL.match(label) for label in labels):
        raise _host_refusal(host, "each part between dots is 1 to 63 letters, digits or "
                                  "hyphens, and does not start or end with a hyphen.")
    return host


# --- feeds ----------------------------------------------------------------------------------

_APP_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}\Z")
_FEED_NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}\Z")
_EXAMPLE_FEED = "reader.articles"


def check_feed_ref(ref: str) -> str:
    """Rule ``feed_names_one_feed``: ``<app>.<feed>``, one app's id and one of its feeds'
    names, or ``PermissionRefused``. No wildcard: each feed is asked for on its own."""
    app_id, dot, feed = (ref or "").partition(".")
    if not ref:
        raise PermissionRefused(
            "\"feed\" must name the app and the feed it reads. Write them after a colon, "
            f"such as \"feed:{_EXAMPLE_FEED}\".")
    if "*" in ref:
        raise PermissionRefused(
            f"\"feed:{ref[:60]}\" cannot be used: a wildcard is not allowed. Name each feed "
            f"on its own line, such as \"feed:{_EXAMPLE_FEED}\".")
    if not dot or not _APP_ID.match(app_id) or not _FEED_NAME.match(feed):
        raise PermissionRefused(
            f"\"feed:{ref[:60]}\" cannot be used: write the app's id, a dot, and the name of "
            f"one of its feeds, in lower case, such as \"feed:{_EXAMPLE_FEED}\".")
    return ref


def feed_ref(grant_text: str) -> tuple[str, str] | None:
    """``("reader", "articles")`` for ``"feed:reader.articles"``; None for anything else."""
    name, _, ref = grant_text.partition(":")
    if name != "feed" or "." not in ref:
        return None
    app_id, _, feed = ref.partition(".")
    return app_id, feed


def feeds(granted: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Every feed the permissions name, as ``(app, feed)``, sorted."""
    return tuple(sorted(f for f in (feed_ref(g) for g in granted) if f is not None))


# --- reading what a manifest wrote ----------------------------------------------------------


def parse_permission(text: str) -> Grant:
    """One permission as written, checked against the table, or ``PermissionRefused``."""
    if not isinstance(text, str):
        raise PermissionRefused(
            f"A permission must be text such as \"tasks.read\", not {_kind_of(text)}.")
    name, colon, param = text.partition(":")
    row = BY_ID.get(name)
    if name == OWNER_HOSTS:
        raise PermissionRefused(
            f"\"{name}\" is not written in \"permissions\": declare it as its own key, "
            "such as \"owner_hosts\": {\"purpose\": \"Publish drafts to your own "
            "site.\", \"max\": 3}.")
    if row is not None and row.always:
        raise PermissionRefused(
            f"\"{name}\" is not something an app asks for: JimmyHQ shows that line itself. "
            "Remove it.")
    if row is None:
        shown = text if len(text) <= 60 else text[:60] + "..."
        raise PermissionRefused(
            f"\"{shown}\" is not a permission JimmyHQ knows. Use one of: "
            f"{', '.join(_written(p) for p in ASKABLE)}.")
    if row.param is None:
        if colon:
            raise PermissionRefused(
                f"\"{name}\" takes nothing after a colon. Write it as \"{name}\".")
        return Grant(name)
    if row.param == "feed":
        return Grant(name, check_feed_ref(param))
    return Grant(name, check_host(param))


def check_for_tier(grant: Grant, tier: str) -> None:
    """Raise ``PermissionRefused`` if an app of this tier may not ask for the permission."""
    row = BY_ID[grant.permission]
    if tier not in row.tiers:
        raise PermissionRefused(
            f"\"{grant.text}\" is only for a {' or '.join(row.tiers)} app, and this app is "
            f"{tier}. Remove it, or make the app {row.tiers[0]}.")


def check_all(permissions: Iterable[str], tier: str) -> tuple[str, ...]:
    """Every permission checked for syntax, tier and repeats. Returns them as written.

    Raises ``PermissionRefused`` for the first one that fails."""
    seen: list[str] = []
    for text in permissions:
        grant = parse_permission(text)
        check_for_tier(grant, tier)
        if grant.text in seen:
            raise PermissionRefused(f"\"{grant.text}\" is listed twice. List it once.")
        seen.append(grant.text)
    if len(seen) > MAX_PERMISSIONS:
        raise PermissionRefused(
            f"An app can ask for at most {MAX_PERMISSIONS} permissions, and this lists "
            f"{len(seen)}. Remove some.")
    if len(hosts(seen)) > MAX_HOSTS:
        raise PermissionRefused(
            f"An app can name at most {MAX_HOSTS} hosts, and this names {len(hosts(seen))}. "
            "Remove some.")
    return tuple(seen)


def allows(granted: Iterable[str], permission: str, *, host: str | None = None) -> bool:
    """Whether what was granted covers a permission; for ``net``, this exact host."""
    wanted = permission if host is None else f"{permission}:{host}"
    return wanted in set(granted)


def hosts(granted: Iterable[str]) -> tuple[str, ...]:
    """The hosts the ``net`` permissions name, sorted."""
    return tuple(sorted(g.partition(":")[2] for g in granted if g.startswith("net:")))


# --- the dialog's words ---------------------------------------------------------------------


def _line(grant: Grant) -> Line:
    row = BY_ID[grant.permission]
    if row.param == "feed":
        app_id, _, feed = (grant.host or "").partition(".")
        sentence = row.sentence.format(app=app_id, feed=feed)
    elif row.param:
        sentence = row.sentence.format(host=grant.host)
    else:
        sentence = row.sentence
    return Line(id=grant.text, permission=row.id, host=grant.host, sentence=sentence,
                strong=row.strong, always=row.always)


def _order(line: Line) -> tuple[int, str]:
    if line.permission == OWNER_HOSTS:
        return (_RANK["net"], "\uffff")  # just after the hosts the manifest names
    return (_RANK.get(line.permission, -1), line.host or "")


def ordered(lines: Iterable[Line]) -> list[Line]:
    """Lines in the order the dialog shows them: most sensitive first."""
    return sorted(lines, key=_order)


def describe_permissions(tier: str, permissions: Iterable[str]) -> list[Line]:
    """The dialog's lines for a tier and its permissions, most sensitive first."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    lines = [_line(Grant(p.id)) for p in TABLE if p.always and tier in p.tiers]
    lines += [_line(parse_permission(text)) for text in dict.fromkeys(permissions)]
    return sorted(lines, key=_order)


def owner_hosts_line(manifest: Any) -> Line | None:
    """Rule ``owner_hosts_is_its_own_sentence``: the line the dialog shows for a manifest
    that may ask the owner for hosts of his own, with the app's purpose in its own words
    and how many at most; None for one that does not."""
    asked = getattr(manifest, "owner_hosts", None)
    if asked is None:
        return None
    return Line(id=OWNER_HOSTS, permission=OWNER_HOSTS, host=None,
                sentence=OWNER_HOSTS_SENTENCE.format(most=asked.max, purpose=asked.purpose))


# A private app's model calls stay on the owner's own machines (bridge.local_models_only), so
# its dialog does not carry the warning about a hosted model.
PRIVATE_MODEL_SENTENCE = ("Ask your AI models, within your roles and limits, and show them "
                          "pictures from its own data. This app is private: only a model on "
                          "your own machines is asked.")


# Added to the ``inbound`` line when a manifest says how large its messages may be, so the
# owner reads it before installing. Not a new consent: the owner's setting stays the limit.
INBOUND_SIZE_SENTENCE = (" It expects messages of up to {kb} KB; the largest JimmyHQ takes is "
                         "yours to set under Settings, Apps.")


def _inbound_sentence(line: Line, manifest: Any) -> Line:
    sizes = [i.max_kb for i in getattr(manifest, "inbound", ()) or ()
             if getattr(i, "max_kb", None)]
    if line.permission != "inbound" or not sizes:
        return line
    return replace(line, sentence=line.sentence + INBOUND_SIZE_SENTENCE.format(kb=max(sizes)))


# Added to a ``net:<host>`` line when the manifest says what it expects of that host
# (``net_expects``), so the owner reads it before installing. Not a new consent: the owner's
# settings stay the limit, and a smaller hint only lowers it.
NET_EXPECTS_SENTENCE = (" It expects {what}; the most JimmyHQ allows is yours to set under "
                        "Settings, Apps.")


def size_words(kb: int) -> str:
    """A size in KB as the owner reads it: "512 KB", "20 MB", "1.5 MB"."""
    if kb < 1024:
        return f"{kb} KB"
    mb = kb / 1024
    return f"{mb:.1f}".removesuffix(".0") + " MB"


def wait_words(seconds: int) -> str:
    """A wait as the owner reads it: "45 seconds", "3 minutes", "1.5 minutes"."""
    if seconds < 60:
        return f"{seconds} second{'' if seconds == 1 else 's'}"
    minutes = f"{seconds / 60:.1f}".removesuffix(".0")
    return f"{minutes} minute{'' if minutes == '1' else 's'}"


def expects_words(max_kb: int | None, timeout_s: int | None) -> str:
    """What an app expects of a host, in plain words, for the install dialog."""
    parts = []
    if max_kb is not None:
        parts.append(f"answers of up to {size_words(max_kb)}")
    if timeout_s is not None:
        parts.append(f"to wait up to {wait_words(timeout_s)} for one")
    return " and ".join(parts)


def _net_sentence(line: Line, manifest: Any) -> Line:
    """Rule ``net_expects_is_what_a_host_gets``: the line for ``net:<host>`` says, in plain words,
    the largest answer and the longest wait the manifest expects of that host."""
    if line.permission != "net" or line.host is None:
        return line
    said = next((e for e in getattr(manifest, "net_expects", ()) or ()
                 if e.host == line.host), None)
    if said is None:
        return line
    what = expects_words(said.max_kb, said.timeout_s)
    return replace(line, sentence=line.sentence + NET_EXPECTS_SENTENCE.format(what=what))


def with_hints(lines: Iterable[Line], manifest: Any) -> list[Line]:
    """The lines with what the manifest says it expects added: inbound sizes and hosts."""
    return [_net_sentence(_inbound_sentence(line, manifest), manifest) for line in lines]


def describe(manifest: Asks) -> list[Line]:
    """The sentences the install dialog shows for this app, most sensitive first."""
    lines = describe_permissions(manifest.tier, manifest.permissions)
    if getattr(manifest, "private", False):
        lines = [replace(line, sentence=PRIVATE_MODEL_SENTENCE)
                 if line.permission == "model.ask" else line for line in lines]
    lines = with_hints(lines, manifest)
    hosts_line = owner_hosts_line(manifest)
    return ordered([*lines, hosts_line]) if hosts_line else lines


def diff(old: Asks, new: Asks) -> Diff:
    """Exactly what an update from ``old`` to ``new`` adds, widens and takes away."""
    if old.id != new.id:
        raise ValueError(f"cannot compare two different apps: {old.id!r} and {new.id!r}")
    before = {line.id: line for line in describe(old)}
    after = {line.id: line for line in describe(new)}
    added = tuple(sorted((after[k] for k in after.keys() - before.keys()), key=_order))
    removed = tuple(sorted((before[k] for k in before.keys() - after.keys()), key=_order))
    widened: tuple[Line, ...] = ()
    if old.private and not new.private:
        widened = (Line(id=PRIVATE_OFF, permission=PRIVATE_OFF, host=None,
                        sentence=_PRIVATE_OFF_SENTENCE),)
    if OWNER_HOSTS in before and OWNER_HOSTS in after \
            and before[OWNER_HOSTS].sentence != after[OWNER_HOSTS].sentence:
        # Another purpose, or more hosts: asked again, in the new words.
        widened += (after[OWNER_HOSTS],)
    tier = (old.tier, new.tier) if old.tier != new.tier else None
    return Diff(added=added, widened=widened, removed=removed, tier=tier)


def fingerprint(manifest: Asks) -> str:
    """A stable mark of what the owner is asked to agree to: the app, its tier, its
    permissions and its ``private`` promise. Two manifests with the same mark need no new
    consent; the order permissions are written in does not matter."""
    parts: dict[str, Any] = {
        "v": 1, "id": manifest.id, "tier": manifest.tier,
        "private": bool(manifest.private), "permissions": sorted(set(manifest.permissions))}
    asked = getattr(manifest, "owner_hosts", None)
    if asked is not None:  # only when declared, so no other app's mark changes
        parts["owner_hosts"] = {"purpose": asked.purpose, "max": asked.max}
    text = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _written(permission_id: str) -> str:
    param = BY_ID[permission_id].param
    if param == "feed":
        return f"{permission_id}:<app>.<feed>"
    return f"{permission_id}:<{param}>" if param else permission_id


def _kind_of(value: Any) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "true or false"
    if isinstance(value, int | float):
        return "a number"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return "that"
