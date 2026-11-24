"""Reconcile: what became of booked posts on a scheduler that sends out.

Run by the ``reconcile`` job, every ``reconcile_minutes``. For each booked post it asks
the scheduler about every copy and, by ``judge``:

- marks it as sent once every copy has gone out;
- picks up a time changed on the scheduler's side;
- notices a copy that vanished before it went out (``missing``), or one the scheduler
  says failed (``failed``, with the scheduler's own words), and tells the owner; a copy
  that cannot be found once its time has passed (``lost``) is ``missing`` too, but says it
  may have gone out, and putting it back in the queue is not the first thing offered;
- marks it out of step when its copies hold different times.

An ``unsure`` booking is looked for on the scheduler (``resolve_unsure``): found for every
account, it is booked; found for none, while its time is still ahead and the whole list
was read, it was not booked and may be booked again by the owner; found for some, with
its time ahead, it is booked on those and the rest wait for "Book the rest"; once its
time has passed it stays unsure, saying what was found. Nothing is ever booked or tried
again here.

A scheduler that cannot be asked (a network blip, a server error, no key) changes
nothing: that post is skipped until the next run. A post stuck past ``stuck_hours`` gets
one card.
"""

from __future__ import annotations

import inspect
import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

from posts import booking, cards, engagement, kinds
from posts.context import Ctx
from posts.schedulers import accounts_for

log = logging.getLogger("posts.reconcile")

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
STUCK = ("booking", "unsure", "missing", "failed")


@dataclass(frozen=True)
class Verdict:
    kind: str  # same, went_out, failed, missing, lost, moved, out_of_step, going
    detail: str = ""
    slot_ms: int | None = None


# --- the rules, one function each -----------------------------------------------------------


