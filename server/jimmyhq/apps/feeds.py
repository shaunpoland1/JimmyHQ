"""Feeds between apps: one app offers a stream of items, another reads it, with the
owner's consent and nothing wider.

**Offering.** A full app declares its feeds in its manifest (``feeds``): a name, one of
its own kinds of event (short, so always ``app.<id>.<kind>``), a plain label and one
sentence on what is in it. Its items are the events of that kind it prints itself
(``tape.print``). There is nothing more to do: the record is the feed. Each feed's name
and kind are part of the producer's consent (``permissions.fingerprint``): an update that
changes a feed's kind, or adds one an installed app already may read, asks the owner again
(``permissions.feeds_changed``).

**Reading.** Another full app asks for ``feed:<app>.<feed>``, one feed of one app, which
the owner grants like any other permission. Its program then calls

    feed.read {app, feed, after?, limit?} -> {items: [{seq, id, ts, v, erased, item}], next}

which is ``tape.read`` narrowed to that one kind printed by that one app, and nothing
else on the owner's record. ``next`` is the position to pass back as ``after``.

Every answer either is the items or says why there are none to be had:

- the producer is not installed (or was removed): ``not_found``, with a sentence;
- the producer is switched off, or its files do not read: ``unavailable``;
- the producer no longer declares that feed: ``not_found``.

So an empty list always means "nothing new", never "could not look". An item the owner
had erased comes back as ``{"erased": true, "item": null}``: its place, never its text.

**Fuller content.** There is no call back into the producer. An item must carry what a
reader needs (an address, a title, a topic), and a reader that needs the source reads it
itself, through its own permissions (Posts reads every article with ``web.read``). That
keeps the producer's own records, and its program, out of reach of every other app.

**Private apps.** A private app offers no feed (its manifest is refused, and it cannot
print on the record anyway). A private app may read one: what it reads stays with it,
and its model calls stay on the owner's own machines unless the owner switches that off
for it (``app.<id>.local_models_only``), as for anything else a private app holds.

Limits: ``apps.quota.feed_reads_per_day`` and ``apps.quota.feed_items_per_read``. The
feeds an app offers and reads are read afresh from its manifest and the owner's grant
on every call, in every process (``sync``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jimmyhq.apps import bridge, quotas, schemas, store
from jimmyhq.apps.bridge import BridgeError, Call, Ctx, only
from jimmyhq.apps.manifest import Feed, Manifest
from jimmyhq.apps.permissions import FULL, PermissionRefused, check_feed_ref, feeds
from jimmyhq.bookmark import within_tape

if TYPE_CHECKING:
    from jimmyhq.core import Core
    from jimmyhq.tape.registry import Event

PERMISSION = "feed"
_SCAN_PAGES = 10  # one read looks at most this many pages of the record


# --- the rules ------------------------------------------------------------------------------


def check_args(args: dict[str, Any]) -> tuple[str, str, int, int | None]:
    """The feed a read names, and where and how much: ``(app, feed, after, limit)``."""
    only("feed.read", args, ("app", "feed", "after", "limit"), ("app", "feed"),
         note="Who is reading is set by JimmyHQ. ")
    app_id, feed = args["app"], args["feed"]
    if not isinstance(app_id, str) or not isinstance(feed, str):
        raise BridgeError("invalid", "\"app\" and \"feed\" for feed.read are the producer's id "
                                     "and the feed's name, as text. Nothing was read.")
    try:
        check_feed_ref(f"{app_id}.{feed}")
    except PermissionRefused:
        raise BridgeError("invalid", "\"app\" and \"feed\" for feed.read are an app's id and "
                                     "one of its feeds' names, in lower case, such as "
                                     "\"reader\" and \"articles\". Nothing was read.") from None
    after, limit = args.get("after", 0), args.get("limit")
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        raise BridgeError("invalid", "\"after\" for feed.read is the \"next\" an earlier read "
                                     "gave, a whole number, or 0. Nothing was read.")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)
                              or limit < 1):
        raise BridgeError("invalid", "\"limit\" for feed.read is a whole number from 1. "
                                     "Nothing was read.")
    return app_id, feed, after, limit


def check_granted(granted: tuple[str, ...], app_id: str, feed: str) -> None:
    """Rule ``feed_needs_its_own_grant``: the owner granted exactly this feed of exactly
    this app. No other grant covers it, ``tape.read`` included."""
    if f"{PERMISSION}:{app_id}.{feed}" not in granted:
        raise BridgeError(
            "not_allowed",
            f"Your app has not been granted \"feed:{app_id}.{feed}\", so nothing was read. Add "
            f"it to \"permissions\" in jimmyhq.app.json; the owner is asked when they install "
            "or update your app.")


def producer_feed(core: Core, app_id: str, feed: str) -> tuple[bridge.Installed, Feed]:
    """Rule ``feed_is_offered_now``: the producer is installed, switched on, readable,
    not private, and its manifest still declares the feed. Otherwise a sentence that says
    which, so a reader never takes "could not look" for "nothing new"."""
    row = store.get(core, app_id)
    if row is None:
        was = store.state(core).get("removed", {}).get(app_id)
        what = "was removed" if was else "is not installed"
        name = f"\"{was['name']}\"" if was else f"called \"{app_id}\""
        raise BridgeError(
            "not_found",
            f"The app {name} {what}, so its feed \"{feed}\" cannot be read. Nothing was read. "
            "Ask the owner to install it again, or carry on without it.")
    app = bridge.find_app(core, app_id)
    if app is None:
        raise BridgeError(
            "unavailable",
            f"The app \"{row.get('name') or app_id}\" is installed but its files cannot be "
            f"read just now, so its feed \"{feed}\" cannot be read. Nothing was read. Try "
            "again later; the Apps screen says what is wrong with it.")
    manifest = app.manifest
    if not app.enabled:
        raise BridgeError(
            "unavailable",
            f"The app \"{manifest.name}\" is switched off, so its feed \"{feed}\" cannot be "
            "read. Nothing was read. The owner can switch it on from the Apps screen.")
    if manifest.private or manifest.tier != FULL:
        raise BridgeError(
            "not_found",
            f"The app \"{manifest.name}\" offers no feeds, so nothing was read. Carry on "
            "without it.")
    found = manifest.feed(feed)
    if found is None:
        offered = ", ".join(f.name for f in manifest.feeds) or "none"
        raise BridgeError(
            "not_found",
            f"The app \"{manifest.name}\" no longer offers a feed called \"{feed}\" (it "
            f"offers: {offered}), so nothing was read. Carry on without it, or ask its maker.")
    return app, found


def item(event: Event) -> dict[str, Any]:
    """Rule ``erased_items_say_so``: one item as a reader is handed it. An erased item
    keeps its place and says so, and never carries what it said."""
    erased = bool(event.erased)
    return {"seq": event.seq, "id": event.id, "ts": event.ts, "v": event.v,
            "erased": erased, "item": None if erased else event.payload}


def read_limit(core: Core, app_id: str, asked: int | None) -> int:
    most = quotas.limit(core, app_id, "feed_items_per_read")
    return most if asked is None else min(asked, most)


def collect(core: Core, producer: Manifest, feed: Feed, after: int,
            limit: int) -> dict[str, Any]:
    """Rule ``only_that_kind_by_that_app``: the events of the feed's one kind that the
    producer printed itself, after ``after``. Anything else on the record, of any kind or
    by anyone, is never looked at, let alone handed over."""
    kind = producer.feed_kind(feed)
    found: list[dict[str, Any]] = []
    # An app that kept a place past the Tape's end (the Tape was put back from an older
    # backup) is told the end as its next place.
    cursor = within_tape(core.tape, after, "an app reading a feed")
    for _ in range(_SCAN_PAGES):
        page = core.tape.after(cursor, limit=limit, kinds=(kind,))
        for event in page:
            if event.kind != kind:
                continue  # the record answered more than was asked: never hand it over
            cursor = event.seq
            if event.actor == producer.actor:
                found.append(item(event))
                if len(found) >= limit:
                    return {"items": found, "next": cursor}
        if len(page) < limit:
            break
    return {"items": found, "next": cursor}


def feed_read(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    app_id, feed, after, limit = check_args(args)
    check_granted(ctx.app.granted, app_id, feed)
    producer, offered = producer_feed(ctx.core, app_id, feed)
    quotas.take(ctx.core, ctx.id, "feed_reads_per_day")
    schemas.load_all(ctx.core)  # its kinds may have been declared through another process
    return {"app": app_id, "feed": feed,
            **collect(ctx.core, producer.manifest, offered, after,
                      read_limit(ctx.core, ctx.id, limit))}


CALL = Call("feed.read", feed_read, PERMISSION, tiers=(FULL,), checks_own=True)


def add_call() -> None:
    from jimmyhq.apps import api  # noqa: PLC0415 - it imports the bridge, as this does

    if CALL.name not in api.API_CALLS:
        api.add_call(CALL)


# --- what the owner reads -------------------------------------------------------------------


def _manifests(core: Core) -> dict[str, Manifest]:
    out: dict[str, Manifest] = {}
    for app_id in store.installed(core):
        try:
            out[app_id] = store.manifest_of(core, app_id)
        except store.AppError:
            continue
    return out


def read_sentence(app_id: str, feed: str, producer: Manifest | None) -> str:
    """The sentence for ``feed:<app>.<feed>`` the owner reads, in the producer's own words
    when it is installed and offers that feed; otherwise saying plainly that nothing is
    read until it is."""
    offered = producer.feed(feed) if producer is not None else None
    if producer is None or offered is None:
        return (f"Read the feed \"{feed}\" of the app \"{app_id}\", if you install it. It is "
                "not installed or does not offer that feed, so this reads nothing now.")
    return (f"Read \"{offered.label}\" from {producer.name}: {offered.description} Nothing "
            f"else of {producer.name}'s or on your record.")


def explain(core: Core, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The dialog's lines, with each feed line said in the producer's words (and
    ``waiting`` when the producer is not there to read from)."""
    known = _manifests(core)
    out = []
    for line in lines:
        if line.get("permission") == PERMISSION and line.get("host"):
            app_id, _, feed = str(line["host"]).partition(".")
            producer = known.get(app_id)
            waiting = producer is None or producer.feed(feed) is None
            line = {**line, "sentence": read_sentence(app_id, feed, producer),
                    "waiting": waiting}
        out.append(line)
    return out


