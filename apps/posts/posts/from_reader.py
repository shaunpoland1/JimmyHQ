"""From Reader: the articles Reader offers, taken in through its feed ``articles``.

Only when the owner let Posts read it (``feed:reader.articles``, asked at install; an
owner without Reader is told it reads nothing until Reader is installed). The ``intake``
job reads what Reader offered since Posts last looked and, as the owner chose in
``from_reader``:

- ``ideas`` (the default): each article becomes an idea, with Start and Dismiss. Nothing
  is drafted until the owner presses Start.
- ``start``: each starts a piece by itself, up to ``pieces_per_day`` a day; the rest wait
  as ideas.

Either way a piece starts like every other way in (``intake.start``): Posts reads the
source itself (``reading``), runs its own overlap check and all its own checks. What
Reader said about an article (its title, topic and why) is shown beside the idea as
Reader's, and the piece says where it came from ("from Reader, topic X"); none of it is
taken as the owner's notes or given to a model. Only the address is used, and only to
read it again.

If the feed cannot be read, the sentence JimmyHQ gave is kept, with when Posts last heard
from Reader, and Today says so loudly. Posts goes on working with links, topics and
pictures. An owner who never had Reader is told quietly, once, where ideas would come
from. An article the owner had erased from the record becomes an erased idea, with its
text gone.

Reader can take an offer back (its second feed, ``withdrawn``; ``feed:reader.withdrawn``,
asked the same way and just as optional). ``take_back`` reads it with its own cursor: an
idea nothing was started from leaves the list (state ``withdrawn``, "taken back in
Reader"); one already started is left alone, and the job says nothing changed for it. If
Reader sends the article on again, the idea comes back. Which of an offer and its taking
back came last is told by their places on the owner's record, never by the order the two
feeds happen to be read in.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError

from posts import intake, sources
from posts.context import Ctx

log = logging.getLogger("posts")

PRODUCER, FEED = "reader", "articles"
GRANT = f"feed:{PRODUCER}.{FEED}"
CURSOR = "reader.cursor"
FEED_WITHDRAWN = "withdrawn"
GRANT_WITHDRAWN = f"feed:{PRODUCER}.{FEED_WITHDRAWN}"
CURSOR_WITHDRAWN = "reader.withdrawn.cursor"
TAKEN_BACK = "Taken back in Reader."
STATUS = "reader.status"
MAX_TITLE, MAX_TOPIC, MAX_WHY = 300, 200, 500
IDEA_STATES = ("new", "started", "dismissed", "erased", "withdrawn")


class IdeaError(ValueError):
    """Nothing was started or changed. The message says what to do instead."""


def granted(ctx: Ctx, grant: str = GRANT) -> bool:
    try:
        return grant in (ctx.app.hello().get("permissions") or [])
    except CallError:
        return False


# --- the rules ------------------------------------------------------------------------------


def offered_item(item: Any) -> dict[str, Any] | None:
    """Rule ``an_offer_is_data``: what Posts keeps of one item Reader offered, or None
    when it is not an article Posts could read (no web address). Plain fields only, each
    cut to its size; nothing in it is ever read as an instruction."""
    if not isinstance(item, dict):
        return None
    try:
        url = sources.check_url(item.get("url"))
    except sources.SourceProblem:
        return None
    offer = item.get("offer")
    return {"offer": offer if isinstance(offer, int) else None, "url": url,
            "title": str(item.get("title") or "")[:MAX_TITLE].strip(),
            "topic": str(item.get("topic") or "")[:MAX_TOPIC].strip(),
            "why": str(item.get("why") or "")[:MAX_WHY].strip()}


def origin_line(idea: dict[str, Any]) -> str:
    """Rule ``a_piece_says_where_it_came_from``: \"from Reader, topic X\"."""
    topic = idea.get("topic") or ""
    return f"from Reader, topic {topic}" if topic else "from Reader"


def already_here(ctx: Ctx, url: str) -> bool:
    """An idea or a piece with this address exists already: offered twice, or pasted."""
    store = ctx.store
    return bool(store.one("SELECT 1 AS x FROM idea WHERE url=? AND state != 'erased'", (url,))
                or store.one("SELECT 1 AS x FROM piece WHERE source_url=?", (url,)))


