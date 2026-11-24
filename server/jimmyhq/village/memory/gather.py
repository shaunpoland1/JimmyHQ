"""What the sleeping mind is shown: the record since the last sorted sleep, what the
agent asked to remember, and what the owner said that no memory line records yet.

Everything here reads the village's event log. Times are float seconds, like the log's.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from jimmyhq.village.memory.diary import local_time
from jimmyhq.village.memory.provenance import CHAT, OWNER

CONSOLIDATION = "consolidation"
INPUT_CHARS = 90_000
CUT_NOTE_ROOM = 150
CARRY_MOST = 20
OWNER_CHARS = 1500  # the owner's messages are shown whole; news can run long
OWN_CHAT_CHARS = 600
DAYS_MOST = 14  # diary days read for one pass, however long the gap
DAY_S = 86400.0


def succeeded(event: Any) -> bool:
    """Did this pass really sort the day? One that failed, or whose every operation was
    rejected, did not: the day is still unsorted and the next try covers it again."""
    d = event.data
    if d.get("failed"):
        return False
    n = d.get("ops") or 0
    return not (n and all(str(r).startswith("rejected") for r in (d.get("results") or [])[:n]))


def born_ts(log: Any, agent_id: str) -> float:
    born = log.query(agent=agent_id, kinds=["born"])
    return born[0].ts if born else 0.0


def since_last(log: Any, agent_id: str, nights_only: bool = False) -> float:
    """When the last successful pass ran, or the agent's birth. With ``nights_only`` a
    nap does not count, so the night sees the whole day and not just what came after
    the nap: a nap that lost something must not put it out of the night's reach."""
    done = [e for e in log.query(agent=agent_id, kinds=[CONSOLIDATION])
            if succeeded(e) and not (nights_only and e.data.get("nap"))]
    return done[-1].ts if done else born_ts(log, agent_id)


def napped(log: Any, agent_id: str, since: float) -> bool:
    return any(e.data.get("nap") and succeeded(e)
               for e in log.query(agent=agent_id, kinds=[CONSOLIDATION], since=since))


def candidates_from_log(log: Any, agent_id: str, since: float) -> list[dict[str, Any]]:
    """What the agent asked to remember, rebuilt from the log when the candidates file
    is gone (an earlier pass clears it). Each cites the action and the two before it."""
    acts = log.query(agent=agent_id, kinds=["action"], since=since)
    out = []
    for i, e in enumerate(acts):
        args = e.data.get("args")
        if e.data.get("tool") == "remember" and isinstance(args, dict):
            if args.get("section") and args.get("text"):
                out.append({"ts": round(e.ts * 1000), "section": args["section"],
                            "text": str(args["text"])[:500],
                            "evidence": [x.id for x in acts[max(0, i - 2):i + 1]]})
    return out


def days_between(since: float, now: float, tz: str) -> list[str]:
    """Every calendar day from ``since`` to ``now``, the newest ``DAYS_MOST`` of them."""
    first, last = local_time(since, tz).date(), local_time(now, tz).date()
    out = []
    while last >= first and len(out) < DAYS_MOST:
        out.append(last.isoformat())
        last -= timedelta(days=1)
    return sorted(out)


def cut_for_length(lines: list[str], thoughts: set[int], feelings: set[int],
                   chats: set[int], limit: int) -> list[str]:
    """Shorten a day that is too long for the model: the oldest thoughts go first, then
    feelings, then the oldest of everything else, and never the chat. With a plain cut
    from the top, what the owner said in the morning is the first thing lost."""
    size = sum(len(x) + 1 for x in lines)
    if size <= limit:
        return lines
    rest = [i for i in range(len(lines))
            if i not in thoughts and i not in feelings and i not in chats]
    cut: set[int] = set()
    for i in sorted(thoughts) + sorted(feelings) + rest:
        if size <= limit - CUT_NOTE_ROOM:
            break
        cut.add(i)
        size -= len(lines[i]) + 1
    n_thought = len(cut & thoughts)
    note = (f"[cut for length: {n_thought} of the day's thoughts and "
            f"{len(cut) - n_thought} other lines, oldest first; the chat is all here]")
    return [note] + [x for i, x in enumerate(lines) if i not in cut]


