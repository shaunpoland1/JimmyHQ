"""How many findings reach the owner's Bench, and the queue for the rest.

A finding is something an agent found and checked that the owner should know or fix: a
broken page, copy that does not match, a risk with a date on it. It needs no review
passes, and it has its own room in the day, apart from hand-overs.

- Held, not lost. A finding past today's room is held and goes on the Bench by itself
  once there is room, oldest first. Refused instead, a checked finding is simply gone.
- Earned. The day's number starts at ``findings_per_day`` and moves with the agent's
  record over the last 14 days: one more for each finding the owner noted, one fewer for
  each they dismissed, between 1 and ``findings_max_per_day``. Many good ones are
  welcome; many poor ones are not.
- Paced by the owner's reading. While ``findings_open_hold`` of this agent's findings are
  still unread, new ones wait, so what reaches the owner follows how fast they clear them.
- Urgent. A finding marked urgent skips the wait, a set number of times a day.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jimmyhq.village import files as _files
from jimmyhq.village.comms import (
    Refused,
    benchlink,
    limit,
    ms,
    owner,
    queue,
    save_queue,
    weekday_date,
)
from jimmyhq.village.comms import work as _work

RECORD_DAYS = 14
MIN_WHAT = 40
TWIN_WORDS = 4  # titles that share this many words ...
TWIN_SHARE = 0.6  # ... and this share of the shorter title are the same finding
SAME_TITLE = 0.8  # held titles this alike are one finding
QUEUE = "findings"

_STOP = set("a an and are as at be been by for from has have in is it its it's likely not of on "
            "or so that the their there this to was will with".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


# --- room ------------------------------------------------------------------------------


def record(v, agent_id: str) -> tuple[int, int]:
    """Findings the owner noted and dismissed in the last 14 days."""
    since = ms(v.clock.now() - RECORD_DAYS * 86400)
    mine = [c for c in benchlink.cards(v, agent_id, benchlink.FINDING)
            if (c["decided_ts"] or 0) >= since]
    return (sum(1 for c in mine if c["status"] == benchlink.APPROVED),
            sum(1 for c in mine if c["status"] == benchlink.DECLINED))


def finding_room(base: int, most: int, noted: int, dismissed: int) -> int:
    """Rule ``finding_room``: today's number of findings, from the recent record."""
    return max(1, min(most, base + noted - dismissed))


def allowed(v, agent_id: str) -> int:
    noted, dismissed = record(v, agent_id)
    return finding_room(limit(v, "findings_per_day"), limit(v, "findings_max_per_day"),
                        noted, dismissed)


def waiting(v, agent_id: str) -> int:
    """This agent's findings the owner has not read yet."""
    return sum(1 for c in benchlink.cards(v, agent_id, benchlink.FINDING)
               if c["status"] == benchlink.OPEN)


def today(v, agent_id: str) -> int:
    return benchlink.filed_today(v, agent_id, benchlink.FINDING)


def why_no_room(v, agent_id: str) -> tuple[str, str] | None:
    """None when a finding can go on the Bench now; otherwise the rule that holds it
    (``finding_room`` or ``findings_open_hold``) and the reason in plain words."""
    name, n = owner(v), allowed(v, agent_id)
    if today(v, agent_id) >= n:
        return "finding_room", f"today's {n} findings are on {name}'s Bench already"
    hold = limit(v, "findings_open_hold")
    if waiting(v, agent_id) >= hold:
        return "findings_open_hold", \
            f"{hold} of your findings are still waiting for {name} to read them"
    return None


def urgent_today(v, agent_id: str) -> int:
    from jimmyhq.village.comms import day_start

    return sum(1 for e in v.log.query(agent=agent_id, kinds=["bench_submitted"],
                                      since=day_start(v))
               if e.data.get("item_kind") == benchlink.FINDING and e.data.get("urgent"))


def urgent_allowed(v, agent_id: str) -> bool:
    """Rule ``urgent_findings_per_day``: whether an urgent finding may skip the wait."""
    return urgent_today(v, agent_id) < limit(v, "urgent_findings_per_day")


# --- the same finding again ------------------------------------------------------------


def finding_twin(v, agent_id: str, title: str) -> dict[str, Any] | None:
    """Rule ``finding_twin``: the agent's earlier finding that says the same thing, open
    or already decided.

    A decided card drops out of the agent's bench view, so it does not see the old one,
    files it again, and on finding both in its notes concludes that card ids change.
    Titles that share most of their words, at least four, count as the same finding."""
    mine = _words(title)
    best, score = None, 0.0
    for c in benchlink.cards(v, agent_id, benchlink.FINDING):
        if c["status"] == benchlink.REPLACED:
            continue
        shared = mine & _words(c["first_title"])
        if len(shared) < TWIN_WORDS:
            continue
        s = len(shared) / max(1, min(len(mine), len(_words(c["first_title"]))))
        if s >= TWIN_SHARE and s > score:
            best, score = c, s
    return best


