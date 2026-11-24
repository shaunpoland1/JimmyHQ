"""When the body forces a task aside, the agent writes itself a note for next time."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.beat import think
from jimmyhq.village.context.system import system_text
from jimmyhq.village.rules import rule

NOTE_TOKENS = 200
NOTE_CHARS = 500


def unhandled(v: Any, st: Any, agent_id: str) -> list[Any]:
    """Set-asides this loop has not looked at yet. On the first beat after a start,
    everything already in the log counts as looked at."""
    events = v.log.query(agent=agent_id, kinds=["set_aside"])
    if st.handled_set_asides is None:
        st.handled_set_asides = {e.id for e in events}
        return []
    new = [e for e in events if e.id not in st.handled_set_asides]
    st.handled_set_asides.update(e.id for e in new)
    return new


@rule("resume_note")
async def write(v: Any, st: Any, agent_id: str) -> int:
    """One short model call per forced set-aside: what would you try next time? A task
    the agent chose to put down already has its note. Returns how many were written."""
    a, written = v.world.agents[agent_id], 0
    for e in unhandled(v, st, agent_id):
        task = e.data["task"]
        if task not in a.parked or e.data.get("chosen"):
            continue
        tried = "\n".join(
            f"- {t['tool']}({t.get('args')}) -> {'ok' if t['ok'] else 'failed'}: "
            f"{str(t.get('result', ''))[:100]}" for t in e.data["resume_note"].get("tried", []))
        ask = (f"You were getting too frustrated with '{task}', so it has been set aside for "
               f"a while. What you tried:\n{tried or '(nothing recorded)'}\n\nIn one or two "
               "sentences, write a note to yourself: what would you try next time?")
        try:
            reply = await v.brain().chat(
                [{"role": "user", "content": ask}],
                system=system_text(v, agent_id, with_memory=False), max_tokens=NOTE_TOKENS,
                actor=f"agent:{agent_id}", caller="village.resume_note",
                **think.target(v.cfg(agent_id)))
            guess = (reply.text or "").strip()[:NOTE_CHARS]
        except Exception:  # noqa: BLE001 - no note is better than no beat
            guess = ""
        a.parked[task]["resume_note"]["next_guess"] = guess or None
        v.log.append("resume_note_written", agent_id, v.clock.now(), task=task, note=guess)
        written += 1
    return written
