"""Dreams: after a night has sorted the day, a short strange paragraph goes in the diary,
marked as a dream. It is never given back to the night that sorts memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jimmyhq.village.memory import diary
from jimmyhq.village.memory.store import MemoryStore

MOMENTS = 6
DIARY_LINES = 5
MAX_TOKENS = 220
TEMPERATURE = 1.0
PROMPT = ("Write a short, strange, dreamlike paragraph (3-4 sentences) that loosely "
          "recombines these memories and diary lines. It is a dream: things blur and "
          "change shape.\n\n{moments}\n\n{entries}")


def dreamt_this_sleep(log: Any, agent_id: str) -> bool:
    """One dream per sleep, however many passes the sleep took."""
    slept = log.last(agent=agent_id, kinds=["sleep"])
    if slept is None:
        return False
    return any(e.data.get("dream")
               for e in log.query(agent=agent_id, kinds=["consolidation"], since=slept.ts))


def material(store: MemoryStore, entries: list[str]) -> str:
    moments = "\n".join(i["text"] for i in store.section("moments")[-MOMENTS:])
    return PROMPT.format(moments=moments, entries="\n".join(entries[-DIARY_LINES:]))


async def dream(models: Any, agent_dir: Path, store: MemoryStore, entries: list[str],
                now: float, tz: str = "") -> str:
    """Ask the everyday model for a dream and write it in the diary. A failure is no
    dream, never a failed night."""
    try:
        reply = await models.beat([{"role": "user", "content": material(store, entries)}],
                                  caller="village.dream", max_tokens=MAX_TOKENS,
                                  temperature=TEMPERATURE)
    except Exception:  # noqa: BLE001
        return ""
    text = (reply.text or "").strip()
    if text:
        diary.append(agent_dir, now, text, dream=True, tz=tz)
    return text
