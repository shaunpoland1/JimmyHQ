"""Drafts, reviews and hand-overs: when is a piece of work final?

A colleague does not hand over a first draft. The path for a piece of work:

1. First pass: the agent writes it in its files. That is a draft.
2. Review, with distance: once the draft is old enough, the agent rereads it against the
   checklist in ``village/reviewing.md``, fixes what it finds, and records the pass with
   ``review_work``. A pass counts for the text as it is at that moment; any later edit
   needs another pass.
3. Hand-over: ``hand_over`` puts it on the owner's Bench with a short version (to move
   fast) and the details (to audit later). It is refused if a file changed since its last
   review pass.
4. Final means the owner said so. The exact files handed over are kept in the agent's
   folder under ``deliverables/<card id>/`` and in the Vault.

The day's hand-over limit protects the owner's attention, not the agent's work. Past it a
ready hand-over is held and goes on the Bench by itself the next day, oldest first.
Refusing it instead costs an afternoon of retries and a hand-kept "queue for tomorrow".
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from jimmyhq.village import files as _files
from jimmyhq.village.comms import (
    Refused,
    benchlink,
    clock_time,
    limit,
    owner,
    queue,
    save_queue,
)
from jimmyhq.village.comms.tasklist import asked_by_owner

KINDS = ("email", "document", "other")
MAX_TLDR = 600
MIN_DETAILS = 80
MIN_NOTES = 40
PASSES_COUNTED = 2  # passes on the same text that are recorded; more is rereading
WRITES = ("write_file", "add_to_file", "edit_file")
QUEUE = "handovers"
DISTANCE = "village.review.min_distance_min"


def clean(path: object) -> str:
    """A path as the log and the cards hold it. Empty when it is not a usable path."""
    try:
        return _files.clean(path)
    except _files.FilesError:
        return ""


def real_file(v, agent_id: str, path: str) -> Path | None:
    """The real file behind a path as the agent writes it: in its own files, or in
    ``shared/``. A draft a coworker put in a shared folder can be reviewed where it is."""
    return _files.for_agent(v, agent_id).file(path)


def digest(v, agent_id: str, path: str, verb: str = "recorded") -> str:
    """A short fingerprint of a file as it is now. A missing file names the files with
    similar names: a draft named by the wrong folder is otherwise written again."""
    p = real_file(v, agent_id, path)
    if p is None:
        root = v.agent_dir(agent_id) / "home" / "files"
        near = _files.similar_names(root, clean(path)) if root.is_dir() else []
        raise Refused("file_missing",
                      f"Nothing was {verb}: there is no file {path}."
                      + (f" The {len(near)} files with similar names are: {', '.join(near)}."
                         if near else "")
                      + " Use list_files to see where it is, and give the path from your top "
                      "folder.")
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def first_written(v, agent_id: str, path: str) -> float | None:
    path = clean(path)
    for e in v.log.query(agent=agent_id, kinds=["action"]):
        d = e.data
        if d.get("ok") and d.get("tool") in WRITES \
                and clean((d.get("args") or {}).get("path")) == path:
            return e.ts
    return None


def passes(v, agent_id: str, path: str) -> list[Any]:
    path = clean(path)
    return [e for e in v.log.query(agent=agent_id, kinds=["work_reviewed"])
            if e.data["path"] == path]


def distance_s(v) -> float:
    return float(v.core.settings.get(DISTANCE)) * 60


def review_distance(v, agent_id: str, path: str) -> None:
    """Rule ``review_distance``: rereading straight after writing misses things."""
    first = first_written(v, agent_id, path)
    gap, now = distance_s(v), v.clock.now()
    if first is not None and now - first < gap:
        # The time is shown rounded up to the whole minute: told "from 09:41" at
        # 09:41:20, an agent tries at once and is refused again.
        opens = math.ceil((first + gap) / 60) * 60
        raise Refused("review_distance",
                      f"Nothing was recorded: {path} was first written "
                      f"{(now - first) // 60:.0f} minutes ago and a review needs "
                      f"{gap / 60:.0f} minutes of distance. Review it from "
                      f"{clock_time(v, opens)}; work on something else until then.")


def review(v, agent_id: str, path: str, kind: str, notes: str) -> dict[str, Any]:
    """Record a review pass of a file as it is now. Returns ``{event, pass, hash}``, with
    ``unchanged`` and ``at`` when the text already has its two passes (rule
    ``third_pass_unchanged``: then nothing is recorded)."""
    path = clean(path) or str(path)
    if kind not in KINDS:
        kind = "other"
    h = digest(v, agent_id, path)
    review_distance(v, agent_id, path)
    said = notes.strip()
    if len(said) < MIN_NOTES:
        raise Refused("review_notes",
                      f"Nothing was recorded: your notes are {len(said)} characters and a "
                      f"review pass needs at least {MIN_NOTES}. Say what you checked and "
                      "what you changed, in a few sentences, so the pass means something.")
    ps = passes(v, agent_id, path)
    same = [e for e in ps if e.data["hash"] == h]
    if len(same) >= PASSES_COUNTED:
        return {"event": same[-1].id, "pass": len(ps), "hash": h, "unchanged": True,
                "at": same[-1].ts}
    n = len(ps) + 1
    ev = v.log.append("work_reviewed", agent_id, v.clock.now(), path=path, work_kind=kind,
                      hash=h, notes=said[:2000], n=n)
    return {"event": ev.id, "pass": n, "hash": h}


def review_wait(v, agent_id: str) -> list[dict[str, Any]]:
    """Drafts still too new to review: ``{path, opens}`` with the time (seconds) review
    opens for each, rounded up to the minute. Shown every beat, or the time is only in
    the last refusal and the agent keeps trying."""
    now, gap = v.clock.now(), distance_s(v)
    seen: set[str] = set()
    out = []
    for e in v.log.query(agent=agent_id, kinds=["action"], since=now - gap):
        d = e.data
        if not (d.get("ok") and d.get("tool") in WRITES):
            continue
        path = clean((d.get("args") or {}).get("path"))
        if not path or path in seen:
            continue
        seen.add(path)
        first = first_written(v, agent_id, path)
        if first is not None and now - first < gap:
            out.append({"path": path, "opens": math.ceil((first + gap) / 60) * 60})
    return out


# --- handing over ----------------------------------------------------------------------


def reviewed_as_is(v, agent_id: str, paths: list[str]) -> list[str]:
    """Rule ``reviewed_as_is``: what stops a hand-over, per file (empty when it can go).
    Each file needs a review pass on the text as it is now."""
    out = []
    for path in paths:
        try:
            h = digest(v, agent_id, path, "handed over")
        except Refused as e:
            out.append(e.sentence.removeprefix("Nothing was handed over: "))
            continue
        ps = passes(v, agent_id, path)
        if not ps:
            out.append(f"{path} has 0 review passes (review_work it first, with "
                       "village/reviewing.md).")
        elif ps[-1].data["hash"] != h:
            out.append(f"{path} changed after its last review pass (pass {len(ps)}); review "
                       "it again as it is now.")
    return out


def check(v, agent_id: str, paths: object, tldr: str, details: str) -> list[str]:
    """Clean paths if the hand-over can go, else the refusal that says what is missing."""
    given = paths if isinstance(paths, list) else [paths] if paths else []
    cleaned = list(dict.fromkeys(clean(p) for p in given if clean(p)))
    if not cleaned:
        raise Refused("handover_files",
                      "Nothing was handed over: you named 0 files. Name the file or files "
                      "you are handing over, by their paths.")
    problems = reviewed_as_is(v, agent_id, cleaned)
    if problems:
        raise Refused("reviewed_as_is",
                      f"Nothing was handed over: {len(problems)} of the {len(cleaned)} files "
                      "are not ready. " + " ".join(problems))
    name = owner(v)
    if not tldr.strip() or len(tldr) > MAX_TLDR:
        raise Refused("tldr_size",
                      f"Nothing was handed over: the short version is {len(tldr.strip())} "
                      f"characters and it takes 1 to {MAX_TLDR}. Write the two or three "
                      f"sentences {name} can act on.")
    if len(details.strip()) < MIN_DETAILS:
        raise Refused("details_short",
                      f"Nothing was handed over: the details are {len(details.strip())} "
                      f"characters and need at least {MIN_DETAILS}. They are for checking "
                      "your work later: say what it is and who it is for, what you checked, "
                      "and your sources.")
    return cleaned


def open_handover(v, agent_id: str, paths: list[str]) -> dict[str, Any] | None:
    """The agent's hand-over still on the Bench that already covers these files."""
    want = {clean(p) for p in paths if clean(p)}
    if not want:
        return None
    return next((c for c in benchlink.cards(v, agent_id, benchlink.WORK)
                 if c["status"] in benchlink.LIVE and want <= set(c["paths"])), None)