def readers_of(core: Core, app_id: str, feed: str) -> list[str]:
    """The names of the installed apps the owner let read this feed, sorted."""
    names = []
    for row in store.installed(core).values():
        if (app_id, feed) in feeds(row.get("permissions") or ()):
            names.append(str(row.get("name") or row["app_id"]))
    return sorted(names, key=str.lower)


def who_reads_what(core: Core, manifest: Manifest, granted: tuple[str, ...]) -> list[str]:
    """Plain lines for an app's page: who reads each feed it offers, and what it reads of
    other apps. Empty when it does neither."""
    lines: list[str] = []
    for offered in manifest.feeds:
        readers = readers_of(core, manifest.id, offered.name)
        who = ", ".join(readers) if readers else "No app"
        verb = "reads" if len(readers) == 1 or not readers else "read"
        lines.append(f"{who} {verb} \"{offered.label}\", which {manifest.name} offers: "
                     f"{offered.description}")
    known = _manifests(core)
    for app_id, feed in feeds(granted):
        producer = known.get(app_id)
        offered = producer.feed(feed) if producer is not None else None
        if offered is None:
            lines.append(f"{manifest.name} may read the feed \"{feed}\" of \"{app_id}\", which "
                         "is not installed or does not offer it, so it reads nothing.")
        else:
            lines.append(f"{manifest.name} reads \"{offered.label}\" from {producer.name}.")
    return lines


def plan_lines(core: Core, manifest: Manifest) -> list[str]:
    """For an install or update: which installed apps will read the feeds this one
    offers, so the owner hears it when the data starts to flow."""
    lines = []
    for offered in manifest.feeds:
        readers = readers_of(core, manifest.id, offered.name)
        if readers:
            lines.append(f"{', '.join(readers)}, already installed, will read "
                         f"\"{offered.label}\": {offered.description}")
    return lines
