"""Tools for the agent's list: focus on a task, finish it, set it aside, take one on from
chat, start one of its own, and suggest a new standing priority. The rules live in
``comms/tasklist.py``. A task is always named by its ref from the list (``P1``, ``T4``)."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.comms import Refused, owner, tasklist
from jimmyhq.village.tools.seams import WORK, done, refuse_as, task_over, text

_REF = {"type": "string", "description": "The task's ref from your list, like P1 or T4"}


async def focus_on(ctx: Any, args: dict[str, Any]) -> Any:
    v = ctx.v
    try:
        item, already = tasklist.focus(v, ctx.agent_id, args.get("task"))
    except Refused as e:
        refuse_as(e, {"task": text(args, "task")[:80]})
    if already:
        # Logged with the minute, so the body does not read a harmless refocus as the
        # same action with nothing new and charge frustration for it.
        return done(f"you are already working on {item.ref}",
                    {"task": item.ref, "already": True, "at": int(v.clock.now())})
    # The beat began on another task (or none). Recorded against that one, this action
    # would hand the agent straight back to it.
    task_over(ctx)
    return done(f"now working on {item.ref}: {item.plain}", {"task": item.ref})


async def finish_task(ctx: Any, args: dict[str, Any]) -> Any:
    v = ctx.v
    cited = args.get("evidence")
    logged = {"task": text(args, "task")[:80], "evidence": cited}
    try:
        out = tasklist.finish(v, ctx.agent_id, args.get("task"), cited)
    except Refused as e:
        refuse_as(e, logged)
    task_over(ctx)
    item, card = out["item"], out["card"]
    said = f"{item.ref} is done, on the evidence of {len(out['verified'])} of your actions"
    if card:
        said += (f". {owner(v)} has been told (card {card['card_id']}) and can reopen it if "
                 "something is missing")
    return done(said, {"task": item.ref, "evidence": out["verified"]})


async def set_aside(ctx: Any, args: dict[str, Any]) -> Any:
    v = ctx.v
    note = text(args, "note")
    try:
        item = tasklist.set_aside(v, ctx.agent_id, args.get("task"), note)
    except Refused as e:
        refuse_as(e, {"task": text(args, "task")[:80]})
    task_over(ctx)
    wait = round(float(v.world.S("frustration.resume_by_choice_min")))
    return done(f"{item.ref} is set aside, with your note for next time. You can pick it "
                f"back up with focus_on in {wait} minutes",
                {"task": item.ref, "note": note})


async def take_on(ctx: Any, args: dict[str, Any]) -> Any:
    v = ctx.v
    title = text(args, "title")
    try:
        ref = tasklist.take_on(v, ctx.agent_id, title, args.get("owner_said"))
    except Refused as e:
        refuse_as(e, {"title": title[:200]})
    task_over(ctx)
    return done(f"on your list as {ref}, and you are now working on it", {"title": title[:200]})


async def start_own_task(ctx: Any, args: dict[str, Any]) -> Any:
    title = text(args, "title")
    try:
        ref = tasklist.start_own(ctx.v, ctx.agent_id, title)
    except Refused as e:
        refuse_as(e, {"title": title[:200]})
    task_over(ctx)
    return done(f"on your list as {ref}, marked as your own, and you are now working on it. "
                "finish_task closes it, citing the actions that show it is done",
                {"title": title[:200]})


async def suggest_priority(ctx: Any, args: dict[str, Any]) -> Any:
    v = ctx.v
    title = text(args, "title")
    try:
        card = tasklist.suggest_priority(v, ctx.agent_id, title, text(args, "why"),
                                         text(args, "details"))
    except Refused as e:
        refuse_as(e, {"title": title[:200]})
    return done(f"put on {owner(v)}'s Bench as {card['card_id']}; you will see their answer",
                {"title": title[:200]})


SPECS = [
    {"name": "focus_on", "kind": "self", "modes": (WORK,), "run": focus_on,
     "description": "Start or switch to a task from your list, by its ref.",
     "params": {"task": _REF}, "required": ["task"]},
    {"name": "finish_task", "kind": "self", "modes": (WORK,), "run": finish_task,
     "description": "Mark a task done, citing the event ids of your actions that show it is "
                    "done. A standing priority does not finish: hand over each piece of "
                    "work under it instead.",
     "params": {"task": _REF,
                "evidence": {"type": "array", "items": {"type": "string"},
                             "description": "Event ids like evt-123"}},
     "required": ["task", "evidence"]},
    {"name": "set_aside", "kind": "self", "modes": (WORK,), "run": set_aside,
     "description": "Put a task down for now, with a note for when you come back to it.",
     "params": {"task": _REF,
                "note": {"type": "string", "description": "What to try next time"}},
     "required": ["task", "note"]},
    {"name": "take_on", "kind": "self", "modes": (WORK,), "run": take_on,
     "description": "Turn something {owner} asked you in chat into a task on your list, so "
                    "you can focus_on it and finish_task it like any other. Only for things "
                    "{owner} asked.",
     "params": {"title": {"type": "string",
                          "description": "The request in a few words, e.g. 'One clean "
                                         "handoff document'"},
                "owner_said": {"type": "string",
                               "description": "A few of {owner}'s own words, copied from "
                                              "the chat message where they asked for it. "
                                              "Needed: take_on is only for their requests"}},
     "required": ["title"]},
    {"name": "start_own_task", "kind": "self", "modes": (WORK,), "run": start_own_task,
     "description": "Put a piece of work of your own on your list, marked as yours, so you "
                    "can focus_on it and finish_task it. For work that takes more than a "
                    "few steps and that nobody asked for. Only a few open at a time. "
                    "Something {owner} asked for goes through take_on instead.",
     "params": {"title": {"type": "string",
                          "description": "The task in a few words, e.g. 'Check every queued "
                                         "file exists'"}},
     "required": ["title"]},
    {"name": "suggest_priority", "kind": "self", "modes": (WORK,), "run": suggest_priority,
     "description": "Suggest a new standing priority to {owner}, on their Bench. Best after "
                    "you have talked it over in chat. They approve it (it joins the "
                    "priorities, marked as suggested by you), say not yet, or no; you will "
                    "see the answer.",
     "params": {"title": {"type": "string",
                          "description": "The priority in a few words, e.g. 'Draft the "
                                         "monthly newsletter'"},
                "why": {"type": "string", "description": "Why it matters"},
                "details": {"type": "string",
                            "description": "What doing it would involve, and what you would "
                                           "need"}},
     "required": ["title", "why"]},
]

RULES = [
    ("task_known", None, tasklist.task_known),
    ("resume_wait", "village.frustration.resume_by_choice_min", tasklist.resume_wait),
    ("already_done", None, tasklist.focus),
    ("priority_never_finishes", None, tasklist.finish),
    ("evidence_own_ok_actions", None, tasklist.evidence_own_ok_actions),
    ("owner_asked", None, tasklist.owner_asked),
    ("requests_taken_per_day", "village.comms.requests_taken_per_day",
     tasklist.requests_taken_per_day),
    ("own_duplicate", None, tasklist.own_duplicate),
    ("own_tasks_per_day", "village.comms.own_tasks_per_day", tasklist.start_own),
    ("own_tasks_open", "village.comms.own_tasks_open", tasklist.start_own),
    ("priority_suggestions_per_day", "village.comms.priority_suggestions_per_day",
     tasklist.suggest_priority),
    ("priority_duplicate", None, tasklist.suggest_priority),
]
