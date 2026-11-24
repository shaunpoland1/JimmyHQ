"""What the Posts screen asks in one go: everything it shows, shaped for the page.

``calls(ctx)`` adds the page's own calls beside the ones in ``program``. Each returns
plain JSON. Nothing here changes a record.

``overview`` is the one call the page makes to draw any of its views: the pages, the
open and recently dropped pieces, the posts with their open questions and warnings
counted, which scheduler books them (so Practice can say so loudly), and from Reader
its ideas and whether its articles reach Posts (``from_reader.status``). From the flow
with agents: each piece's "who worked on this" line by role, and a ``notice`` when agent
work is stuck or its budget spent (with Wait, Finish without agents, Drop); research
that waits on the owner is in ``notices``. From the scheduler: each post's ``trouble``
(failed, missing, no answer, partial, out of step) with the calls that resolve it. Text that came
from an article or a model is returned as it is; the page shows it as text, never as
HTML.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from posts import booking, credits, from_reader, pages, states, work
from posts.context import Ctx
from posts.schedulers import lists_accounts, reads_posts
from posts.waiting import count as owner_count

# A post whose whole text the page needs; the others are listed with their opening.
FULL_TEXT = frozenset({"draft", "revising", "queued", "booking", "booked", "unsure",
                       "missing", "failed"})

# The page's buttons for a post in trouble with its scheduler, by the call each makes.
BOOK_AGAIN = {"call": "post.book_again", "label": "Put it back in the queue"}
# For a post that may have gone out: offered, but never as the first choice.
BOOK_AGAIN_CHECKED = {"call": "post.book_again", "kind": "quiet",
                      "label": "I checked: it did not go out. Put it back in the queue"}
DECLINE = {"call": "post.cancel", "label": "Decline it", "kind": "danger"}
CLEAR = {"call": "post.clear", "label": "I checked: it is on none of them",
         "ask": "Look on {tool} first, on every account of this post. Only if it is on none "
                "of them does Posts put it back in the queue to book again. If it is on "
                "any, leave this: Posts picks it up when it next looks."}
MOVE = {"call": "post.move", "label": "Move it to one time"}
CANCEL = {"call": "post.cancel", "label": "Cancel", "kind": "danger"}


def _counts(ctx: Ctx) -> dict[int, dict[str, int]]:
    """Open questions and warnings on each post's current revision."""
    out: dict[int, dict[str, int]] = {}
    for row in ctx.store.all(
            "SELECT c.post_id, c.level, c.answer FROM check_result c JOIN post p "
            "ON p.id = c.post_id AND c.revision = p.revision"):
        seen = out.setdefault(row["post_id"], {"questions": 0, "warnings": 0})
        if row["level"] == "ask" and not row["answer"]:
            seen["questions"] += 1
        elif row["level"] == "warn":
            seen["warnings"] += 1
    return out


def _asks(ctx: Ctx) -> dict[int, list[dict[str, str]]]:
    """The open questions on each post's current revision, by check: ``lived`` ("did
    this happen?"), ``numbers``, ``quotes`` and the rest, each with its sentence."""
    out: dict[int, list[dict[str, str]]] = {}
    for row in ctx.store.all(
            "SELECT c.post_id, c.name, c.detail FROM check_result c JOIN post p "
            "ON p.id = c.post_id AND c.revision = p.revision WHERE c.level = 'ask' AND "
            "(c.answer IS NULL OR c.answer = '') ORDER BY c.id"):
        out.setdefault(row["post_id"], []).append({"check": row["name"],
                                                   "detail": row["detail"]})
    return out


def clear_rest(names: str) -> dict[str, str]:
    """The owner's word that the accounts without a known copy do not have it either."""
    return {"call": "post.clear", "label": f"I checked: it is not on {names}",
            "ask": f"Look on {{tool}} for {names} first. If it is not there, the post stays "
                   "booked on the accounts that have it, and you can book the rest. If it "
                   "is there, leave this: Posts picks it up when it next looks."}


def book_rest(names: str) -> dict[str, str]:
    """Book only the accounts a partly booked post lacks."""
    return {"call": "post.book", "label": f"Book the rest ({names})",
            "ask": f"Book it for {names} at its own time? The accounts that have it are "
                   "left alone."}