def record(log: Any, agent_id: str, since: float, now: float, *, tz: str = "",
           owner: str = "the owner", limit: int = INPUT_CHARS) -> tuple[str, list[str]]:
    """The record since ``since`` as text, and the days it covers (for the diary)."""
    days = days_between(since, now, tz)
    fmt = "%H:%M" if len(days) <= 1 else "%d %b %H:%M"

    def at(e: Any) -> str:
        return f"{e.id} {local_time(e.ts, tz).strftime(fmt)}"

    lines: list[str] = []
    thoughts: set[int] = set()
    feelings: set[int] = set()
    chats: set[int] = set()
    # What was said in the break room and on the work board while this agent could hear.
    for e in log.query(kinds=["breakroom", "board_post", "board_resolved"], since=since):
        who = "me" if e.agent == agent_id else e.agent
        d = e.data
        if e.kind == "breakroom":
            lines.append(f"{at(e)} break room, {who}: {str(d.get('text', ''))[:400]}")
        elif e.kind == "board_post":
            lines.append(f"{at(e)} work board, {who} ({d.get('post_kind', 'post')}): "
                         f"{str(d.get('text', ''))[:200]}")
        else:
            lines.append(f"{at(e)} work board, {who} resolved {d.get('post', '')}")
    for e in log.query(agent=agent_id, since=since):
        d = e.data
        if e.kind == "action":
            args = json.dumps(d.get("args"), ensure_ascii=False, default=str)[:120]
            lines.append(f"{at(e)} action {d.get('tool')} {args} -> "
                         f"{'ok' if d.get('ok') else 'FAILED'}: {str(d.get('result', ''))[:100]}")
        elif e.kind == "thought" and d.get("text"):
            thoughts.add(len(lines))
            lines.append(f"{at(e)} thought: {str(d['text'])[:240]}")
        elif e.kind in ("task_done", "claim_unverified", "set_aside", "resume",
                        "validator_reject", "bench_response"):
            shown = {k: v for k, v in d.items()
                     if k in ("task", "reason", "tool", "item", "verdict")}
            lines.append(f"{at(e)} {e.kind}: {json.dumps(shown, ensure_ascii=False, default=str)}")
        elif e.kind == "feeling" and d.get("phrase"):
            feelings.add(len(lines))
            lines.append(f"{at(e)} felt {d['phrase']}")
        elif e.kind == CHAT:
            from_owner = d.get("sender") == OWNER
            chats.add(len(lines))
            room = OWNER_CHARS if from_owner else OWN_CHAT_CHARS
            lines.append(f"{at(e)} chat from {owner if from_owner else 'me'}: "
                         f"{str(d.get('text', ''))[:room]}")
        elif e.kind in ("sleep", "wake", "world_paused"):
            lines.append(f"{at(e)} {e.kind}")
    text = "\n".join(cut_for_length(lines, thoughts, feelings, chats, limit))
    if len(text) > limit:  # the chat alone is over the limit: keep the newest of it
        text = "[... the start of the day was cut for length]\n" + text[-limit:]
    return text, days


def unrecorded_from_owner(log: Any, agent_id: str, items: list[dict[str, Any]],
                          heard: set[str], since: float, carry_days: int) -> list[Any]:
    """The owner's chat messages from the ``carry_days`` before this pass's own record
    that no memory line cites and no night has set aside as needing no memory. Without
    this, something said yesterday that last night failed to record is simply lost.
    The newest ``CARRY_MOST``, oldest first."""
    if carry_days <= 0:
        return []
    cited = {x for i in items for x in i.get("evidence") or []}
    out = [e for e in log.query(agent=agent_id, kinds=[CHAT],
                                since=since - carry_days * DAY_S, until=since)
           if e.ts < since and e.data.get("sender") == OWNER
           and e.id not in cited and e.id not in heard]
    return out[-CARRY_MOST:]


def met_since(log: Any, agent_id: str, since: float) -> set[str]:
    """Who the agent really dealt with since ``since``: coworkers who spoke in the break
    room, and the owner if they wrote in chat. Only these impressions may be rewritten."""
    met = {e.agent for e in log.query(kinds=["breakroom"], since=since)
           if e.agent and e.agent != agent_id}
    if any(e.data.get("sender") == OWNER
           for e in log.query(agent=agent_id, kinds=[CHAT], since=since)):
        met.add(OWNER)
    return met
