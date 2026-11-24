"""Single lines that name a pattern the agent cannot see from inside it.

Each one states what the log shows and leaves the choice to the agent. They are shown
only while they are true.
"""

from __future__ import annotations

import math
from typing import Any

from jimmyhq.village.rules import rule


def waiting_count(v: Any, agent_id: str) -> int:
    """How many things of this agent's are with the owner or queued for them."""
    return (len(v.ask("comms.benchlink", "waiting", agent_id, default=[]) or [])
            + len(v.ask("comms.work", "queued", agent_id, default=[]) or [])
            + len(v.ask("comms.findings", "queued", agent_id, default=[]) or []))


@rule("circling", "village.context.circling_revisits")
def circling_text(v: Any, agent_id: str) -> str:
    """Say so when nothing is waiting for the owner and the agent keeps going back over
    the same ground. With an empty bench and a finished list an agent can spend hours
    rereading its own plan; this names the pattern."""
    if waiting_count(v, agent_id):
        return ""
    window, most = int(v.ctx_s("circling_window")), int(v.ctx_s("circling_revisits"))
    acts = v.tail(agent_id, ["action"], window, since=v.clock.now() - 2 * 3600)
    if len(acts) < window:
        return ""
    seen: set[tuple[str, str]] = set()
    repeats = 0
    for e in acts:
        tool, args = e.data.get("tool"), e.data.get("args") or {}
        target = str(args.get("path") if tool == "read_file"
                     else args.get("url") if tool == "open_page" else "")
        if tool in ("read_file", "open_page") and target:
            repeats += (tool, target) in seen
            seen.add((tool, target))
        elif e.data.get("making") and args.get("path"):
            # reading a file again after changing it is work
            seen.discard(("read_file", str(args["path"])))
    if repeats < most:
        return ""
    return (f"Nothing of yours is waiting for {v.owner_name()}, and {repeats} of your last "
            f"{len(acts)} actions went back to a file or page you had already looked at. If "
            "your list is done, checking it again won't change that. This is the time for "
            "something bigger than a check: decide what the next useful piece of work is, "
            "write it down in one line, and start it (start_own_task puts it on your list as "
            "yours).")


@rule("waiting_is_not_done", "village.context.waiting_quiet")
def waiting_text(v: Any, agent_id: str) -> str:
    """Work waiting on the owner is not a finished day. Shown in daytime, when
    something of the agent's waits or is queued and its last few actions made nothing."""
    a = v.world.agents[agent_id]
    if a.asleep or v.world._hour(a) >= v.S("sleep.chosen_rest_from_hour"):
        return ""
    waiting = waiting_count(v, agent_id)
    if not waiting:
        return ""
    quiet = int(v.ctx_s("waiting_quiet"))
    acts = v.tail(agent_id, ["action"], quiet, since=v.clock.now() - 2 * 3600)
    if len(acts) < quiet or any(e.data.get("making") for e in acts):
        return ""
    owner = v.owner_name()
    return (f"{waiting} thing{'s' if waiting != 1 else ''} of yours "
            f"{'are' if waiting != 1 else 'is'} with {owner} or queued. That is waiting, not a "
            "finished day: an answer may take days, and what is wanted is an agent that keeps "
            "finding useful things on its own. Checking the same lists again won't move them. "
            "Pick the next useful piece of work nobody asked for (start_own_task puts it on "
            "your list) and start it.")


def news_heads(v: Any, agent_id: str, limit: int) -> str:
    """The bold first sentences of the newest dated section of the agent's copy of the
    village's changes file, as one line."""
    heads = v.ask("docs", "news_heads", agent_id, limit, default=[]) or []
    return " ".join(str(h).strip() for h in heads)


@rule("news_line", "village.context.news_heads")
def news_text(v: Any, agent_id: str) -> str:
    """One line while the village's changes file has news the agent has not read. It
    names the newest changes, because a line that only says "there is news" is passed
    over while the agent goes on reasoning from what it remembers."""
    news = v.tail(agent_id, ["village_news"], 1)
    if not news:
        return ""
    for e in v.log.query(agent=agent_id, kinds=["action"], since=news[-1].ts):
        d = e.data
        path = str((d.get("args") or {}).get("path", "")).strip().lstrip("/")
        if d.get("tool") == "read_file" and d.get("ok") and path.endswith("village/changes.md"):
            return ""
    heads = news_heads(v, agent_id, int(v.ctx_s("news_heads")))
    return ("News from the village: some things on your computer have been fixed or changed"
            + (f" ({heads})" if heads else "") +
            ". village/changes.md says more (it may correct things you remember about your "
            "tools).")


@rule("review_wait", "village.review.min_distance_min")
def review_wait_text(v: Any, agent_id: str) -> str:
    """Drafts still too new to review, and the minute review opens for each, so the
    agent does not have to find out by being refused."""
    waits = v.ask("comms.work", "review_wait", agent_id, default=[]) or []
    out = [f"{w['path']} from {v.when(math.ceil(w['opens'] / 60) * 60):%H:%M}" for w in waits]
    return ("Too new to review yet (review_work opens): " + "; ".join(out) + ".") if out else ""