def handovers_today(v, agent_id: str) -> int:
    return benchlink.filed_today(v, agent_id, benchlink.WORK)


def _hashes(v, agent_id: str, paths: list[str]) -> dict[str, str]:
    return {p: digest(v, agent_id, p, "handed over") for p in paths}


def hand_over(v, agent_id: str, title: str, paths: object, tldr: str, details: str,
              unsure: str = "", needs: str = "", task: str | None = None) -> dict[str, Any]:
    """Put finished work on the Bench. Returns the card's row, with ``updated`` when an
    open card for the same files was revised in place and ``refiled`` when its files had
    changed, so a new card took its place. Neither counts as a new hand-over: a duplicate
    must not use up the day's last one."""
    cleaned = check(v, agent_id, paths, tldr, details)
    reviews = {p: len(passes(v, agent_id, p)) for p in cleaned}
    hashes = _hashes(v, agent_id, cleaned)
    summary, more = benchlink.summary_of(tldr)
    notes = [*more, ("Not sure about", unsure.strip()[:1500]),
             ("Needs from you", needs.strip()[:1500]),
             ("Review passes", "; ".join(f"{p}: {n}" for p, n in reviews.items()))]
    extra = {"tldr": tldr.strip(), "unsure": unsure.strip()[:1500],
             "needs": needs.strip()[:1500], "reviews": reviews, "hashes": hashes}
    same = open_handover(v, agent_id, cleaned)
    fs = _files.for_agent(v, agent_id)
    real = {p: fs.path(p) for p in cleaned}
    if same and all(same["extra"].get("hashes", {}).get(p) == h for p, h in hashes.items()):
        card = dict(benchlink.revise(v, same, title=title, summary=summary, body=details.strip(),
                                     note="The agent revised the notes on this hand-over.",
                                     extra=extra), updated=True)
    elif same:
        returned = f" It was sent back with this note: \"{same['note'][:500]}\"" \
            if same["note"] else ""
        card = benchlink.file(
            v, agent_id, benchlink.WORK, title, summary=summary, body=details,
            details=[*notes, ("Replaces", f"An earlier version of this hand-over, card "
                                          f"{same['card_id']}.{returned}")],
            files=real, task=task or same["extra"].get("task"), extra=extra,
            replaces=same["card_id"])
        benchlink.replace(v, same, f"Replaced by a revised version, card {card['card_id']}.")
        card = dict(card, refiled=True)
    else:
        card = benchlink.file(v, agent_id, benchlink.WORK, title, summary=summary, body=details,
                              details=notes, files=real, task=task, extra=extra)
    # A held entry for the same files is now stale. Left in the queue, the next flush
    # would overwrite the card with the older title and notes.
    held = queued(v, agent_id)
    left = [q for q in held if not set(q["paths"]) & set(cleaned)]
    if left != held:
        save_queue(v, agent_id, QUEUE, left)
    return card


