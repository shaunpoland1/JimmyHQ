"""Builds what the agent reads each beat.

Ordered so the most stable text comes first and what changes every beat comes last,
which lets a model reuse its work on the unchanged start:

    system: the village frame, then what the agent knows about itself
    user:   an urgent call; the list; right now; the owner's chat; messages from
            outside (marked untrusted); forms it asked to send from its browser; coworkers, break
            room, board, impressions; last actions; working files; folders; the last
            answer in full; the diary; feelings; the time
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.context import (
    browser_view,
    clock_view,
    files_view,
    outside_view,
    situation,
    talk_view,
)
from jimmyhq.village.context.system import system_text
from jimmyhq.village.rules import rule

__all__ = ["CUT_NOTE", "cut_to_cap", "system_text", "user_text"]

MODE_WORDS = {
    "work": "You feel like getting things done.",
    "explore": "You're curious. Something new would feel good right now, not your tasks.",
    "outlet": "You're frustrated. Do something with no stakes for a bit: a small puzzle, "
              "something silly in your files.",
    "social": "You'd like some company.",
    "rest": "You're low on energy and resting.",
}

FEELING_WORDS = {
    "tired": "tired",
    "stuck": "stuck: several tries in a row on the same task have gone nowhere",
    "waiting_on_others": "waiting on someone else",
    "lonely": "lonely",
    "restless": "restless after a long stretch of routine work",
    "aimless": "aimless: nothing you've done in the last two hours has added anything",
    "useful": "useful: you finished something recently",
    "settled": "settled",
}

CUT_NOTE = "\n[... cut: this moment's view was too long to show in full]"


@rule("total_cap", "village.context.total_cap")
def cut_to_cap(text: str, cap: int) -> str:
    """Never cut silently: a view over the cap is cut and says so."""
    return text if len(text) <= cap else text[:cap] + CUT_NOTE


def feelings_text(feelings: list[dict[str, Any]]) -> str:
    return "; ".join(FEELING_WORDS.get(f["phrase"], f["phrase"])
                     for f in feelings) or "nothing in particular"


def user_text(v: Any, agent_id: str, feelings: list[dict[str, Any]],
              last_result: tuple[str, str] | None = None) -> str:
    a = v.world.agents[agent_id]
    diary_count, diary = situation.diary_text(v, agent_id)
    text = (
        talk_view.call_text(v, agent_id)
        + f"# Your priorities and tasks\n{situation.priorities_text(v, agent_id)}\n\n"
        + f"# Right now\n{situation.right_now_text(v, agent_id)}\n\n"
        + talk_view.chat_text(v, agent_id)
        + outside_view.outside_text(v, agent_id)
        + browser_view.browser_text(v, agent_id)
        + talk_view.social_text(v, agent_id)
        + f"# Your last actions\n{situation.recent_actions_text(v, agent_id)}\n\n"
        + files_view.working_files_text(v, agent_id)
        + files_view.folders_text(v, agent_id)
        + files_view.last_result_text(last_result, int(v.ctx_s("last_result_chars")))
        + f"# Your diary today{diary_count}\n{diary}\n\n"
        + f"# How you feel\n{feelings_text(feelings)}. {MODE_WORDS.get(a.mode, '')}\n\n"
        + f"# Time\n{clock_view.time_text(v, agent_id)}\n\n"
        + "Choose one action."
    )
    return cut_to_cap(text, int(v.ctx_s("total_cap")))
