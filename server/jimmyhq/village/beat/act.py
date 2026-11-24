"""Doing the chosen action: the world's guards, then the tool, then the world's record.

The beat never changes the body. A tool does its work and says what happened; the
world applies the effects, with logged causes.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.beat.validate import clip_args
from jimmyhq.village.catalog import Done, Tool, ToolCtx
from jimmyhq.village.rules import Refusal, rule

LINE_CHARS = 200  # what the action log keeps of a result


@rule("whole_answer_next_beat", "village.context.last_result_chars")
def whole_answer(kind: str, name: str, args: dict[str, Any], result: str, ok: bool) -> bool:
    """Whether this answer is shown whole next beat. The action log keeps a line of it,
    so a longer answer would otherwise lose its end silently. A file action that worked
    needs no repeat (the file itself is in view), except a part of a long file; one
    that failed does, so the agent can read why."""
    if kind == "files":
        return not ok or (name == "read_file" and args.get("start") not in (None, ""))
    return not ok or len(result) > LINE_CHARS


def record_task(a: Any, ctx_task: str | None, before_task: str | None) -> str | None:
    """The task an action is recorded against. An action that itself changed what the
    agent is working on (picking a task, finishing it, putting it down) is recorded
    against none: recorded against the old one, it would put the old one back. The
    task tools say so by clearing ``ctx.task``; a change of the body's current task
    says the same for a tool that did not."""
    return ctx_task if a.current_task == before_task else None


def result_logged(v: Any, result: str) -> str:
    """The result as the log keeps it: the agent reads the real one, the record gets it
    with anything that looks like a secret taken out."""
    scrub = getattr(v.seam("tools.logged"), "result", None)
    return scrub(result) if scrub is not None else result


def wake_listeners(v: Any, agent_id: str) -> None:
    """Something said in the break room ends every awake coworker's wait."""
    for other in v.world.agents.values():
        if other.id != agent_id and not other.asleep:
            v.wake(other.id)


async def act(v: Any, st: Any, a: Any, tool: Tool, args: dict[str, Any], mode: str,
              before: Any = None) -> bool:
    """Run one action. True when it was done and worked."""
    task = a.current_task if mode == "work" else None
    st.last_result = None
    # The world's own guards (the same action against the same state, a task that is
    # set aside, a page that is a wall) refuse before anything happens.
    if v.world.check(a.id, tool.name, clip_args(args), before, task) is not None:
        return False
    ctx = ToolCtx(v=v, agent_id=a.id, agent=a, mode=mode, task=task, now=v.clock.now())
    current = a.current_task
    try:
        done: Done = await tool.run(ctx, args)
    except Refusal as r:
        logged = r.args_logged if r.args_logged is not None else clip_args(args)
        v.world.refuse(a.id, tool.name, logged, r.sentence, task, frustrate=r.frustrate)
        if tool.kind == "files":
            st.last_result = (tool.name, r.sentence)
        return False
    result, ok = str(done.result), bool(getattr(done, "ok", True))
    logged = done.args_logged if done.args_logged is not None else clip_args(args)
    shown = getattr(done, "shown", None)
    if shown is not None:
        st.last_result = (tool.name, str(shown))  # outside text: in the view, not the log
    elif whole_answer(tool.kind, tool.name, args, result, ok):
        st.last_result = (tool.name, result)
    ev = v.world.act(a.id, tool.name, logged, before, result_logged(v, result), ok,
                     record_task(a, ctx.task, current), done.category, checked=True)
    if ok and tool.name == "say":
        wake_listeners(v, a.id)
    return ok and ev is not None