def same_offer(ctx: Ctx, offer: Any, url: str, states: tuple[str, ...]) -> dict[str, Any] | None:
    """Rule ``an_offer_is_known_by_its_number``: the idea in one of ``states`` that came
    from this offer, by Reader's offer number, else by the article's address."""
    marks = ", ".join("?" * len(states))
    if isinstance(offer, int) and not isinstance(offer, bool):
        found = ctx.store.one(f"SELECT * FROM idea WHERE offer=? AND state IN ({marks}) "  # noqa: S608
                              "ORDER BY id DESC LIMIT 1", (offer, *states))
        if found is not None:
            return found
    if not url:
        return None
    return ctx.store.one(f"SELECT * FROM idea WHERE url=? AND state IN ({marks}) "  # noqa: S608
                         "ORDER BY id DESC LIMIT 1", (url, *states))


def offered_again(ctx: Ctx, seq: int, found: dict[str, Any]) -> bool | None:
    """Rule ``an_article_sent_on_again_comes_back``: Reader offered an article Posts holds
    as an idea that is waiting or was taken back. The idea takes the newer offer's place
    on the record (so an older taking back no longer applies) and, if it was taken back,
    waits for the owner again. True when it came back, False when it was waiting anyway,
    None when Posts holds no such idea."""
    idea = same_offer(ctx, found["offer"], found["url"], ("new", "withdrawn"))
    if idea is None or seq <= idea["seq"]:
        return None
    back = idea["state"] == "withdrawn"
    ctx.store.update("idea", idea["id"], {"seq": seq, **found, "state": "new",
                                          "note": "" if back else idea["note"],
                                          "decided": None if back else idea["decided"]})
    return back


def auto_room(ctx: Ctx) -> int:
    """How many more ideas may start by themselves today (``pieces_per_day``; 0 is no
    limit)."""
    most = int(ctx.cfg().pieces_per_day)
    if most <= 0:
        return 1_000_000
    today = ctx.day(ctx.now())
    done = ctx.store.one("SELECT COUNT(*) AS n FROM idea WHERE started_by='posts' AND "
                         "started_day=?", (today,))["n"]
    return max(0, most - done)


def single_page(ctx: Ctx) -> int | None:
    """The one page ideas may start for by themselves: paused pages do not count."""
    pages = ctx.store.active_pages()
    return pages[0]["id"] if len(pages) == 1 else None


# --- the job --------------------------------------------------------------------------------


def take_in(ctx: Ctx) -> int:
    """Read what Reader offered since Posts last looked and keep each as an idea.
    Returns how many were new. Raises ``CallError`` with JimmyHQ's sentence when the feed
    cannot be read."""
    store = ctx.store
    after = int(store.get_meta(CURSOR) or 0)
    page = ctx.app.feeds.read(PRODUCER, FEED, after=after)
    new = 0
    for entry in page.items:
        known = store.one("SELECT * FROM idea WHERE seq=?", (entry.seq,))
        if entry.erased:
            if known is not None:
                store.update("idea", known["id"], {
                    "state": "erased" if known["state"] == "new" else known["state"],
                    "title": "", "topic": "", "why": "", "url": "",
                    "note": "The owner had this article erased from the record."})
            continue
        found = offered_item(entry.item)
        if known is not None or found is None:
            continue
        again = offered_again(ctx, entry.seq, found)
        if again is not None:
            new += int(again)
            continue
        if already_here(ctx, found["url"]):
            continue
        store.insert("idea", {"seq": entry.seq, **found, "state": "new",
                              "created": ctx.now()})
        new += 1
    store.set_meta(CURSOR, page.next)
    return new


