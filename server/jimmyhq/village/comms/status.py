"""What coworkers can see of each other: behaviour, never numbers.

A status line is derived from the body, the way you can tell a colleague is having a
rough day without seeing inside their head. It is honest: each phrase has a cause.
"""

from __future__ import annotations

from jimmyhq.village.comms.breakroom import short_tempered

WORN_OUT_BELOW = 30  # energy
GOOD_SPIRITS_ABOVE = 0.35  # mood valence


def display_name(v, agent_id: str) -> str:
    """The name an agent has settled on, or its id made readable."""
    try:
        from jimmyhq.village.memory.store import MemoryStore

        name = MemoryStore(v.agent_dir(agent_id)).locked_name()
    except Exception:  # noqa: BLE001 - no memory yet is no name yet
        name = None
    return str(name).strip() if name else agent_id.replace("-", " ").title()


def _on(v, agent_id: str, ref: str) -> str:
    """What the task is, in words a coworker understands: its title, not its ref."""
    try:
        from jimmyhq.village.comms import tasklist

        title = tasklist.title_of(v, agent_id, ref)
    except Exception:  # noqa: BLE001 - a status line never fails for want of a title
        title = ref
    return title if len(title) <= 80 else title[:79].rstrip() + "…"


def status_line(v, agent_id: str) -> str:
    w = v.world
    a = w.agents[agent_id]
    if a.asleep:
        return "asleep" if a.sleep_kind != "nap" else "napping"
    parts = []
    if short_tempered(v, agent_id):
        parts.append("short-tempered")
    elif a.needs["energy"] < WORN_OUT_BELOW:
        parts.append("worn out")
    elif w.mood(a)[0] > GOOD_SPIRITS_ABOVE:
        parts.append("in good spirits")
    doing = {"work": f"heads down on {_on(v, agent_id, a.current_task)}" if a.current_task
             else "working",
             "explore": "off exploring", "outlet": "taking a break from work",
             "rest": "resting", "social": "in the break room"}.get(a.mode, a.mode)
    parts.append(doing)
    return ", ".join(parts)


def coworkers_text(v, agent_id: str) -> str:
    others = [aid for aid in v.world.agents if aid != agent_id]
    if not others:
        return ""
    lines = [f"- {display_name(v, o)}: {status_line(v, o)}" for o in others]
    return "# Your coworkers right now\n" + "\n".join(lines) + "\n\n"
