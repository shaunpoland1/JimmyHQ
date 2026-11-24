"""Tools for findings: report something checked that the owner should know, and correct a
card already on the Bench. The rules live in ``comms/findings.py`` and
``comms/benchlink.py``."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.comms import Refused, benchlink, dates, findings, owner
from jimmyhq.village.tools.seams import EXPLORE, WORK, done, flag, refuse, refuse_as, text


def what_enough(what: str) -> bool:
    """Rule ``finding_what``: a finding says what is wrong and where, in enough words for
    the owner to see it themselves."""
    return len(what.strip()) >= findings.MIN_WHAT


async def report_finding(ctx: Any, args: dict[str, Any]) -> Any:
    v, agent_id = ctx.v, ctx.agent_id
    name = owner(v)
    title, what, why, fix = (text(args, k) for k in ("title", "what", "why", "fix"))
    urgent = flag(args, "urgent")
    logged = {"title": title[:200]}
    if urgent:
        logged["urgent"] = True
    if not what_enough(what):
        refuse("finding_what",
               f"Nothing was put on the Bench: 'what' is {len(what.strip())} characters and "
               f"needs at least {findings.MIN_WHAT}. Say what is wrong and exactly where you "
               f"saw it (the address, page or file), so {name} can see it too.",
               args_logged=logged)
    said = " ".join((title, what, why, fix))
    try:
        slip = dates.check(v, agent_id, "report_finding", title, said)
        if slip:
            refuse("date_slip", slip, args_logged=logged)
        twin = findings.finding_twin(v, agent_id, title)
        if twin and twin["card_id"] not in said:
            refuse("finding_twin", findings.twin_sentence(v, twin), args_logged=logged)
        note = ""
        no_room = findings.why_no_room(v, agent_id)
        if no_room and urgent:
            if findings.urgent_allowed(v, agent_id):
                no_room = None
            else:
                note = (" It was not sent as urgent: today's urgent findings are used up, so "
                        "it waits its turn like any other.")
                urgent = False
        if no_room:
            # Held, not refused: past a flat limit a checked finding is simply lost.
            q = findings.hold(v, agent_id, title, what, why, fix, args.get("files"))
            return done(
                f"{no_room[1]}, so this one is held (number {q['position']}"
                + (", replacing the held one with this title" if q["replaced"] else "")
                + "). It goes on the Bench by itself when there is room; if it stops being "
                "true, report it again with the same title and what changed." + note, logged)
        card = findings.file(v, agent_id, title, what, why, fix, args.get("files"),
                             urgent=urgent, task=ctx.task)
    except Refused as e:
        refuse_as(e, logged)
    return done(f"put on {name}'s Bench as {card['card_id']}"
                + (", marked urgent" if urgent else "") + "; you will see their answer." + note,
                logged)


async def correct_finding(ctx: Any, args: dict[str, Any]) -> Any:
    v = ctx.v
    cid = benchlink.card_id_of(args.get("card"))
    new_title = text(args, "title")
    try:
        card = benchlink.correct(v, ctx.agent_id, cid, text(args, "correction"), new_title)
    except Refused as e:
        refuse_as(e, {"card": cid[:40]})
    said = (f"{card['card_id']} now opens with your correction; {owner(v)} reads it first. "
            f"Its title is now \"{card['title']}\"")
    if card.get("replaced_earlier"):
        said += ". This replaced your earlier correction; the card holds only the newest one"
    if not new_title.strip():
        said += ". If the title itself is wrong, correct it once more with a new title"
    return done(said, {"card": card["card_id"]})


_S = {"type": "string"}

SPECS = [
    {"name": "report_finding", "kind": "self", "modes": (WORK, EXPLORE), "run": report_finding,
     "description": "Put something you found and checked on {owner}'s Bench, something they "
                    "should know or fix: a broken link or page, copy that doesn't match what "
                    "is live, a risk with a date on it. Not for drafts (hand_over those) or "
                    "new priorities (suggest_priority). Separate from hand-overs; past the "
                    "day's room it is held and goes by itself later. Say exactly where you "
                    "saw it, so they can see it too.",
     "params": {"title": {"type": "string", "description": "The problem in a few words"},
                "what": {"type": "string",
                         "description": "What is wrong and exactly where you saw it (the "
                                        "address, page or file), and how you checked it"},
                "why": {"type": "string",
                        "description": "Why it matters, who it affects, and by when"},
                "fix": {"type": "string", "description": "What would fix it, if you know"},
                "files": {"type": "array", "items": _S,
                          "description": "Your own notes on it, if any"},
                "urgent": {"type": "boolean",
                           "description": "true only when it cannot wait its turn: it goes "
                                          "straight to the Bench. Very few a day"}},
     "required": ["title", "what", "why"]},
    {"name": "correct_finding", "kind": "self", "modes": (WORK, EXPLORE),
     "run": correct_finding,
     "description": "Correct one of your own open cards on the Bench (a finding or a "
                    "hand-over) when you learn part of it was wrong. Your correction goes at "
                    "the top (a later correction replaces it), the title is marked Corrected "
                    "or replaced by the one you give, and what you first sent stays below "
                    "it. {owner} still decides the card.",
     "params": {"card": {"type": "string",
                         "description": "The card's id, as shown in your bench view"},
                "correction": {"type": "string",
                               "description": "What was wrong, what is true instead, and "
                                              "how you checked"},
                "title": {"type": "string",
                          "description": "Optional: a new title, when the old one says the "
                                         "wrong thing. The old title stays below with what "
                                         "you first sent"}},
     "required": ["card", "correction"]},
]

RULES = [
    ("finding_what", None, what_enough),
    ("finding_twin", None, findings.finding_twin),
    ("finding_room", "village.comms.findings_per_day", findings.finding_room),
    ("findings_open_hold", "village.comms.findings_open_hold", findings.why_no_room),
    ("urgent_findings_per_day", "village.comms.urgent_findings_per_day",
     findings.urgent_allowed),
    ("findings_held_full", "village.comms.findings_max_per_day", findings.hold),
    ("finding_duplicate", None, findings.file),
    ("own_open_card", None, benchlink.correct),
    ("correction_size", None, benchlink.correct),
    ("decision_trusted", None, benchlink.trusted),
]
