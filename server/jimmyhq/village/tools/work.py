"""Tools for finished work: record a review pass, and hand the work over to the owner's
Bench. The rules live in ``comms/work.py``."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.comms import Refused, clock_time, dates, owner, work
from jimmyhq.village.tools.seams import WORK, done, refuse, refuse_as, text


async def review_work(ctx: Any, args: dict[str, Any]) -> Any:
    v, agent_id = ctx.v, ctx.agent_id
    path = work.clean(args.get("path")) or text(args, "path")[:200]
    try:
        r = work.review(v, agent_id, path, text(args, "kind") or "other", text(args, "notes"))
    except Refused as e:
        refuse_as(e, {"path": path})
    held = work.queued_position(v, agent_id, path)
    logged = {"path": path, "pass": r["pass"]}
    if r.get("unchanged"):
        # Two passes on the same text is the design; a third is rereading.
        return done(
            f"{path} already has two review passes as it is now (the last at "
            f"{clock_time(v, r['at'])}) and hasn't changed since, so this one isn't recorded: "
            "another reread adds nothing. "
            + ("It is already queued for tomorrow, so move on to something new" if held
               else "Hand it over if it isn't already, or move on to something new"), logged)
    if held:
        # A queued file goes as it stands; there is nothing to hand over again.
        said = (f"review pass {r['pass']} of {path} recorded. It is already queued for "
                f"tomorrow (number {held}) and goes on the Bench as it stands then: no need "
                "to hand it over again")
    else:
        said = f"review pass {r['pass']} of {path} recorded" + (
            f". That's two passes: if it's good enough to show {owner(v)}, hand it over."
            if r["pass"] >= work.PASSES_COUNTED else "")
    return done(said, logged)


async def hand_over(ctx: Any, args: dict[str, Any]) -> Any:
    v, agent_id, task = ctx.v, ctx.agent_id, ctx.task
    name = owner(v)
    title, paths = text(args, "title"), args.get("paths")
    fields = [text(args, k) for k in ("tldr", "details", "unsure", "needs")]
    logged = {"title": title[:200], "paths": paths}
    try:
        why = dates.check(v, agent_id, "hand_over", title, " ".join([title, *fields]))
        if why:
            refuse("date_slip", why, args_logged=logged)
        if work.over_day_limit(v, agent_id, paths, task):
            # Held, not refused, and said plainly that the day goes on: "the rest can go
            # tomorrow" is read as bedtime, and "keep working" as "stop drafting".
            q = work.hold(v, agent_id, title, paths, *fields, task=task)
            if q["replaced"]:
                return done(
                    f"these files were already queued for tomorrow (number {q['position']}, "
                    f"since {clock_time(v, q['queued_at'])}); this replaced that entry's "
                    "title and notes, nothing more. You never need to hand a queued file "
                    "over again: if you edit it, one review pass of the new text is enough, "
                    "and it goes on the Bench tomorrow as it stands then", logged)
            return done(
                f"{name} has had as many hand-overs as they can take today, so this one is "
                f"queued (number {q['position']}): it goes on the Bench by itself tomorrow "
                "morning, nothing more to do. The day isn't over: keep working. The limit is "
                f"on what {name} reads in a day, not on what you make, so it is no reason to "
                "stop drafting or to keep your own queue file", logged)
        card = work.hand_over(v, agent_id, title, paths, *fields, task=task)
    except Refused as e:
        refuse_as(e, logged)
    cid = card["card_id"]
    if card.get("updated"):
        said = (f"{cid} already had these files, so it now shows {name} this version (no new "
                "card)")
    elif card.get("refiled"):
        said = (f"your revision was sent: {cid} now holds these files as they are, in place "
                "of the earlier card (it does not count as a new hand-over)")
    else:
        said = f"handed over to {name} as {cid}; you will see their answer"
    return done(said, logged)


SPECS = [
    {"name": "review_work", "kind": "self", "modes": (WORK,), "run": review_work,
     "description": "Record a review pass of something you made, as it is now, after "
                    "rereading it against village/reviewing.md (with some distance from the "
                    "first draft). Fix what you find first; a later edit needs another "
                    "pass.",
     "params": {"path": {"type": "string", "description": "The file you reviewed"},
                "kind": {"type": "string", "enum": list(work.KINDS)},
                "notes": {"type": "string",
                          "description": "What you checked, what you changed and why"}},
     "required": ["path", "kind", "notes"]},
    {"name": "hand_over", "kind": "self", "modes": (WORK,), "run": hand_over,
     "description": "Hand finished work to {owner} on their Bench, once each file has a "
                    "review pass since its last edit. They approve it (final), ask for "
                    "changes, or say no.",
     "params": {"title": {"type": "string", "description": "What it is, in a few words"},
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "The file or files"},
                "tldr": {"type": "string",
                         "description": "Two or three sentences {owner} can act on in a "
                                        "minute: what it is and what you want from them"},
                "details": {"type": "string",
                            "description": "For checking later: what it is and who it's "
                                           "for, what you checked, decisions you made and "
                                           "why, sources"},
                "unsure": {"type": "string",
                           "description": "Anything you're not confident about, once and "
                                          "specifically"},
                "needs": {"type": "string",
                          "description": "Anything only {owner} can give: a decision, a "
                                         "link, an approval"}},
     "required": ["title", "paths", "tldr", "details"]},
]

RULES = [
    ("review_distance", work.DISTANCE, work.review_distance),
    ("review_notes", None, work.review),
    ("third_pass_unchanged", None, work.review),
    ("reviewed_as_is", None, work.reviewed_as_is),
    ("tldr_size", None, work.check),
    ("details_short", None, work.check),
    ("date_slip", None, dates.slip),
    ("handovers_per_day", "village.comms.handovers_per_day", work.over_day_limit),
    ("handover_queue_full", "village.comms.handovers_per_day", work.hold),
]