# --- the queue -------------------------------------------------------------------------


def queued(v, agent_id: str) -> list[dict[str, Any]]:
    """Hand-overs held for the next day, oldest first."""
    return queue(v, agent_id, QUEUE)


def queued_position(v, agent_id: str, path: str) -> int | None:
    """Where this file sits in the queue (1-based), or None when it is not held."""
    path = clean(path)
    return next((i + 1 for i, q in enumerate(queued(v, agent_id)) if path in q["paths"]), None)


def over_day_limit(v, agent_id: str, paths: object, task: str | None) -> bool:
    """Rule ``handovers_per_day``: whether a new hand-over must wait for tomorrow. Work
    the owner asked for is not held, and neither is a revision of a card already there."""
    given = paths if isinstance(paths, list) else [paths] if paths else []
    if asked_by_owner(v, agent_id, task) or open_handover(v, agent_id, given):
        return False
    return handovers_today(v, agent_id) >= limit(v, "handovers_per_day")


def hold(v, agent_id: str, title: str, paths: object, tldr: str, details: str,
         unsure: str = "", needs: str = "", task: str | None = None) -> dict[str, Any]:
    """Hold a ready hand-over for the next day. Same checks as ``hand_over``. One that
    shares a file with a held one replaces it in its place, and says so: an answer that
    reads like a first queueing each time gets the same files handed over six times."""
    cleaned = check(v, agent_id, paths, tldr, details)
    items = queued(v, agent_id)
    old = next((i for i, q in enumerate(items) if set(q["paths"]) & set(cleaned)), None)
    most = limit(v, "handovers_per_day")
    if old is None and len(items) >= most:
        raise Refused("handover_queue_full",
                      f"Nothing was queued: your queue for tomorrow already holds "
                      f"{len(items)}, as many as {owner(v)} takes in a day ({most}). Keep "
                      "this one in your files, or hand over the same files as a held one to "
                      "replace it.", frustrate=False)
    now = v.clock.now()
    entry = {"title": " ".join(title.split())[:300], "paths": cleaned, "tldr": tldr.strip(),
             "details": details.strip(), "unsure": unsure, "needs": needs, "task": task,
             "queued_at": items[old]["queued_at"] if old is not None else now, "problem": ""}
    if old is None:
        items.append(entry)
        position = len(items)
    else:
        items = [q for i, q in enumerate(items) if i == old or not set(q["paths"]) & set(cleaned)]
        position = next(i for i, q in enumerate(items) if set(q["paths"]) & set(cleaned)) + 1
        items[position - 1] = entry
    save_queue(v, agent_id, QUEUE, items)
    v.log.append("handover_queued", agent_id, now, title=entry["title"], paths=cleaned,
                 position=position, replaced=old is not None)
    return dict(entry, position=position, replaced=old is not None)


def flush(v, agent_id: str) -> list[dict[str, Any]]:
    """Put held hand-overs on the Bench while today's limit allows, oldest first. A held
    file that changed since its review stays held with the problem noted, so the agent
    can review it again."""
    items = queued(v, agent_id)
    if not items:
        return []
    cap, out, keep = limit(v, "handovers_per_day"), [], []
    for q in items:
        if handovers_today(v, agent_id) >= cap and not open_handover(v, agent_id, q["paths"]):
            keep.append(q)
            continue
        try:
            out.append(hand_over(v, agent_id, q["title"], q["paths"], q["tldr"], q["details"],
                                 q.get("unsure", ""), q.get("needs", ""), q.get("task")))
        except Refused as e:
            keep.append(dict(q, problem=e.sentence))
    if keep != items:
        save_queue(v, agent_id, QUEUE, keep)
    return out


__all__ = ["KINDS", "MAX_TLDR", "check", "clean", "digest", "first_written",
           "flush", "hand_over", "handovers_today", "hold", "open_handover", "over_day_limit",
           "passes", "queued", "queued_position", "real_file", "review", "review_distance",
           "review_wait", "reviewed_as_is"]