def take_back(ctx: Ctx) -> tuple[int, int]:
    """Read what Reader took back since Posts last looked. An idea nothing was started
    from, offered before the taking back, leaves the list; one already started is left
    as it is. Returns (how many left the list, how many were already started). Reads
    nothing when the owner did not let Posts read that feed. Raises ``CallError`` with
    JimmyHQ's sentence when the feed cannot be read."""
    if not granted(ctx, GRANT_WITHDRAWN):
        return 0, 0
    store = ctx.store
    after = int(store.get_meta(CURSOR_WITHDRAWN) or 0)
    page = ctx.app.feeds.read(PRODUCER, FEED_WITHDRAWN, after=after)
    hidden = started = 0
    for entry in page.items:
        item = entry.item
        if entry.erased or not isinstance(item, dict):
            continue
        offer, url = item.get("offer"), str(item.get("url") or "")
        idea = same_offer(ctx, offer, url, ("new",))
        if idea is not None:
            if entry.seq > idea["seq"]:  # not one that was sent on again since
                store.update("idea", idea["id"], {"state": "withdrawn", "note": TAKEN_BACK,
                                                  "decided": ctx.now()})
                hidden += 1
        elif same_offer(ctx, offer, url, ("started",)) is not None:
            started += 1
    store.set_meta(CURSOR_WITHDRAWN, page.next)
    return hidden, started


def _taken_back(ctx: Ctx) -> list[str]:
    """``take_back`` for the job, in sentences. A feed of offers taken back that cannot be
    read (an older Reader has none) is no reason to stop taking in articles."""
    try:
        hidden, started = take_back(ctx)
    except CallError as error:
        log.info("could not read what Reader took back: %s", error.message)
        return []
    said = []
    if hidden:
        said.append(f"{hidden} idea{' was' if hidden == 1 else 's were'} taken back in Reader "
                    "and left the list.")
    if started:
        said.append(f"Reader took back {started} article{'' if started == 1 else 's'} "
                    "Posts had already started, so nothing changed for "
                    f"{'it' if started == 1 else 'them'}.")
    return said


def intake_job(ctx: Ctx) -> str:
    """The ``intake`` job."""
    if not granted(ctx):
        return "Posts may not read Reader's articles, so there is nothing to take in."
    store = ctx.store
    was = store.get_meta(STATUS) or {}
    try:
        new = take_in(ctx)
    except CallError as error:
        store.set_meta(STATUS, {**was, "ok": False, "said": error.message, "code": error.code,
                                "failed_at": ctx.now()})
        ctx.emit("posts.changed")
        return f"Could not read Reader's articles: {error.message}"
    store.set_meta(STATUS, {"ok": True, "said": "", "code": "", "heard_at": ctx.now(),
                            "failed_at": None})
    said = [f"{new} new ideas from Reader."] if new else []
    gone = _taken_back(ctx)  # before anything starts by itself
    said += gone
    if str(ctx.cfg().from_reader) == "start":
        said += start_waiting(ctx)
    if new or gone or was.get("ok") is False:
        ctx.emit("posts.changed")
    return " ".join(said) or "Nothing new from Reader."


LOOK_AGAIN_MS = 30_000  # how soon a failed read is tried again when the owner is looking


def look_again(ctx: Ctx) -> bool:
    """Rule ``a_mended_handover_shows_at_once``: when the last read of Reader's articles
    failed, opening the screen tries again, so installing or switching on Reader shows
    without waiting for the next ``intake`` run. At most once in a short while; never
    when the last read worked. Returns whether it looked."""
    if not granted(ctx):
        return False
    was = ctx.store.get_meta(STATUS) or {}
    if was.get("ok", True):
        return False
    failed_at = was.get("failed_at")
    if failed_at is not None and ctx.now() - int(failed_at) < LOOK_AGAIN_MS:
        return False
    intake_job(ctx)
    return True


def start_waiting(ctx: Ctx) -> list[str]:
    """For ``from_reader`` = ``start``: start new ideas by themselves, oldest first, up to
    today's room, on the owner's one page. With several pages they wait for the owner."""
    page_id = single_page(ctx)
    waiting = ctx.store.all("SELECT * FROM idea WHERE state='new' ORDER BY id")
    if not waiting:
        return []
    if page_id is None:
        return ["Ideas from Reader wait for you to choose a brand, since you have more than "
                "one that is not paused (or none)."]
    room = auto_room(ctx)
    started = 0
    for idea in waiting[:room]:
        start(ctx, idea["id"], page_id, by="posts")
        started += 1
    return [f"Started {started} pieces from Reader by themselves."] if started else []


