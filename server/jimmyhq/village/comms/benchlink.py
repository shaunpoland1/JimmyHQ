"""The village's cards on the owner's Bench, and what the owner's decisions do.

An agent files four kinds of card, all as ``agent:<id>``:

- ``village.work``: finished work handed over. Approve is final; "not yet" sends the
  owner's note back for a revision; no closes it.
- ``village.finding``: something the agent checked and the owner should know. Approve
  means noted, "not yet" asks for more, no means it is not a problem.
- ``village.priority``: a new standing priority the agent suggests. Approve adds it.
- ``village.done``: a task the agent finished. "Got it" closes the card; Reopen puts the
  task back on the agent's list with the owner's reason.
- ``village.hand``: a request to use an integration that needs the owner's yes. The
  owner's click is not the answer the agent waits for: the hands executor performs or
  closes the request and prints what became of it (``hand.outcome``), and that is what
  ``apply_outcome`` turns into the agent's answer.

The Bench owns the cards. This module keeps one row per card in ``village.db`` (table
``cards``), which is what the agent's view and the day's limits read, and it is the only
thing a decision is trusted against: a ``bench.decided`` event is acted on only when its
``filed_by`` matches the row. Any write token can file a card under any kind, so the
kind alone proves nothing.

A card that needs the owner (work, a finding, a suggested priority) is something the
agent waits on. Filing one tells the body (``World.submit``): a little purpose at once,
because the agent did its part, and a little frustration for each hour the answer takes,
within the world's caps. The owner's decision ends the wait (``World.respond``), and an
approval gives the rest of the purpose. So an agent's wellbeing does not hang on how fast
the owner answers, and waiting is still felt. A ``village.done`` card only informs: it is
not waited on. What an agent waits on is rebuilt from the ``cards`` table on start.

Times in the table are unix milliseconds.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

from jimmyhq import bench, storeversion
from jimmyhq.tape.store import connect
from jimmyhq.village.comms import Refused, actor, day_start, local, ms, owner
from jimmyhq.village.dbfile import STORE
from jimmyhq.village.rules import rule

WORK, FINDING, PRIORITY, DONE = "village.work", "village.finding", "village.priority", \
    "village.done"
HAND = "village.hand"
KINDS = (WORK, FINDING, PRIORITY, DONE, HAND)
NEEDS_OWNER = (WORK, FINDING, PRIORITY, HAND)  # the agent waits on these; a done card informs
# Only these can be corrected (``correct``), so only their first body is kept in
# ``village.db``. A request's card is never copied here: its values belong to hands.db.
CORRECTABLE = (WORK, FINDING)
SOURCE = "Village"
REOPEN = "reopen"
TITLE_CHARS = 200
SUMMARY_CHARS = 300
DETAIL_CHARS = 2000
NOTE_CHARS = 2000
CORRECTION_MIN, CORRECTION_MAX = 20, 1500

# A card's life in the table.
OPEN, RETURNED, APPROVED, DECLINED, REOPENED, REPLACED = \
    "open", "returned", "approved", "declined", "reopened", "replaced"
LIVE = (OPEN, RETURNED)  # still on the Bench

_CARD_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")



# --- the database ------------------------------------------------------------------

_open: dict[str, tuple[sqlite3.Connection, threading.Lock]] = {}
_open_lock = threading.Lock()


def db(v) -> tuple[sqlite3.Connection, threading.Lock]:
    """This process's connection to ``village.db`` for the comms tables, made on first
    use. The file is made, or brought up to date, here: its tables and their version are
    declared in ``village/dbfile.py``."""
    path = str(Path(v.log.path))
    with _open_lock:
        held = _open.get(path)
        if held is None:
            conn = connect(path)
            storeversion.prepare(conn, STORE)
            held = _open[path] = (conn, threading.Lock())
        return held


def close(v=None) -> None:
    """Close the connection for one village, or all of them."""
    with _open_lock:
        keys = [str(Path(v.log.path))] if v is not None else list(_open)
        for key in keys:
            held = _open.pop(key, None)
            if held:
                held[0].close()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["paths"] = json.loads(out["paths"])
    out["extra"] = json.loads(out["extra"])
    out["urgent"] = bool(out["urgent"])
    return out


def _select(v, where: str, params: tuple) -> list[dict[str, Any]]:
    conn, lock = db(v)
    with lock:
        rows = conn.execute(f"SELECT * FROM cards WHERE {where} ORDER BY filed_ts, card_id",  # noqa: S608
                            params).fetchall()
    return [_row(r) for r in rows]


def _set(v, card_id: str, **fields: Any) -> None:
    if "paths" in fields:
        fields["paths"] = json.dumps(fields["paths"])
    if "extra" in fields:
        fields["extra"] = json.dumps(fields["extra"], ensure_ascii=False)
    if "urgent" in fields:
        fields["urgent"] = int(bool(fields["urgent"]))
    names = ", ".join(f"{k} = ?" for k in fields)
    conn, lock = db(v)
    with lock:
        conn.execute(f"UPDATE cards SET {names} WHERE card_id = ?",  # noqa: S608
                     (*fields.values(), card_id))


# --- reading -------------------------------------------------------------------------


def card_id_of(text: object) -> str:
    """A card id as an agent typed it: spaces, brackets and case set aside."""
    return str(text or "").strip().strip("[]()\"'`.").upper()


def get(v, card_id: str) -> dict[str, Any] | None:
    rows = _select(v, "card_id = ?", (card_id_of(card_id),))
    return rows[0] if rows else None


def looks_like_card(v, text: str) -> bool:
    return bool(_CARD_ID.fullmatch(card_id_of(text))) or get(v, text) is not None


def cards(v, agent_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    """Every card this agent ever filed, oldest first."""
    if kind:
        return _select(v, "agent = ? AND kind = ?", (agent_id, kind))
    return _select(v, "agent = ?", (agent_id,))


def view(v, agent_id: str, hours: float = 24) -> list[dict[str, Any]]:
    """The agent's own cards for its bench view: those still on the Bench, and those
    decided in the last day. Decided cards drop out after that, which is why a finding is
    also checked against every earlier one before it is filed."""
    cutoff = ms(v.clock.now() - hours * 3600)
    return [c for c in cards(v, agent_id)
            if c["status"] in LIVE or (c["decided_ts"] or 0) >= cutoff]


def waiting(v, agent_id: str) -> list[dict[str, Any]]:
    """Cards the owner has not answered yet."""
    return [c for c in cards(v, agent_id) if c["status"] == OPEN and c["kind"] != DONE]


def tally(v, agent_id: str) -> dict[str, dict[str, int]]:
    """The lifetime count of this agent's cards, by kind and status."""
    out: dict[str, dict[str, int]] = {}
    for c in cards(v, agent_id):
        by = out.setdefault(c["kind"], {})
        by[c["status"]] = by.get(c["status"], 0) + 1
    return out