def judge(post: dict[str, Any], copies: list[dict[str, Any]]) -> Verdict:
    """What the copies on the scheduler say about one booked post."""
    if not copies:
        return Verdict("same")
    failed = [c for c in copies if c["state"] == "failed"]
    if failed:
        said = "; ".join(c["said"] for c in failed if c["said"]) or "no reason given"
        return Verdict("failed", said)
    if all(c["state"] == "published" for c in copies):
        return Verdict("went_out")
    lost = [c for c in copies if c["state"] == "lost"]
    if lost:  # not found after its time: it may have gone out, so never "missing"
        return Verdict("lost", f"{len(lost)} of {len(copies)} copies")
    gone = [c for c in copies if c["state"] == "gone"]
    if gone:
        return Verdict("missing", f"{len(gone)} of {len(copies)} copies")
    waiting = [c for c in copies if c["state"] == "scheduled"]
    if len(waiting) < len(copies):
        return Verdict("going")  # some went out, the rest are still to go
    minutes = {c["slot_ms"] // MINUTE_MS for c in waiting if c["slot_ms"] is not None}
    if len(minutes) > 1:
        return Verdict("out_of_step", "its copies hold different times")
    if len(minutes) == 1:
        slot = waiting[0]["slot_ms"]
        if post["slot_ms"] is None or post["slot_ms"] // MINUTE_MS != slot // MINUTE_MS:
            return Verdict("moved", slot_ms=slot)
    return Verdict("same")


def same_text(a: Any, b: Any) -> bool:
    """Two post texts are the same when they match after the same tidying on both sides:
    Unicode composed, and every run of spaces, tabs and line breaks made one space."""
    def tidy(text: Any) -> str:
        return " ".join(unicodedata.normalize("NFC", str(text or "")).split())
    return tidy(a) == tidy(b)


@dataclass(frozen=True)
class Match:
    """What the list says about one account: ``found`` (with the id), ``none``, or a doubt
    that keeps the post unsure: ``different`` (another post sits in that slot), ``several``
    (more than one does), ``draft`` (held there in a way that will not go out by itself) or
    ``unknown`` (it could not be asked)."""

    kind: str
    external_id: str = ""
    count: int = 0


def match_upstream(listing: list[dict[str, Any]], accounts: list[str], slot_ms: int, *,
                   text: str | None = None, known: dict[str, str] | None = None
                   ) -> dict[str, Match]:
    """Rule ``an_unsure_booking_is_found_only_when_it_is_certainly_this_post``: for each
    account, a copy Posts already holds the id of (``known``: an unconfirmed copy) is
    taken when the list shows it, before anything else. Otherwise a post in the same
    minute on that account is taken only when it is the only one there and, when the list
    gives each post's text (Buffer does; SocialBu does not), its text is the booked
    ``text``. A post with other text is a different post in that slot, and more than one
    cannot be told apart: both keep the post unsure."""
    known = known or {}
    minute = slot_ms // MINUTE_MS
    out: dict[str, Match] = {}
    for account in accounts:
        mine = [i for i in listing if i.get("account") == account]
        held = known.get(account)
        if held and any(str(i.get("external_id")) == held for i in mine):
            out[account] = Match("found", held)
            continue
        here = [i for i in mine if i.get("slot_ms") is not None
                and i["slot_ms"] // MINUTE_MS == minute]
        if not here:
            out[account] = Match("none")
        elif len(here) > 1:
            out[account] = Match("several", count=len(here))
        elif text is not None and isinstance(here[0].get("text"), str) \
                and not same_text(here[0]["text"], text):
            out[account] = Match("different", str(here[0]["external_id"]))
        else:
            out[account] = Match("found", str(here[0]["external_id"]))
    return out


def found_upstream(listing: list[dict[str, Any]], accounts: list[str], slot_ms: int
                   ) -> dict[str, str]:
    """Which of the accounts hold a post in the minute of ``slot_ms``, by the scheduler's
    list, when nothing more is known: the account and the minute name the post."""
    return {a: m.external_id for a, m in match_upstream(listing, accounts, slot_ms).items()
            if m.kind == "found"}


def doubt_sentence(match: Match, name: str, label: str) -> str:
    """What one doubt says, for the post's note."""
    if match.kind == "different":
        return (f"A different post sits in that slot on {name} (its text is not this "
                f"post's), so Posts did not take it for this one. Look at it on {label}: "
                "if it is this post, changed there, it goes out from there.")
    if match.kind == "several":
        return (f"{match.count} posts sit in that minute on {name}, so Posts cannot tell "
                f"which is this one. Look on {label}.")
    if match.kind == "draft":
        return (f"{label} holds it on {name} as a draft or waiting for approval: it will not "
                f"go out by itself. Finish it on {label} or remove it.")
    return f"Posts could not ask {label} about its copy on {name}."


def is_stuck(post: dict[str, Any], now_ms: int, hours: int) -> bool:
    """Unsure, being booked, missing, failed or out of step for longer than ``hours``."""
    troubled = post["state"] in STUCK or (post["state"] == "booked" and post["out_of_step"])
    return troubled and post["updated"] < now_ms - hours * HOUR_MS


def is_due(last_ms: int | None, now_ms: int, minutes: int) -> bool:
    """A run handed over twice, or sooner than the setting, does nothing."""
    return last_ms is None or now_ms - last_ms >= minutes * MINUTE_MS - MINUTE_MS // 2


# --- one run --------------------------------------------------------------------------------


PRACTICE_NOTE = "Practice: this is when it would have gone out. Nothing was sent."


def end_practice(ctx: Ctx) -> int:
    """Rule ``a_practice_post_ends_when_its_time_passes``: a booked post on a scheduler that
    sends nothing out and gives the owner no Posted step (Practice) moves to went out once
    its time has passed, with a note that nothing was sent. It is not told to the ``posted``
    feed (nothing was posted) and no figures are asked for. By hand and every scheduler
    that sends out are left as they are. Returns how many ended."""
    count = 0
    for post in ctx.store.all("SELECT * FROM post WHERE state='booked' AND slot_ms IS NOT NULL "
                              "AND slot_ms <= ? ORDER BY id", (ctx.now(),)):
        sched = ctx.schedulers.get(post["scheduler"])
        if sched is None or sched.sends_out or not getattr(sched, "ends_by_itself", False):
            continue
        ctx.store.move_post(post["id"], "went_out", out_of_step=0, note=PRACTICE_NOTE)
        ctx.tape("post.went_out", {"post": post["id"]}, subject=f"post:{post['id']}")
        count += 1
    return count


def run(ctx: Ctx) -> str:
    cfg = ctx.cfg()
    now = ctx.now()
    if not is_due(ctx.store.get_meta("reconcile.last"), now, int(cfg.reconcile_minutes)):
        return "Not due yet."
    ctx.store.set_meta("reconcile.last", now)
    booking.mark_unsure(ctx)
    said = {"checked": 0, "changed": 0, "skipped": 0}
    by_sched: dict[str, list[dict[str, Any]]] = {}
    for post in ctx.store.all("SELECT * FROM post WHERE state='booked' ORDER BY id"):
        sched = ctx.schedulers.get(post["scheduler"])
        if sched is not None and sched.sends_out:
            by_sched.setdefault(sched.id, []).append(post)
    for sched_id, posts in by_sched.items():
        sched = ctx.schedulers[sched_id]
        seen = statuses(ctx, sched, posts)
        for post in posts:
            said["checked"] += 1
            outcome = check_post(ctx, sched, post, seen.get(post["id"], ...))
            said[outcome] = said.get(outcome, 0) + 1
    said["practice"] = end_practice(ctx)
    said["resolved"] = resolve_all_unsure(ctx)
    said["figures"] = engagement.collect(ctx)
    said["told"] = tell_stuck(ctx)
    if said["changed"] or said["resolved"] or said["told"] or said["practice"]:
        ctx.emit("posts.changed")
    return (f"Checked {said['checked']} booked posts: {said['changed']} changed, "
            f"{said['skipped']} skipped for now; {said['resolved']} unsure resolved.")


def statuses(ctx: Ctx, sched: Any, posts: list[dict[str, Any]]) -> dict[int, Any]:
    """What the scheduler says of each post, asked all at once where it can
    (``status_many``: Buffer batches its reads), else nothing here: ``check_post`` asks
    for each. A failure leaves them to be asked one by one."""
    many = getattr(sched, "status_many", None)
    if not callable(many) or not posts:
        return {}
    try:
        return dict(many([(p["id"], ctx.store.upstream(p["id"], live=True)) for p in posts]))
    except Exception as error:  # noqa: BLE001 - a blip changes nothing
        log.info("could not check %s's posts: %s", sched.id, type(error).__name__)
        return {p["id"]: None for p in posts}


def check_post(ctx: Ctx, sched: Any, post: dict[str, Any], seen: Any = ...) -> str:
    """``changed``, ``same`` or ``skipped`` (the scheduler could not tell). ``seen`` is
    what a batch already said of it (None: it could not tell); left out, it is asked."""
    if seen is None:
        return "skipped"
    if seen is ...:
        try:
            seen = sched.status(post["id"], ctx.store.upstream(post["id"], live=True))
        except Exception as error:  # noqa: BLE001 - a blip changes nothing
            log.info("could not check post %s: %s", post["id"], type(error).__name__)
            return "skipped"
    if not seen.known:
        log.info("could not check post %s: %s", post["id"], seen.detail[:200])
        return "skipped"
    verdict = judge(post, seen.copies)
    return "same" if apply(ctx, sched, post, verdict, seen.copies) is None else "changed"


def apply(ctx: Ctx, sched: Any, post: dict[str, Any], verdict: Verdict,
          copies: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Carry out one verdict. Returns the post when anything changed."""
    store = ctx.store
    post_id = post["id"]
    subject = f"post:{post_id}"
    for copy in copies:
        store.conn.execute("UPDATE upstream SET seen=?, said=?, slot_ms=COALESCE(?, slot_ms) "
                           "WHERE post_id=? AND external_id=?",
                           (ctx.now(), copy["said"], copy["slot_ms"], post_id,
                            copy["external_id"]))
    store.conn.commit()
    if verdict.kind == "went_out":
        store.conn.execute("UPDATE upstream SET state='published' WHERE post_id=? AND "
                           "state='booked'", (post_id,))
        store.conn.commit()
        done = store.move_post(post_id, "went_out", out_of_step=0,
                               note=f"It went out on {sched.label}.")
        ctx.tape("post.went_out", {"post": post_id}, subject=subject)
        ctx.tape("posted", kinds.posted(done, store.piece(done["piece_id"])), subject=subject)
        return done
    if verdict.kind == "failed":
        note = (f"{sched.label} could not publish it. It said: \"{verdict.detail}\". Nothing "
                f"was tried again. Book it again, or put it right on {sched.label}.")
        done = store.move_post(post_id, "failed", note=note)
        ctx.tape("post.failed", {"post": post_id}, subject=subject)
        cards.notice(ctx, "A post did not go out", f"Post #{post_id}: {note}")
        return done
    if verdict.kind == "missing":
        note = (f"It is no longer on {sched.label} ({verdict.detail} gone before it went "
                "out). Nothing was booked again. Book it again if it should still go out, "
                "or decline it.")
        store.conn.execute("UPDATE upstream SET state='gone' WHERE post_id=? AND state IN "
                           "('booked', 'unconfirmed')", (post_id,))
        store.conn.commit()
        done = store.move_post(post_id, "missing", note=note)
        cards.notice(ctx, "A booked post vanished", f"Post #{post_id}: {note}")
        return done
    if verdict.kind == "lost":
        note = (f"It cannot be found on {sched.label} ({verdict.detail}), and its time has "
                "passed, so it may have gone out. Look for it on the account itself before "
                "you book it again. Nothing was booked again.")
        for copy in copies:
            if copy["state"] == "lost":
                store.conn.execute("UPDATE upstream SET state='lost' WHERE post_id=? AND "
                                   "external_id=?", (post_id, copy["external_id"]))
        store.conn.execute("UPDATE upstream SET state='gone' WHERE post_id=? AND state IN "
                           "('booked', 'unconfirmed')", (post_id,))
        store.conn.commit()
        done = store.move_post(post_id, "missing", note=note)
        cards.notice(ctx, "A booked post cannot be found", f"Post #{post_id}: {note}")
        return done
    if verdict.kind == "moved" and verdict.slot_ms is not None:
        return store.move_post(post_id, "booked", slot_ms=verdict.slot_ms, out_of_step=0,
                               note=f"Its time was changed on {sched.label}; Posts now "
                               "shows that time.")
    if verdict.kind == "out_of_step" and not post["out_of_step"]:
        return store.move_post(post_id, "booked", out_of_step=1, note=(
            f"On {sched.label} {verdict.detail}, so this post is out of step. Move it here to "
            f"one time, or put it right on {sched.label}."))
    return None  # the same, or out of step already: an out-of-step post waits for the owner


def resolve_all_unsure(ctx: Ctx) -> int:
    """Look on each scheduler, once, for its unsure bookings: only on their accounts and
    around their times, where the scheduler can ask for that. Returns how many changed."""
    count = 0
    unsure = ctx.store.all("SELECT * FROM post WHERE state='unsure' ORDER BY id")
    by_sched: dict[str, list[dict[str, Any]]] = {}
    for post in unsure:
        sched = ctx.schedulers.get(post["scheduler"])
        if sched is not None and sched.sends_out:
            by_sched.setdefault(sched.id, []).append(post)
    for sched_id, posts in by_sched.items():
        sched = ctx.schedulers[sched_id]
        try:
            listing, narrowed = read_listing(ctx, sched, posts)
        except Exception as error:  # noqa: BLE001 - a blip (Unavailable) changes nothing
            log.info("could not read %s's list: %s", sched.id, type(error).__name__)
            continue
        for post in posts:
            if resolve_unsure(ctx, sched, post, listing, narrowed=narrowed):
                count += 1
    return count


def read_listing(ctx: Ctx, sched: Any, posts: list[dict[str, Any]]
                 ) -> tuple[list[dict[str, Any]], bool]:
    """The scheduler's list, asked for these posts' accounts and times when it takes them
    (``scheduled(accounts=, around_ms=)``; SocialBu's reads its whole list). Returns the
    list and whether it was narrowed: a narrowed list does not show an unconfirmed copy
    that was moved away, so such a copy is then asked about by itself."""
    takes = "around_ms" in inspect.signature(sched.scheduled).parameters
    if not takes:
        return sched.scheduled(), False
    accounts: set[str] = set()
    times: list[int] = []
    for post in posts:
        attempt = next((a for a in reversed(ctx.store.attempts(post["id"]))
                        if a["action"] == "book" and a["outcome"] == "unsure"), None)
        if attempt is None:
            continue
        accounts |= {str(a) for a in attempt["sent"].get("accounts") or []}
        slot = attempt["sent"].get("slot_ms") or post["slot_ms"]
        if slot is not None:
            times.append(int(slot))
    return sched.scheduled(accounts=sorted(accounts), around_ms=times), True


def booked_text_of(ctx: Ctx, post: dict[str, Any], attempt: dict[str, Any]) -> str:
    """The text the attempt sent: kept with it, else made again from the post."""
    if isinstance(attempt["sent"].get("text"), str):
        return attempt["sent"]["text"]
    piece = ctx.store.piece(post["piece_id"]) or {}
    return booking.booked_text(post, piece, str(ctx.cfg().sponsor_tag))


def ask_named(sched: Any, post_id: int, copies: list[dict[str, Any]]) -> dict[str, Match]:
    """Copies an unclear answer named that a narrowed list did not show, asked about by
    themselves: there (in any state but a draft) is found, not found before its time is
    none, anything else a doubt."""
    if not copies:
        return {}
    try:
        seen = sched.status(post_id, copies)
    except Exception:  # noqa: BLE001 - could not tell
        seen = None
    if seen is None or not seen.known:
        return {c["account"]: Match("unknown") for c in copies}
    out: dict[str, Match] = {c["account"]: Match("unknown") for c in copies}
    for copy in seen.copies:
        if copy.get("status") in ("draft", "needs_approval"):
            out[copy["account"]] = Match("draft")
        elif copy["state"] in ("scheduled", "published", "failed"):
            out[copy["account"]] = Match("found", str(copy["external_id"]))
        elif copy["state"] == "gone":
            out[copy["account"]] = Match("none")
    return out


def resolve_unsure(ctx: Ctx, sched: Any, post: dict[str, Any],
                   listing: list[dict[str, Any]], *, narrowed: bool = False) -> bool:
    """Look for an unsure booking on the scheduler's list. Each account without a copy
    booked for certain is looked for; a copy found is adopted. Then: every account has
    it, so it is booked; its time is ahead and the whole list was read, so an account not
    found does not have it: with no copy anywhere it goes back in the queue, with some it
    is booked on those and lacks the rest ("Book the rest"); its time has passed, so it
    stays unsure and says what to check. Nothing is ever booked here."""
    store = ctx.store
    open_ = [a for a in store.attempts(post["id"]) if a["outcome"] == "unsure"
             and a["action"] == "book"]
    if not open_:
        return False
    attempt = open_[-1]
    slot = attempt["sent"].get("slot_ms") or post["slot_ms"]
    page = store.page(post["page_id"]) or {}
    accounts = attempt["sent"].get("accounts") or [a["account"]
                                                   for a in accounts_for(sched.id, page)]
    if slot is None:
        return False
    live = store.upstream(post["id"], live=True)
    held = {u["account"]: u["external_id"] for u in live if u["state"] == "booked"}
    lacking = [a for a in accounts if a not in held]
    known = {u["account"]: str(u["external_id"]) for u in live if u["state"] == "unconfirmed"
             and u["external_id"]}
    matches = match_upstream(listing, lacking, slot, text=booked_text_of(ctx, post, attempt),
                             known=known)
    if narrowed:  # a named copy the narrowed list did not show is asked about by itself
        unseen = [u for u in live if u["state"] == "unconfirmed" and u["account"] in matches
                  and matches[u["account"]].kind != "found"]
        matches.update(ask_named(sched, post["id"], unseen))
    found = {a: m.external_id for a, m in matches.items() if m.kind == "found"}
    doubts = {a: m for a, m in matches.items() if m.kind not in ("found", "none")}
    adopted = adopt(ctx, post["id"], found, slot)
    held.update(found)
    absent = [a for a in accounts if a not in held]
    label = sched.label
    if doubts:  # never adopted, never taken as absent: the owner is told what to look at
        note = " ".join(["This post is still unsure: nothing was sent again."] + [
            doubt_sentence(m, booking.labels_of(ctx, post, [a]), label)
            for a, m in doubts.items()])
        if post["note"] == note:
            return adopted
        store.update("post", post["id"], {"note": note})
        return True
    if not absent:
        for one in open_:
            booking._finish(ctx, post["id"], one["id"], "sent",  # noqa: SLF001
                            f"Found on {label} by reconcile.")
        store.move_post(post["id"], "booked", out_of_step=0, note=(
            f"Posts found it on {label}: it is booked."))
        return True
    if slot > ctx.now():
        for one in open_:
            booking._finish(ctx, post["id"], one["id"], "cleared",  # noqa: SLF001
                            f"Not on {label}'s list for {len(absent)} account(s) when "
                            "reconcile looked.")
        marks = ",".join("?" * len(absent))
        store.conn.execute(f"UPDATE upstream SET state='cleared' WHERE post_id=? AND "  # noqa: S608
                           f"state='unconfirmed' AND account IN ({marks})",
                           (post["id"], *absent))
        store.conn.commit()
        if not held:
            store.move_post(post["id"], "queued", note=(
                f"Posts looked on {label} and it is not there, so it was not booked. "
                "Nothing was sent again. Book it when you are ready."))
            return True
        store.move_post(post["id"], "booked", out_of_step=0, note=(
            f"Posts looked on {label}: it is booked for {booking.labels_of(ctx, post, list(held))} "
            f"but not for {booking.labels_of(ctx, post, absent)}. Nothing was sent again. "
            "Press \"Book the rest\" to book it for "
            f"{booking.labels_of(ctx, post, absent)}; the accounts that have it are left "
            "alone."))
        return True
    if held:
        note = (f"Posts found it on {label} for {len(held)} of {len(accounts)} accounts, and "
                f"not for {booking.labels_of(ctx, post, absent)}; its time has passed, so it "
                f"may have gone out there. Nothing was sent again. Look on "
                f"{booking.labels_of(ctx, post, absent)} to see whether it went out.")
    else:
        note = (f"Posts could not find it on {label}, and its time has passed, so it may "
                f"have gone out. Nothing was sent again. Look on {label} and on the accounts "
                "themselves; if it is on none of them, press \"I checked: it is on none of "
                "them\" to book it again.")
    if post["note"] == note:
        return adopted
    store.update("post", post["id"], {"note": note})  # still unsure: only what it says changes
    return True


def adopt(ctx: Ctx, post_id: int, found: dict[str, str], slot: int) -> bool:
    """Copies found on the scheduler's list become booked: an unconfirmed copy of the same
    id is promoted, any other unconfirmed copy for that account is cleared. Returns
    whether anything was adopted."""
    store = ctx.store
    for account, external_id in found.items():
        same = store.one("SELECT id FROM upstream WHERE post_id=? AND account=? AND "
                         "external_id=? AND state='unconfirmed'",
                         (post_id, account, external_id))
        store.conn.execute("UPDATE upstream SET state='cleared' WHERE post_id=? AND "
                           "account=? AND state='unconfirmed' AND external_id != ?",
                           (post_id, account, external_id))
        if same:
            store.conn.execute("UPDATE upstream SET state='booked', seen=? WHERE id=?",
                               (ctx.now(), same["id"]))
        else:
            store.insert("upstream", {"post_id": post_id, "account": account,
                                      "external_id": external_id, "state": "booked",
                                      "seen": ctx.now(), "slot_ms": slot})
    store.conn.commit()
    return bool(found)


def tell_stuck(ctx: Ctx) -> int:
    """One card for the posts stuck past ``stuck_hours`` that the owner has not been told
    about. Returns how many it named."""
    now = ctx.now()
    hours = int(ctx.cfg().stuck_hours)
    told = set(ctx.store.get_meta("reconcile.told") or [])
    stuck = [p for p in ctx.store.all("SELECT * FROM post ORDER BY id")
             if is_stuck(p, now, hours)]
    fresh = [p for p in stuck if p["id"] not in told]
    still = {p["id"] for p in stuck}
    if fresh:
        lines = [f"Post #{p['id']}: {p['note'] or p['state']}" for p in fresh]
        word = "post has" if len(fresh) == 1 else "posts have"
        cards.notice(ctx, f"{len(fresh)} {word} been stuck for over {hours} hours",
                     "\n".join(lines))
    ctx.store.set_meta("reconcile.told", sorted((told & still) | {p["id"] for p in fresh}))
    return len(fresh)