def twin_sentence(v, twin: dict[str, Any]) -> str:
    name, cid = owner(v), twin["card_id"]
    when = weekday_date(v, twin["filed_ts"] / 1000)
    head = f"Nothing was put on the Bench: this looks like 1 finding you already reported, " \
           f"{cid} (\"{twin['title'][:120]}\")"
    if twin["status"] in benchlink.LIVE:
        return (f"{head}, which has been on {name}'s Bench since {when}. If something about "
                f"it changed, use correct_finding on {cid}.")
    word = {benchlink.APPROVED: "noted it",
            benchlink.DECLINED: "said it isn't a problem"}.get(twin["status"], "decided it")
    return (f"{head}, on {when}; {name} {word}"
            + (f": \"{twin['note'][:200]}\"" if twin["note"] else "")
            + f". Card ids never change. If something new has happened since, report it again "
            f"and say in it what changed since {cid}, or tell them with message_owner.")


def same_title(a: str, b: str) -> bool:
    """The same finding under nearly the same words: "in the first 48h" for "in first
    48h". Held twice, the agent finds the double and has no way to take one out."""
    x, y = (set(re.findall(r"[a-z0-9$%]+", t.lower())) for t in (a, b))
    return bool(x) and len(x & y) / len(x | y) >= SAME_TITLE


# --- reporting -------------------------------------------------------------------------


def _real_files(v, agent_id: str, paths: object) -> dict[str, Path]:
    given = paths if isinstance(paths, list) else [paths] if paths else []
    cleaned = list(dict.fromkeys(_work.clean(p) for p in given if _work.clean(p)))
    fs = _files.for_agent(v, agent_id)
    out = {}
    for p in cleaned:
        _work.digest(v, agent_id, p, "put on the Bench")  # refuses a missing file
        out[p] = fs.path(p)
    return out


def file(v, agent_id: str, title: str, what: str, why: str, fix: str = "",
         files: object = None, urgent: bool = False, task: str | None = None) -> dict[str, Any]:
    """Put a finding on the Bench now."""
    if benchlink.duplicate_open_title(v, agent_id, benchlink.FINDING, title):
        raise Refused("finding_duplicate",
                      f"Nothing was put on the Bench: 1 finding with that title is already "
                      f"on {owner(v)}'s Bench, waiting. If something about it changed, use "
                      "correct_finding on it.")
    return benchlink.file(v, agent_id, benchlink.FINDING, title, body=what, why=why,
                          details=[("What would fix it", fix)],
                          files=_real_files(v, agent_id, files), urgent=urgent, task=task)


def queued(v, agent_id: str) -> list[dict[str, Any]]:
    """Findings held until there is room, oldest first."""
    return queue(v, agent_id, QUEUE)


def hold(v, agent_id: str, title: str, what: str, why: str, fix: str = "",
         files: object = None) -> dict[str, Any]:
    """Hold a finding until there is room. The same title again, or nearly the same,
    replaces the held one in its place: the newer, re-checked version goes."""
    name = owner(v)
    if benchlink.duplicate_open_title(v, agent_id, benchlink.FINDING, title):
        raise Refused("finding_duplicate",
                      f"Nothing was held: 1 finding with that title is already on {name}'s "
                      "Bench, waiting. If something about it changed, use correct_finding "
                      "on it.")
    paths = list(_real_files(v, agent_id, files))
    items = queued(v, agent_id)
    old = next((n for n, q in enumerate(items) if same_title(q["title"], title)), None)
    most = limit(v, "findings_max_per_day")
    if old is None and len(items) >= most:
        raise Refused("findings_held_full",
                      f"Nothing was held: you already have {len(items)} findings held for "
                      f"{name}, as many as they take in a day ({most}). Keep this one in your "
                      "notes, or report a held one again by its title to replace it.",
                      frustrate=False)
    now = v.clock.now()
    entry = {"title": " ".join(title.split())[:300], "what": what.strip(), "why": why.strip(),
             "fix": fix.strip(), "files": paths,
             "queued_at": items[old]["queued_at"] if old is not None else now}
    if old is None:
        items.append(entry)
        position = len(items)
    else:
        items[old] = entry
        position = old + 1
        items = [q for n, q in enumerate(items)
                 if n <= old or not same_title(q["title"], title)]  # a double goes
    save_queue(v, agent_id, QUEUE, items)
    v.log.append("finding_queued", agent_id, now, title=entry["title"], position=position,
                 replaced=old is not None)
    return dict(entry, position=position, replaced=old is not None)


def flush(v, agent_id: str) -> list[dict[str, Any]]:
    """Put held findings on the Bench while there is room, oldest first."""
    items = queued(v, agent_id)
    if not items:
        return []
    out, keep = [], []
    for q in items:
        if why_no_room(v, agent_id):
            keep.append(q)
            continue
        try:
            out.append(file(v, agent_id, q["title"], q["what"], q["why"], q.get("fix", ""),
                            q.get("files") or []))
        except Refused as e:  # the same finding is already open, or its file is gone
            v.log.append("finding_dropped", agent_id, v.clock.now(), title=q["title"],
                         reason=e.sentence)
    if len(keep) != len(items):
        save_queue(v, agent_id, QUEUE, keep)
    return out