def filed_today(v, agent_id: str, kind: str) -> int:
    """New cards of one kind since the start of the owner's day. A revision of a card
    that was already there is not a new card."""
    since = day_start(v)
    return sum(1 for e in v.log.query(agent=agent_id, kinds=["bench_submitted"], since=since)
               if e.data.get("item_kind") == kind)


# --- filing --------------------------------------------------------------------------


def _line(text: str, most: int) -> str:
    """One line of ordinary text, as the Bench wants a title or a summary."""
    out = " ".join("".join(ch if ord(ch) >= 32 and ord(ch) != 127 else " "
                           for ch in str(text or "")).split())
    return out if len(out) <= most else out[:most - 1].rstrip() + "…"


def _details(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"label": label, "text": str(text).strip()[:DETAIL_CHARS]}
            for label, text in pairs if str(text or "").strip()]


def summary_of(tldr: str) -> tuple[str, list[tuple[str, str]]]:
    """The Bench shows a summary of at most 300 characters and a hand-over's short
    version may run to 600. A longer one is shown cut, and whole under the card's
    details, so nothing the agent wrote is lost."""
    tldr = tldr.strip()
    if len(_line(tldr, 10_000)) <= SUMMARY_CHARS:
        return _line(tldr, SUMMARY_CHARS), []
    return _line(tldr, SUMMARY_CHARS), [("The short version, whole", tldr)]