def trouble(ctx: Ctx, post: dict[str, Any], label: str) -> dict[str, Any] | None:
    """What went wrong with the scheduler for one post, in the owner's words, and the
    buttons that resolve it. None when nothing did.

    failed       the scheduler could not publish it: back in the queue, or decline
    missing      it vanished from the scheduler: back in the queue, or decline
    lost         it cannot be found after its time, so it may have gone out: decline, or
                 (quietly, once the owner looked) back in the queue
    no_answer    the booking got no clear answer and no copy is known: look, then say it is
                 on none of its accounts
    partial      only some accounts have it: booked on some, "Book the rest"; unsure with
                 copies booked for certain, "I checked: it is not on" the rest; unsure with
                 a copy an unclear answer named, nothing (reconcile settles it); or a
                 cancel that reached some
    out_of_step  its copies hold different times: move it to one, or cancel"""
    state = post["state"]
    said = post.get("note") or ""
    copies = ctx.store.upstream(post["id"]) if state in ("unsure", "booked", "missing") \
        else []
    if state == "missing" and any(c["state"] == "lost" for c in copies):
        return {"kind": "lost", "said": said, "actions": [BOOK_AGAIN_CHECKED, DECLINE]}
    if state in ("failed", "missing"):
        return {"kind": state, "said": said, "actions": [BOOK_AGAIN, DECLINE]}
    if state == "unsure":
        said = said or f"{label} gave no clear answer."
        if any(c["state"] == "unconfirmed" for c in copies):
            return {"kind": "partial", "said": said, "actions": []}  # only its list can say
        held = [c["account"] for c in copies if c["state"] == "booked"]
        if held:
            asked = next((a["sent"].get("accounts") or [] for a in reversed(
                ctx.store.attempts(post["id"])) if a["action"] == "book"), [])
            rest = booking.labels_of(ctx, post, [a for a in asked if a not in held])
            return {"kind": "partial", "said": said, "actions": [clear_rest(rest)]}
        return {"kind": "no_answer", "said": said, "actions": [CLEAR]}
    lacking = booking.lacking_accounts(ctx, post) if state == "booked" else []
    if lacking:  # the rest can be booked only while its time is ahead
        names = booking.labels_of(ctx, post, lacking)
        ahead = post.get("slot_ms") is not None and post["slot_ms"] > ctx.now()
        return {"kind": "partial", "said": said, "actions": [
            *([book_rest(names)] if ahead else []),
            *([MOVE] if post.get("out_of_step") else []), CANCEL]}
    if state == "booked" and post.get("out_of_step"):
        cut = any(c["state"] == "cancelled" for c in copies)
        return {"kind": "partial" if cut else "out_of_step", "said": said,
                "actions": [MOVE, CANCEL]}
    return None


def _scheduler(ctx: Ctx) -> dict[str, Any]:
    chosen = str(ctx.cfg().scheduler)
    sched = ctx.schedulers.get(chosen)
    if sched is None:
        return {"id": chosen, "label": chosen, "sends_out": False, "known": False}
    return {"id": sched.id, "label": sched.label, "sends_out": bool(sched.sends_out),
            "known": True}


def page_tool(ctx: Ctx, page: dict[str, Any]) -> dict[str, Any]:
    """The tool that books one page and why: its own choice (``own``), or Posts' setting.
    ``lost`` is True when the page chose a tool Posts no longer offers, so the setting
    books it instead."""
    chosen = str(page.get("book_through") or "")
    sched_id = booking.chosen_id(ctx, page)
    sched = ctx.schedulers.get(sched_id)
    own = bool(chosen) and chosen == sched_id
    lost = bool(chosen) and chosen != sched_id
    if sched is None:
        return {"id": sched_id, "label": sched_id, "sends_out": False, "known": False,
                "own": False, "lost": lost}
    return {"id": sched.id, "label": sched.label, "sends_out": bool(sched.sends_out),
            "known": True, "own": own, "lost": lost}


