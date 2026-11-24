"""The agent's list, what it is doing right now, what it just did, and today's diary."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.context import bench_view, nudges
from jimmyhq.village.rules import rule

# With every item done, an agent will try made-up task names one after another, and each
# refusal adds frustration. Tell it plainly that no task is needed.
ALL_DONE = ("Everything on this list is done. You don't need a task to keep working: "
            "choose something useful and just do it. focus_on and finish_task only take "
            "references from this list.")

REASON_WORDS = {
    "repeat_same_state": "you just did exactly this and nothing has changed since, so it "
                         "would not help; try something different",
    "task_set_aside": "that task is set aside for now",
    "page_wall": "reading pages has given you nothing new for several tries in a row; it "
                 "is a wall, not a puzzle. Step back: write down what you did get, then "
                 "come at the goal another way. A web_search with different words that "
                 "finds something opens reading again",
}


@rule("repeat_same_state", "village.guards.repeat_window")
@rule("task_set_aside")
@rule("page_wall", "village.guards.wall_after")
def reason_words(reason: str) -> str:
    """The words for a refusal. The world's own guards answer with a short code; the
    agent reads a sentence."""
    return REASON_WORDS.get(reason, reason)


@rule("all_done")
def priorities_text(v: Any, agent_id: str) -> str:
    """The agent's list: the owner's priorities, then its tasks, each with the
    reference the task tools take."""
    items = v.ask("comms.tasklist", "items", agent_id, default=[]) or []
    owner = v.owner_name()
    lines = [f"- [{i.status}]{f' (from {owner})' if i.source == 'priority' else ''} "
             f"{i.ref}: {i.title}" for i in items]
    text = "\n".join(lines) or "(none)"
    if not any(i.status != "done" for i in items):
        text += "\n" + ALL_DONE
    return text


def args_text(args: dict | None) -> str:
    return ", ".join(f"{k}={str(x)[:60]!r}" for k, x in (args or {}).items())


@rule("recent_actions", "village.context.recent_actions")
def recent_actions_text(v: Any, agent_id: str) -> str:
    """The agent's last actions, a line each, with the event id it can cite as
    evidence. A refusal shows its arguments: "click was refused" twelve times says
    nothing about which click."""
    n, chars = int(v.ctx_s("recent_actions")), int(v.ctx_s("result_chars"))
    evs = v.tail(agent_id, ["action", "validator_reject", "task_done", "claim_unverified",
                            "set_aside", "resume"], n)
    lines = []
    for e in evs:
        d = e.data
        if e.kind == "action":
            lines.append(f"{e.id}: {d['tool']}({args_text(d.get('args'))}) -> "
                         f"{'ok' if d['ok'] else 'FAILED'}: {str(d.get('result', ''))[:chars]}"
                         + (" (nothing new)" if d.get("no_new_result") else ""))
        elif e.kind == "validator_reject":
            args = d.get("args") if isinstance(d.get("args"), dict) else None
            lines.append(f"{e.id}: {d['tool']}({args_text(args)}) was refused: "
                         f"{reason_words(d['reason'])}")
        elif e.kind == "task_done":
            lines.append(f"{e.id}: finished {d['task']}")
        elif e.kind == "claim_unverified":
            lines.append(f"{e.id}: tried to finish {d['task']} without evidence; not counted")
        elif e.kind == "set_aside":
            lines.append(f"{e.id}: {d['task']} was set aside")
        elif e.kind == "resume":
            lines.append(f"{e.id}: {d['task']} "
                         + ("picked back up" if d.get("chosen") else "is available again"))
    return "\n".join(lines) or "(nothing yet)"


@rule("resume_note_shown", "village.context.resume_note_hours")
def resume_note_text(v: Any, agent_id: str, task: str) -> str:
    """After a task is picked back up: what had been tried, and the note left for now."""
    since = v.clock.now() - int(v.ctx_s("resume_note_hours")) * 3600
    resumed = [e for e in v.log.query(agent=agent_id, kinds=["resume"], since=since)
               if e.data["task"] == task]
    if not resumed:
        return ""
    note = resumed[-1].data["resume_note"]
    tried = "; ".join(f"{t['tool']} -> {'ok' if t['ok'] else 'failed'}"
                      for t in note.get("tried", []))
    return (f"You set this aside earlier. You had tried: {tried or 'nothing recorded'}. "
            f"Your note for next time: {note.get('next_guess') or '(none)'}")


@rule("parked", "village.frustration.resume_by_choice_min")
def parked_lines(v: Any, a: Any) -> list[str]:
    """Each task set aside, and the minute it can be picked back up."""
    now, wait = v.clock.now(), v.S("frustration.resume_by_choice_min") * 60
    out = []
    for task, p in a.parked.items():
        back = p["since"] + wait
        out.append(f"Set aside since {v.when(p['since']):%H:%M}: {task}. " + (
            "You can pick it back up with focus_on whenever you like." if now >= back
            else f"You can pick it back up with focus_on from {v.when(back):%H:%M}."))
    return out


WAIT_NAMES, WAIT_NAME_CHARS = 5, 80


@rule("blocked_ladder")
def blocked_text(a: Any) -> str:
    """When waiting on someone: don't stop and don't drift."""
    if not a.waiting:
        return ""
    names = [str((w or {}).get("title") or item)[:WAIT_NAME_CHARS]
             for item, w in a.waiting.items()]
    more = f", and {len(names) - WAIT_NAMES} more" if len(names) > WAIT_NAMES else ""
    return ("You are waiting on someone for " + ", ".join(names[:WAIT_NAMES]) + more
            + ". While you wait: ask "
            "a coworker (the work board), look for another way to the same goal, or pick the "
            "next item on your list.")


def right_now_text(v: Any, agent_id: str) -> str:
    a = v.world.agents[agent_id]
    sit = []
    if a.current_task:
        sit.append(f"You are working on: {a.current_task}")
        sit.append(resume_note_text(v, agent_id, a.current_task))
    else:
        sit.append("You are not working on a particular task right now.")
    sit.append(bench_view.bench_text(v, agent_id))
    sit.append(nudges.circling_text(v, agent_id) or nudges.waiting_text(v, agent_id))
    sit.append(nudges.news_text(v, agent_id))
    sit.append(nudges.review_wait_text(v, agent_id))
    sit += parked_lines(v, a)
    sit.append(blocked_text(a))
    return "\n".join(s for s in sit if s)


@rule("diary_view", "village.context.diary_entries")
def diary_text(v: Any, agent_id: str) -> tuple[str, str]:
    """Today's last diary entries and, when there are more, how many there are: an
    agent that sees only the last three cannot tell how much it has already written.
    Returns (the count note for the heading, the entries)."""
    entries = getattr(v.seam("memory.diary"), "entries", None)
    day = v.when(v.clock.now()).date().isoformat()
    today = entries(v.agent_dir(agent_id), day, include_dreams=True) if entries else []
    n, chars = int(v.ctx_s("diary_entries")), int(v.ctx_s("diary_chars"))
    text = "\n".join(f"{x['time']}{' (dream)' if x.get('dream') else ''}: {x['text'][:chars]}"
                     for x in today[-n:]) or "(nothing written today)"
    count = (f" ({len(today)} entries so far, the last at {today[-1]['time']})"
             if len(today) > n else "")
    return count, text