def _store_files(v, agent_id: str, files: dict[str, Path]) -> list[dict[str, str]]:
    """Put each file's bytes in the Vault. The Bench shows them from there."""
    return [{"name": name.replace("/", "_"), "ref": v.core.vault.put(path.read_bytes())}
            for name, path in files.items()]


def _keep_copies(v, agent_id: str, card_id: str, files: dict[str, Path]) -> None:
    """The exact files handed over are kept in the agent's folder, outside its files, so
    what the owner decided on is always on record."""
    keep = v.agent_dir(agent_id) / "deliverables" / card_id
    for name, path in files.items():
        dst = keep.joinpath(*name.split("/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)


@rule("bench_waits")
def waited_on(kind: str, lane: str = "needs") -> bool:
    """Whether the agent waits on the owner for a card of this kind: work, a finding or
    a suggested priority in the owner's "needs" lane. A done card is for information."""
    return kind in NEEDS_OWNER and lane == "needs"


def _label(v, agent_id: str, card_id: str, title: str) -> None:
    """What the agent's view calls the thing it waits on: the card's title, not its id."""
    entry = v.world.agents[agent_id].waiting.get(card_id)
    if entry is not None:
        entry["title"] = title


@rule("waiting_rebuilt")
def rebuild_waiting(v, agent_id: str) -> list[str]:
    """Make what the body waits on agree with the ``cards`` table, which is the record.
    Run for every agent when the village starts, so a restart loses no wait and keeps
    none that was answered. A wait the saved body already has keeps its count. Returns
    the cards whose wait was put back."""
    a = v.world.agents.get(agent_id)
    if a is None:
        return []
    owed = {c["card_id"]: c for c in waiting(v, agent_id) if waited_on(c["kind"])}
    for item in [i for i in a.waiting if i not in owed]:
        a.waiting.pop(item)
    missing = [c for card_id, c in owed.items() if card_id not in a.waiting]
    if not missing:
        return []
    filed: dict[str, str] = {}
    for e in v.log.query(agent=agent_id, kinds=["bench_submitted", "bench_updated"]):
        filed[str(e.data.get("item"))] = e.id
    note = v.log.append("waiting_rebuilt", agent_id, v.clock.now(),
                        items=[c["card_id"] for c in missing])
    for c in missing:
        since = c["extra"].get("revised_ts") or c["filed_ts"]
        a.waiting[c["card_id"]] = {"evt": filed.get(c["card_id"], note.id),
                                   "since": float(since) / 1000, "caused": 0.0,
                                   "title": c["title"]}
    return [c["card_id"] for c in missing]


def file(v, agent_id: str, kind: str, title: str, *, summary: str = "", body: str = "",
         why: str = "", details: list[tuple[str, str]] | None = None,
         files: dict[str, Path] | None = None, urgent: bool = False, task: str | None = None,
         extra: dict[str, Any] | None = None, lane: str = "needs",
         actions: list[dict[str, Any]] | None = None, subject: str | None = None,
         replaces: str | None = None, ladder: dict[str, str] | None = None
         ) -> dict[str, Any]:
    """Put a card on the Bench and remember it. Returns the new row.

    ``files`` maps each path as the agent writes it to the real file. ``replaces`` is the
    id of an earlier card this one is a revision of: then it is not counted as a new
    submission."""
    files = files or {}
    shown = _line(("Urgent: " if urgent else "") + title, TITLE_CHARS)
    fields: dict[str, Any] = {
        "kind": kind, "source": SOURCE, "title": shown, "summary": _line(summary, SUMMARY_CHARS),
        "body": body.strip(), "why": why.strip()[:NOTE_CHARS] or None,
        "details": _details(details or []), "files": _store_files(v, agent_id, files),
        "lane": lane,
    }
    if actions:
        fields["actions"] = actions
    if subject:
        fields["subject"] = subject
    if ladder:
        fields["ladder"] = ladder  # the row the owner's answer is tallied on
    try:
        card_id = bench.file_card(v.core, filed_by=actor(agent_id), **fields)
    except bench.BenchError as e:
        raise Refused("bench_refused",
                      f"Nothing was put on the Bench: the Bench would not take this card. "
                      f"{e} Fix that and send it again.") from None
    _keep_copies(v, agent_id, card_id, files)
    now = v.clock.now()
    data = dict(extra or {})
    if kind in CORRECTABLE:
        data["first_body"] = fields["body"]
    if task:
        data["task"] = task
    if replaces:
        data["replaces"] = replaces
    conn, lock = db(v)
    with lock:
        conn.execute(
            "INSERT INTO cards (card_id, agent, kind, title, first_title, paths, status, note,"
            " urgent, filed_ts, extra) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)",
            (card_id, agent_id, kind, shown, _line(title, TITLE_CHARS), json.dumps(list(files)),
             OPEN, int(urgent), ms(now), json.dumps(data, ensure_ascii=False)))
    here = agent_id in v.world.agents and waited_on(kind, lane)
    if replaces:
        # A revision is not a new submission: no purpose, and the wait goes on.
        ev = v.log.append("bench_updated", agent_id, now, item=card_id, item_kind=kind,
                          title=shown, replaces=replaces)
        if here:
            v.world.wait_again(agent_id, card_id, ev, instead_of=replaces)
    elif here:
        # One event, one gain of purpose, and the wait begins: all the world's doing.
        v.world.submit(agent_id, card_id, task, item_kind=kind, title=shown, urgent=urgent)
    else:
        v.log.append("bench_submitted", agent_id, now, item=card_id, item_kind=kind,
                     title=shown, task=task, urgent=urgent)
    if here:
        _label(v, agent_id, card_id, shown)
    return get(v, card_id) or {}


def revise(v, card: dict[str, Any], *, title: str | None = None, summary: str | None = None,
           body: str | None = None, note: str | None = None,
           extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Change an open card in place. Not a new submission: it does not count against the
    day, and the owner sees one card with the current version. A card the owner had sent
    back returns to their "needs" lane."""
    agent_id, card_id = card["agent"], card["card_id"]
    shown = None if title is None else _line(title, TITLE_CHARS)
    try:
        bench.update_card(v.core, card_id, by=actor(agent_id), note=note, title=shown,
                          summary=None if summary is None else _line(summary, SUMMARY_CHARS),
                          body=body)
    except bench.BenchError as e:
        raise Refused("bench_refused",
                      f"Nothing was changed on the Bench: the Bench would not take the "
                      f"change to {card_id}. {e}") from None
    now = v.clock.now()
    data = {**card["extra"], **(extra or {}), "revised_ts": ms(now)}
    fields: dict[str, Any] = {"extra": data, "status": OPEN}
    if shown is not None:
        fields["title"] = shown
    _set(v, card_id, **fields)
    ev = v.log.append("bench_updated", agent_id, now, item=card_id, item_kind=card["kind"],
                      title=shown or card["title"])
    if agent_id in v.world.agents and waited_on(card["kind"]):
        # Sent back and now revised: the owner has it again. Still open: nothing changes.
        v.world.wait_again(agent_id, card_id, ev)
        _label(v, agent_id, card_id, shown or card["title"])
    return get(v, card_id) or {}


def replace(v, card: dict[str, Any], note: str) -> None:
    """Take a card off the Bench because a revision with new files takes its place. A
    card's files cannot change once filed, so the revision is a new card."""
    try:
        bench.withdraw_card(v.core, card["card_id"], by=actor(card["agent"]), note=note)
    except bench.BenchError:
        pass  # already gone from the Bench: the row is still closed below
    _set(v, card["card_id"], status=REPLACED, decided_ts=ms(v.clock.now()))


def duplicate_open_title(v, agent_id: str, kind: str, title: str) -> dict[str, Any] | None:
    key = _line(title, TITLE_CHARS).casefold()
    return next((c for c in cards(v, agent_id, kind) if c["status"] in LIVE
                 and key in (c["title"].casefold(), c["first_title"].casefold())), None)


# --- corrections -----------------------------------------------------------------------


def correct(v, agent_id: str, card_id: str, correction: str, title: str = "") -> dict[str, Any]:
    """The agent corrects its own open finding or hand-over. Without this a claim the
    agent has since seen to be false stays in front of the owner. The owner still decides
    the card; this only puts the correction at the top, where they read first.

    Returns the row, with ``replaced_earlier`` set when there was a correction before."""
    card = get(v, card_id)
    shown = card_id_of(card_id)[:40] or "that"
    if card is None:
        mine = [c["card_id"] for c in cards(v, agent_id) if c["status"] in LIVE
                and c["kind"] in (FINDING, WORK)]
        raise Refused("own_open_card",
                      f"Nothing was corrected: there is no card {shown}. You have "
                      f"{len(mine)} open findings and hand-overs"
                      + (": " + ", ".join(mine[-5:]) if mine else "")
                      + ". Use one of those ids, as shown in your bench view.")
    if card["agent"] != agent_id or card["kind"] not in (FINDING, WORK):
        raise Refused("own_open_card",
                      f"Nothing was corrected: {shown} is not 1 of your findings or "
                      "hand-overs. Use the id of one of your own, as shown in your bench "
                      "view.")
    if card["status"] not in LIVE:
        raise Refused("own_open_card",
                      f"Nothing was corrected: {owner(v)} already decided {shown}, so the "
                      "card is closed. Tell them what changed with message_owner (1 "
                      "message).")
    text = correction.strip()
    if not CORRECTION_MIN <= len(text) <= CORRECTION_MAX:
        raise Refused("correction_size",
                      f"Nothing was corrected: a correction is {CORRECTION_MIN} to "
                      f"{CORRECTION_MAX} characters and yours is {len(text)}. Say what was "
                      "wrong, what is true instead and how you checked, in a few sentences.")
    now = v.clock.now()
    when = f"{local(v, now):%a %d %b %H:%M}"
    extra = card["extra"]
    earlier = int(extra.get("corrections", 0))
    first_title = card["first_title"]
    # A title that still says the wrong thing under "Corrected: " reads as still false,
    # to the owner and to the agent itself. A new title replaces it.
    new = title.strip().removeprefix("Corrected: ").strip()
    if new:
        head = "Corrected: " + new
    else:
        head = card["title"] if card["title"].startswith("Corrected: ") else \
            "Corrected: " + card["title"]
    head = _line(head, TITLE_CHARS)
    first = extra.get("first_body", "")
    sent = "--- as first sent ---" if head == _line("Corrected: " + first_title, TITLE_CHARS) \
        else f"--- as first sent: {first_title} ---"
    body = f"CORRECTION ({when}): {text}\n\n{sent}\n{first}"
    out = revise(v, card, title=head, body=body, note="The agent corrected this card.",
                 extra={"corrections": earlier + 1})
    out["replaced_earlier"] = earlier > 0
    return out


# --- decisions -------------------------------------------------------------------------

_WORD = {"approve": "approved", "ok": "approved", "not_yet": "not_yet", "no": "denied",
         REOPEN: "reopened"}
_STATUS = {"approved": APPROVED, "not_yet": RETURNED, "denied": DECLINED, "reopened": REOPENED}


def trusted(card: dict[str, Any] | None, payload: dict[str, Any], event_actor: str) -> bool:
    """Rule ``decision_trusted``: a decision counts only for a card this village filed,
    by the agent the Bench says filed it, decided by the owner."""
    return bool(card) and event_actor == "owner" \
        and payload.get("filed_by") == actor(card["agent"]) \
        and payload.get("card_kind") == card["kind"]


def apply_decision(v, event: Any) -> dict[str, Any] | None:
    """Turn one ``bench.decided`` Tape event into what it means for the village. Safe to
    call again with the same event. None when the event is not for a village card, is not
    trusted, or was already applied.

    Every decision is logged (``bench_decided``), wakes the agent and shows in its bench
    view. It is the owner's answer, so the body stops waiting on the card
    (``World.respond``), and an approval adds purpose there, once."""
    if getattr(event, "kind", None) != "bench.decided":
        return None
    p = event.payload
    card = get(v, p.get("card_id", ""))
    if not trusted(card, p, event.actor):
        return None
    extra = card["extra"]
    if event.id in extra.get("decisions", []):
        return None
    word = _WORD.get(p.get("action", ""))
    if card["kind"] == HAND:
        # A request's answer is its outcome (``apply_outcome``), unless the request
        # itself is gone and no outcome can ever come.
        return _request_gone(v, card, event) if card["status"] in LIVE else None
    if word is None or card["status"] not in LIVE:
        return None
    agent_id, card_id, kind = card["agent"], card["card_id"], card["kind"]
    note = (p.get("reason") or p.get("note") or "").strip()[:NOTE_CHARS]
    now = v.clock.now()
    extra = {**extra, "decisions": [*extra.get("decisions", []), event.id][-20:]}
    _set(v, card_id, status=_STATUS[word], note=note, decided_ts=ms(now), extra=extra)
    ev = v.log.append("bench_decided", agent_id, now, item=card_id, item_kind=kind,
                      decision=word, note=note, title=card["title"], task=extra.get("task"),
                      by="owner", tape=event.id)
    result: dict[str, Any] = {"card_id": card_id, "agent": agent_id, "kind": kind,
                              "decision": word, "note": note, "event": ev.id}
    if agent_id in v.world.agents:
        v.world.respond(agent_id, card_id, word, acknowledged=word == "approved")
    if kind == WORK and word == "approved":
        # Final means the owner said so.
        v.log.append("work_final", agent_id, now, item=card_id, files=card["paths"],
                     title=card["title"], task=extra.get("task"), evidence=[ev.id])
    elif kind == PRIORITY and word == "approved":
        from jimmyhq.village.comms import tasklist

        result["priority_id"] = tasklist.add_priority(
            v, card["first_title"], extra.get("why", ""), extra.get("details", ""),
            suggested_by=agent_id, cause=event.id)
    elif kind == DONE and word == "reopened":
        from jimmyhq.village.comms import tasklist

        tasklist.reopen(v, agent_id, extra.get("task_id", ""), extra.get("task"), note,
                        evidence=[ev.id], cause=event.id)
    v.wake(agent_id)
    return result


# --- what became of a request to use an integration --------------------------------------

_SAFE_REF = re.compile(r"[A-Za-z0-9._:@/+=-]{1,120}")
# outcome -> (the word the body hears, the card's status, whether it adds purpose)
_OUTCOMES = {"done": ("approved", APPROVED, True), "failed": ("approved", APPROVED, False),
             "unknown": ("approved", APPROVED, False), "refused": ("approved", APPROVED, False),
             "declined": ("denied", DECLINED, False)}


@rule("hand_outcome_words")
def outcome_note(v, outcome: str, payload: dict[str, Any]) -> str:
    """What the agent reads about its request, written here. Nothing a provider said
    is in it, except its reference when that is a plain id."""
    if outcome == "done":
        ref = str(payload.get("ref") or "")
        return ("Done, exactly as shown on the card."
                + (f" The provider's reference: {ref}." if _SAFE_REF.fullmatch(ref) else ""))
    if outcome == "failed":
        return ("Approved, but it did not work at the provider. Nothing more is tried. If "
                "it still matters, ask again.")
    if outcome == "unknown":
        return ("Approved, but the outcome is unknown: the provider never answered, so it "
                f"may or may not have happened. Check before retrying: {owner(v)} has been "
                "told and will look. Do not ask for it again until then.")
    if outcome == "refused":
        return "Approved, but not done. " + str(payload.get("reason") or "").strip()
    return (str(payload.get("note") or "").strip()
            or "No reason was given. Nothing was done.")[:NOTE_CHARS]


def _provider_said(v, tool: str, run_id: str | None) -> str:
    """Why a provider refused an approved action, for the agent to read once. It is the
    provider's own text, so it is framed as data and kept out of the log."""
    from jimmyhq import hands

    run = hands.hands(v.core).store.run(run_id) if run_id else None
    said = (run or {}).get("error") or "It did not say why."
    return f"{tool} did not work. What the provider said:\n\n{hands.shape.framed(said)}"


def _show_once(v, agent_id: str, tool: str, text: str) -> None:
    state = getattr(v, "state", None)
    if state is not None:
        state(agent_id).last_result = (tool, text)


GONE = ("Not done: the account this was for is no longer connected, so the request is "
        "gone. Nothing was sent.")


def _request_gone(v, card: dict[str, Any], event: Any) -> dict[str, Any] | None:
    """The owner decided a request whose account has since been forgotten: its frozen
    arguments were deleted with the account, so nothing can be performed and the
    executor has nothing to report. The agent is told here instead of waiting for ever."""
    from jimmyhq import hands

    if hands.pending(v.core, card["extra"].get("fingerprint", "")) is not None:
        return None
    agent_id, now = card["agent"], v.clock.now()
    extra = {**card["extra"], "decisions": [*card["extra"].get("decisions", []), event.id],
             "outcome": "gone"}
    _set(v, card["card_id"], status=DECLINED, note=GONE, decided_ts=ms(now), extra=extra)
    ev = v.log.append("bench_decided", agent_id, now, item=card["card_id"], item_kind=HAND,
                      decision="denied", outcome="gone", note=GONE, title=card["title"],
                      by="owner", tape=event.id)
    if agent_id in v.world.agents:
        v.world.respond(agent_id, card["card_id"], "denied", acknowledged=False)
    v.wake(agent_id)
    return {"card_id": card["card_id"], "agent": agent_id, "kind": HAND,
            "decision": "denied", "outcome": "gone", "note": GONE, "event": ev.id}


def apply_outcome(v, event: Any) -> dict[str, Any] | None:
    """Turn one ``hand.outcome`` Tape event into the agent's answer. Safe to call again
    with the same event. None when it is not about a request this village filed.

    The agent sees it in its next beat as a decision on its card: done with the
    provider's reference, failed, "outcome unknown: check before retrying", not done
    because something changed, or the owner's no. The body stops waiting."""
    if getattr(event, "kind", None) != "hand.outcome":
        return None
    p = event.payload
    outcome, agent_id, tool = p.get("outcome"), p.get("agent"), str(p.get("tool") or "")
    if agent_id not in v.world.agents:
        return None
    now = v.clock.now()
    card = get(v, p.get("card_id") or "")
    if card is None:
        # No card of ours: something that ran without asking was undone, or was cut short.
        words = {"undone": f"{owner(v)} undid what you did with {tool}: it was taken back.",
                 "unknown": f"The outcome of {tool} is unknown: it may or may not have "
                            f"happened. Do not repeat it; {owner(v)} has been told.",
                 }.get(outcome)
        if words is None or p.get("card_id"):
            return None
        v.log.append("hand_" + outcome, agent_id, now, tool=tool, tape=event.id)
        _show_once(v, agent_id, tool, words)
        v.wake(agent_id)
        return {"agent": agent_id, "outcome": outcome}
    if (outcome not in _OUTCOMES or card["kind"] != HAND or card["agent"] != agent_id
            or card["extra"].get("fingerprint") != p.get("fingerprint")
            or card["status"] not in LIVE or event.id in card["extra"].get("decisions", [])):
        return None
    word, status, acknowledged = _OUTCOMES[outcome]
    note = outcome_note(v, outcome, p)
    extra = {**card["extra"], "decisions": [*card["extra"].get("decisions", []), event.id],
             "outcome": outcome}
    _set(v, card["card_id"], status=status, note=note, decided_ts=ms(now), extra=extra)
    ev = v.log.append("bench_decided", agent_id, now, item=card["card_id"], item_kind=HAND,
                      decision=word, outcome=outcome, note=note, title=card["title"],
                      by="owner", tape=event.id)
    v.world.respond(agent_id, card["card_id"], word, acknowledged=acknowledged)
    if outcome == "failed":
        _show_once(v, agent_id, tool, _provider_said(v, tool, p.get("run_id")))
    v.wake(agent_id)
    return {"card_id": card["card_id"], "agent": agent_id, "kind": HAND, "decision": word,
            "outcome": outcome, "note": note, "event": ev.id}