def waits(ctx: Ctx) -> list[dict[str, Any]]:
    """The schedulers that asked Posts to wait (a rate limit), and until when: nothing is
    asked of them before that, and the queue says so."""
    out = []
    for sched in ctx.schedulers.values():
        until = sched.wait_until() if callable(getattr(sched, "wait_until", None)) else None
        if until is not None:
            out.append({"scheduler": sched.id, "label": sched.label, "until_ms": int(until)})
    return out


def offered(ctx: Ctx) -> list[dict[str, Any]]:
    """The schedulers a page's account may be on, for the page form's rows."""
    return [{"id": s.id, "label": s.label, "sends_out": bool(s.sends_out),
             "lists_accounts": lists_accounts(s), "reads_posts": reads_posts(s)}
            for s in ctx.schedulers.values()]


def overview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, cfg = ctx.store, ctx.cfg()
    rows = int(cfg.screen_rows)
    snippet = int(cfg.screen_snippet_chars)
    counts = _counts(ctx)
    asks = _asks(ctx)
    sched = _scheduler(ctx)
    waiting = work.needs_owner(ctx)
    by_piece = {w["piece_id"]: w for w in waiting if w["piece_id"]}
    found_pages = store.pages()
    tools = {}
    for page in found_pages:
        page["books_through"] = page_tool(ctx, page)
        tools[page["id"]] = {"page": page, "tool": page["books_through"]}
    pieces = store.all(
        "SELECT p.id, p.page_id, p.way, p.source_url, p.source_title, p.topic, p.state, "
        "p.origin, p.error, p.created, p.updated, (SELECT MAX(id) FROM post WHERE piece_id = p.id) "
        "AS post_id FROM piece p ORDER BY p.updated DESC, p.id DESC LIMIT ?", (rows,))
    posts = store.all(
        "SELECT o.id, o.piece_id, o.page_id, o.title, o.body, o.state, o.revision, "
        "o.slot_ms, o.scheduler, o.out_of_step, o.note, o.created, o.updated, "
        "i.state AS piece_state, i.source_url, i.source_title, i.topic, i.way, i.sponsored, "
        "i.origin "
        "FROM post o JOIN piece i ON i.id = o.piece_id "
        "ORDER BY COALESCE(o.slot_ms, o.updated) DESC, o.id DESC LIMIT ?", (rows,))
    for post in posts:
        if post["state"] not in FULL_TEXT and len(post["body"]) > snippet:
            post["body"] = post["body"][:snippet].rstrip() + "…"
            post["cut"] = True
        post["words"] = states.WORDS.get(post["state"], post["state"])
        post.update(counts.get(post["id"], {"questions": 0, "warnings": 0}))
        post["sponsored"] = bool(post["sponsored"])
        post["out_of_step"] = bool(post["out_of_step"])
        post["asks"] = asks.get(post["id"], [])
        own = tools.get(post["page_id"])
        found = ctx.schedulers.get(booking.scheduler_id_of(ctx, post, own and own["page"]))
        label = found.label if found else (own["tool"]["label"] if own else sched["label"])
        post["trouble"] = trouble(ctx, post, label)
        post["tool"] = {"id": found.id if found else "", "label": label}
    for piece in pieces:
        piece["words"] = states.WORDS.get(piece["state"], piece["state"])
        piece["worked"] = credits.line(store, piece["id"])
        piece["notice"] = by_piece.get(piece["id"])
    from_reader.look_again(ctx)
    return {"now_ms": ctx.now(), "pages": found_pages,
            "hint": "" if found_pages else pages.NO_PAGES, "pieces": pieces, "posts": posts,
            "scheduler": sched, "schedulers": offered(ctx), "waits": waits(ctx),
            "calendar_days": int(cfg.screen_days),
            "slow_minutes": int(cfg.step_timeout_minutes),
            "notices": [w for w in waiting if not w["piece_id"]],
            "book_lead_minutes": int(cfg.book_lead_minutes),
            "letter_keys": bool(cfg.letter_keys),
            "reader": from_reader.status(ctx), "ideas": from_reader.ideas(ctx),
            "waiting": owner_count(ctx),  # the sidebar's number: the page says the same
            "decline_reasons": [str(r) for r in cfg.decline_reasons or [] if str(r).strip()]}


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {"overview": lambda a: overview(ctx, a)}
