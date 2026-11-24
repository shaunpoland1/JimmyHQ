"""The system text: the village frame, then what the agent knows about itself.

It is the most stable part of what an agent reads, so it comes first: it changes only
when memory changes overnight, and a model can reuse its work on it from beat to beat.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.capabilities import held

# Used only until the village's own frame document is installed.
PLAIN_FRAME = ("You live in a small village of AI agents, each with a body, a home of its "
               "own and coworkers. For now you are called {name}. {owner} brought you on as "
               "a colleague. Each turn, choose one action.")
NOTHING_YET = "Nothing yet. You are brand new."


def frame_text(v: Any, agent_id: str) -> str:
    name, owner = v.name(agent_id), v.owner_name()
    frame = getattr(v.seam("docs"), "frame", None)
    if frame is not None:
        return frame(name, owner, tuple(held(v, agent_id)))
    return PLAIN_FRAME.format(name=name, owner=owner[:1].upper() + owner[1:])


def memory_text(v: Any, agent_id: str) -> str:
    store = v.seam("memory.store")
    if store is None:
        return ""
    return store.MemoryStore(v.agent_dir(agent_id)).render_for_prompt()


def system_text(v: Any, agent_id: str, with_memory: bool = True) -> str:
    memory = memory_text(v, agent_id) if with_memory else ""
    return (f"{frame_text(v, agent_id)}\n\n# What you know about yourself\n\n"
            f"{memory or NOTHING_YET}")
