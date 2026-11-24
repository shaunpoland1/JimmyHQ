"""Divergence check: compare what the body claims with what the log shows.

- Feelings repeating while actions vary  -> body_artifact   (platform fault)
- Actions repeating while feelings vary  -> rut             (agent's struggle; guards
                                                             handle it)
- A negative claim while the log shows steady progress -> claim_contradicted
                                                            (platform fault)

Works on any event log with `action` and `feeling` events. Settings are read through
the same getter the world uses.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .defaults import Getter
from .events import Event, EventLog
from .guards import action_key

NEGATIVE_CLAIMS = {"no_shape", "aimless", "stuck", "nothing_done", "wired_but_idle"}
# Rule A only applies to claims about how the day is going. State feelings like
# "tired" or "waiting_on_others" can honestly repeat for hours while work varies.
ACTIVITY_CLAIMS = NEGATIVE_CLAIMS
REJECT_KINDS = ("validator_reject",)


def window_stats(actions: list[Event], rejects: int = 0) -> dict[str, Any]:
    n = len(actions)
    if not n:
        return {"n": 0, "distinct_ratio": 0.0, "top_share": 0.0, "no_new_share": 0.0,
                "fail_rate": 0.0, "progress": 0, "top_task_share": 0.0, "rejects": rejects}
    keys = Counter(action_key(e.data["tool"], e.data.get("args")) for e in actions)
    tasks = Counter(e.data.get("task") or "-" for e in actions)
    no_new = sum(1 for e in actions if e.data.get("no_new_result"))
    fails = sum(1 for e in actions if not e.data.get("ok"))
    progress = sum(1 for e in actions if e.data.get("making") or
                   (e.data.get("ok") and not e.data.get("no_new_result")
                    and e.data.get("category", "work") == "work"))
    return {
        "n": n,
        "distinct_ratio": len(keys) / n,
        "top_share": keys.most_common(1)[0][1] / n,
        "no_new_share": no_new / n,
        "fail_rate": fails / n,
        "progress": progress,
        "top_task_share": tasks.most_common(1)[0][1] / n if tasks else 0.0,
        "rejects": rejects,
    }


def classify(s: dict[str, Any], diversity_min: float) -> dict[str, Any]:
    s["varied"] = (s["n"] >= 5 and s["distinct_ratio"] >= diversity_min
                   and s["no_new_share"] < 0.3)
    # Repeating with no new results is a rut; repeating with new results (scrolling) is not.
    s["repetitive"] = s["n"] >= 5 and (s["no_new_share"] >= 0.5 or
                                       (s["top_share"] >= 0.5 and s["progress"] < 0.5 * s["n"]))
    # Fixation: lots of refused or fruitless attempts, or most effort on one topic.
    s["fixated"] = s["rejects"] >= 2 or s["no_new_share"] >= 0.1 or s["top_task_share"] >= 0.4
    return s


def check_window(log: EventLog, agent: str, end: float, get: Getter) -> list[dict[str, Any]]:
    w = get("divergence.window_minutes") * 60
    evs = log.query(agent=agent, since=end - w, until=end)
    actions = [e for e in evs if e.kind == "action"]

    def unsupported(f: Event) -> bool:
        cited = [log.get(i) for i in f.data.get("evidence", [])]
        if cited and all(cited):
            # A body claim with citations: judge it by them, not by the window.
            return not _evidence_supports(log, f, cited)
        return True

    feelings = [e for e in evs if e.kind == "feeling" and e.data["phrase"] in ACTIVITY_CLAIMS
                and unsupported(e)]
    rejects = sum(1 for e in evs if e.kind in REJECT_KINDS)
    s = classify(window_stats(actions, rejects), get("divergence.action_diversity_min"))
    flags = []
    if len(feelings) >= 3:
        phrases = Counter(f.data["phrase"] for f in feelings)
        top, cnt = phrases.most_common(1)[0]
        share = cnt / len(feelings)
        repeat_share = get("divergence.feeling_repeat_share")
        if share >= repeat_share and s["varied"] and not s["fixated"]:
            flags.append({"type": "body_artifact", "fault": "platform", "phrase": top,
                          "share": round(share, 2), "stats": s})
        elif s["repetitive"] and share < repeat_share:
            flags.append({"type": "rut", "fault": "agent", "stats": s})
    elif s["repetitive"]:
        flags.append({"type": "rut", "fault": "agent", "stats": s})
    for f in flags:
        f.update(agent=agent, end=end,
                 evidence=[e.id for e in actions[-10:]] + [e.id for e in feelings])
    return flags


def check_claim(log: EventLog, claim: Event, get: Getter) -> dict[str, Any] | None:
    """Is this negative feeling claim contradicted by the actions just before it?"""
    if claim.data["phrase"] not in NEGATIVE_CLAIMS:
        return None
    cited = [log.get(i) for i in claim.data.get("evidence", [])]
    if cited and all(cited):
        # A body claim that cites events: check the citations themselves.
        if _evidence_supports(log, claim, cited):
            return None
        return {"type": "claim_contradicted", "fault": "platform", "agent": claim.agent,
                "claim": claim.id, "phrase": claim.data["phrase"], "end": claim.ts,
                "stats": {"cited": len(cited),
                          "reason": "cited events do not support the claim"},
                "evidence": [claim.id] + [c.id for c in cited]}
    w = get("divergence.window_minutes") * 60
    since, until = claim.ts - w, claim.ts
    actions = log.query(agent=claim.agent, kinds=["action"], since=since, until=until)
    done = log.query(agent=claim.agent, kinds=["task_done"], since=since, until=until)
    rejects = log.count(agent=claim.agent, kinds=REJECT_KINDS, since=since, until=until)
    s = classify(window_stats(actions, rejects), get("divergence.action_diversity_min"))
    s["progress"] += 3 * len(done)
    pmin = get("divergence.progress_min")
    phrase = claim.data["phrase"]
    if phrase == "stuck":
        contradicted = (not s["repetitive"] and not s["fixated"] and s["fail_rate"] < 0.2
                        and s["progress"] >= pmin)
    else:
        contradicted = s["progress"] >= pmin and (s["varied"] or len(done) > 0)
    if not contradicted:
        return None
    return {"type": "claim_contradicted", "fault": "platform", "agent": claim.agent,
            "claim": claim.id, "phrase": phrase, "end": claim.ts, "stats": s,
            "evidence": [claim.id] + [e.id for e in actions[-10:]] + [e.id for e in done]}


def run(log: EventLog, agent: str, start: float, end: float, get: Getter,
        every_minutes: int = 60) -> list[dict[str, Any]]:
    """Hourly checks over a span, plus every negative claim in it. The caller decides
    what to do with the flags (a daily review, for instance)."""
    flags: list[dict[str, Any]] = []
    t = start + every_minutes * 60
    while t <= end:
        flags.extend(check_window(log, agent, t, get))
        t += every_minutes * 60
    for claim in log.query(agent=agent, kinds=["feeling"], since=start, until=end):
        f = check_claim(log, claim, get)
        if f:
            flags.append(f)
    return flags


def _fruitless(e: Event) -> bool:
    if e.kind in REJECT_KINDS:
        return True
    return e.kind == "action" and (not e.data.get("ok") or bool(e.data.get("no_new_result")))


def _evidence_supports(log: EventLog, claim: Event, cited: list[Event]) -> bool:
    phrase = claim.data["phrase"]
    if phrase == "stuck":
        return len(cited) >= 3 and all(_fruitless(c) for c in cited)
    if phrase == "aimless":
        contributed = log.count(agent=claim.agent, kinds=["task_done", "bench_submitted"],
                                since=claim.ts - 7200, until=claim.ts)
        return not contributed
    return True