# --- the owner's buttons --------------------------------------------------------------------


def need_idea(ctx: Ctx, idea_id: Any) -> dict[str, Any]:
    try:
        idea = ctx.store.one("SELECT * FROM idea WHERE id=?", (int(idea_id),))
    except (TypeError, ValueError):
        idea = None
    if idea is None:
        raise IdeaError("There is no such idea. Nothing was started.")
    if idea["state"] != "new":
        words = {"started": "was started already", "dismissed": "was dismissed",
                 "erased": "was erased from the record",
                 "withdrawn": "was taken back in Reader"}[idea["state"]]
        raise IdeaError(f"That idea {words}, so nothing was started.")
    return idea


def start(ctx: Ctx, idea_id: Any, page_id: Any, *, by: str = "owner") -> dict[str, Any]:
    """Start a piece from an idea, like any other way in: Posts reads the article itself
    and checks it. Only the address is taken from Reader; the piece says where it came
    from."""
    idea = need_idea(ctx, idea_id)
    try:
        piece = intake.start(ctx, page_id=page_id, way="article", source_url=idea["url"],
                             came_from=f"an article {origin_line(idea)}",
                             origin=origin_line(idea))
    except intake.IntakeError as error:
        raise IdeaError(str(error)) from None
    ctx.store.update("idea", idea["id"], {"state": "started", "piece_id": piece["id"],
                                          "started_by": by, "decided": ctx.now(),
                                          "started_day": ctx.day(ctx.now())})
    return piece


def dismiss(ctx: Ctx, idea_id: Any) -> dict[str, Any]:
    idea = need_idea(ctx, idea_id)
    ctx.store.update("idea", idea["id"], {"state": "dismissed", "decided": ctx.now()})
    return ctx.store.one("SELECT * FROM idea WHERE id=?", (idea["id"],)) or {}


# --- what the screen shows ------------------------------------------------------------------

NOT_INSTALLED = ("Install Reader to have the articles it finds arrive here as ideas. Until "
                 "then, paste a link, type a topic or upload pictures.")


def ideas(ctx: Ctx, most: int = 100) -> list[dict[str, Any]]:
    return ctx.store.all("SELECT id, url, title, topic, why, created FROM idea "
                         "WHERE state='new' ORDER BY id DESC LIMIT ?", (most,))


def waits_for_brand(ctx: Ctx) -> bool:
    """Whether ideas that would start by themselves wait for the owner to choose a brand:
    ``from_reader`` is ``start`` and more than one brand is not paused."""
    return str(ctx.cfg().from_reader) == "start" and len(ctx.store.active_pages()) > 1


def status(ctx: Ctx) -> dict[str, Any]:
    """Rule ``a_broken_handover_is_loud``: what the screen says about Reader.

    ``loud`` when the feed cannot be read and Posts had heard from Reader before, or the
    reason is anything but Reader never having been installed: Today then says the
    sentence, what to do, and when Posts last heard from Reader. An owner who never had
    Reader gets one quiet line instead."""
    if not granted(ctx):
        return {"reads": False, "loud": False, "said": "", "todo": "", "heard_at": None,
                "quiet": "", "waits_for_brand": False}
    found = ctx.store.get_meta(STATUS) or {}
    heard = found.get("heard_at")
    if found.get("ok", True):
        return {"reads": True, "loud": False, "said": "", "todo": "", "heard_at": heard,
                "quiet": "", "waits_for_brand": waits_for_brand(ctx)}
    # By the answer's code and by never having heard from Reader: not by its wording,
    # which is the core's to change.
    never_there = found.get("code") == "not_found" and heard is None
    if never_there:
        return {"reads": True, "loud": False, "said": found.get("said", ""), "todo": "",
                "heard_at": None, "quiet": NOT_INSTALLED, "waits_for_brand": False}
    todo = ("Open the Apps screen to see Reader: install it again, or switch it on. Until "
            "then Posts takes no articles from Reader, and links, topics and pictures work "
            "as before.")
    return {"reads": True, "loud": True, "said": found.get("said", ""), "todo": todo,
            "heard_at": heard, "quiet": "", "waits_for_brand": waits_for_brand(ctx)}
