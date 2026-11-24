"""The researcher: rounds of looking given to whoever holds the role, budgets and stalls.

With the role empty, the ``search`` job finds articles for every topic. With it filled,
the researcher is given a round every ``research_hours`` for the topics it covers (a
topic's "Let the researcher look" switch), and the job stands down for those topics
(``plain_covers``). The researcher's judgment arrives only through the tools in
``tools``; the code reads every source itself and runs have-I-seen-this whoever
suggested it.

A round is answered when the researcher suggests something or ends its task; ending it
with nothing is a fine answer ("an empty day is better than a weak article"). A round not
answered in ``stall_hours`` is handled as ``on_stall`` says: remind once and then tell
the owner (a card: Wait, Look without the researcher, Drop), tell at once, or look the
plain way for the researcher's topics until the next round. ``agent_steps_per_round``
caps the researcher's tool calls in a round.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError, Decision, Work

from reader import cards, topics
from reader.context import Ctx

log = logging.getLogger("reader")

HOUR_MS = 3_600_000
TOOL = "app_reader_"  # how agents see Reader's tools named

WAIT = {"id": "wait", "label": "Wait", "style": "primary", "closes": True}
PLAIN = {"id": "finish_plain", "label": "Look without the researcher", "closes": True}
DROP = {"id": "drop", "label": "Drop this round", "style": "danger", "closes": True}
STALL_ACTIONS = [WAIT, PLAIN, DROP]

SPENT = ("This round's budget of {n} agent steps is spent, so nothing was read or "
         "searched. Suggest what you have read with {tool}suggest, then finish your task.")


class WorkError(ValueError):
    """The owner's choice could not be carried out. The message says why."""


def holder(ctx: Ctx) -> str | None:
    """The researcher's label ("" when it has none), or None when nobody holds the role
    (or the app was not granted agents.tools)."""
    try:
        found = ctx.app.roles.get()
    except CallError:
        return None
    held = found.get("researcher")
    return (held.agent_label or "") if held is not None and held.filled else None


def latest(ctx: Ctx) -> dict[str, Any] | None:
    return ctx.store.one("SELECT * FROM round ORDER BY id DESC LIMIT 1")


def open_round(ctx: Ctx) -> dict[str, Any] | None:
    return ctx.store.one("SELECT * FROM round WHERE state='open' ORDER BY id DESC LIMIT 1")


def plain_covers(ctx: Ctx, topic: dict[str, Any], held: bool | None = None) -> bool:
    """Rule ``plain_stands_down``: Reader's own search covers a topic unless the topic
    lets the researcher look, someone holds the role, and the latest round was not
    handed back to the plain way."""
    if not topic["researcher"]:
        return True
    if not (held if held is not None else holder(ctx) is not None):
        return True
    last = latest(ctx)
    return bool(last and last["plain"])


def covered(ctx: Ctx) -> list[dict[str, Any]]:
    return [t for t in topics.active(ctx) if t["researcher"]]


# --- budget ---------------------------------------------------------------------------------


def steps_used(ctx: Ctx) -> int:
    row = open_round(ctx)
    if row is not None:
        return ctx.store.one("SELECT COUNT(*) AS n FROM agent_step WHERE round_id=?",
                             (row["id"],))["n"]
    today = ctx.today()
    return sum(1 for r in ctx.store.all("SELECT created FROM agent_step WHERE round_id IS "
                                        "NULL ORDER BY id DESC LIMIT 2000")
               if ctx.day(r["created"]) == today)


def spend(ctx: Ctx, agent: str, tool: str, *, submit: bool = False) -> str | None:
    """Rule ``step_budget``: count one step of the round (or of today, outside a round).
    Past ``agent_steps_per_round`` a reading tool is refused with the sentence returned; a
    suggestion is still taken, uncounted, so the count never passes the budget."""
    most = int(ctx.cfg().agent_steps_per_round)
    if steps_used(ctx) >= most:
        return None if submit else SPENT.format(n=most, tool=TOOL)
    row = open_round(ctx)
    ctx.store.insert("agent_step", {"round_id": row["id"] if row else None,
                                    "agent": agent[:120], "tool": tool, "created": ctx.now()})
    return None


def answered(ctx: Ctx) -> None:
    """The researcher handed something back this round (a record tool calls this)."""
    row = open_round(ctx)
    if row is not None and row["answered"] is None:
        ctx.store.update("round", row["id"], {"answered": ctx.now()})


# --- giving rounds --------------------------------------------------------------------------


def _ask(found: list[dict[str, Any]]) -> tuple[str, str]:
    listed = ", ".join(f"{t['id']} ({t['name']})" for t in found)
    return ("Find articles for the owner's topics",
            f"Look for articles worth the time of the people each topic is for. Topics: "
            f"{listed}. See them with {TOOL}topics, search with {TOOL}search, read with "
            f"{TOOL}read and check with {TOOL}seen; suggest the ones you stand behind with "
            f"{TOOL}suggest. Then finish this task with a short note. Suggesting nothing "
            "is a fine answer when nothing is worth it.")


def give(ctx: Ctx, *, label: str, reminded: int = 0, title: str = "", details: str = ""
         ) -> int | None:
    """Give one round and keep its row. None when nobody holds the role now."""
    if not title:
        title, details = _ask(covered(ctx))
    try:
        work_id = ctx.app.work.give("researcher", title[:200], details=details[:4000],
                                    subject="round",
                                    due_ms=ctx.now() + int(ctx.cfg().stall_hours) * HOUR_MS)
    except CallError as error:
        if error.code == "not_found":
            return None
        raise
    return ctx.store.insert("round", {"work_id": work_id, "state": "open",
                                      "reminded": reminded, "agent_label": label or None,
                                      "given": ctx.now()})


def due(ctx: Ctx) -> bool:
    """Rule ``research_rhythm``: a new round once ``research_hours`` have passed since the
    last one was given, and none is open."""
    if open_round(ctx) is not None:
        return False
    last = latest(ctx)
    return last is None or ctx.now() - last["given"] >= int(ctx.cfg().research_hours) * HOUR_MS


def stalled(ctx: Ctx, row: dict[str, Any]) -> bool:
    """Rule ``stall_hours``: an open round not answered within ``stall_hours``."""
    return (row["state"] == "open" and row["answered"] is None and not row["card_id"]
            and ctx.now() - row["given"] >= int(ctx.cfg().stall_hours) * HOUR_MS)


def watch(ctx: Ctx) -> list[str]:
    """Part of the ``advance`` job: tidy cards, act on a late round, give a round when
    one is due."""
    said: list[str] = []
    for row in ctx.store.all("SELECT * FROM round WHERE answered IS NOT NULL AND card_id IS "
                             "NOT NULL AND card_id != ''"):
        cards.withdraw(ctx, row["card_id"], "The researcher answered.")
        ctx.store.update("round", row["id"], {"card_id": ""})
    label = holder(ctx)
    if label is None:
        return said
    row = open_round(ctx)
    if row is not None and stalled(ctx, row):
        said.append(late(ctx, row, f"The researcher has not answered its round for "
                         f"{int(ctx.cfg().stall_hours)} hours."))
    rhythm = int(ctx.cfg().research_hours) * HOUR_MS
    if row is not None and row["answered"] is not None and ctx.now() - row["given"] >= rhythm:
        withdraw(ctx, row["work_id"])  # answered, and the next round is due
        ctx.store.update("round", row["id"], {"state": "answered", "ended": ctx.now()})
    if covered(ctx) and due(ctx) and give(ctx, label=label) is not None:
        said.append("Gave the researcher a round.")
    return said


def late(ctx: Ctx, row: dict[str, Any], why: str) -> str:
    policy = str(ctx.cfg().on_stall)
    if policy == "finish_plain":
        return look_plain(ctx, row, why)
    if policy == "remind_then_tell" and not row["reminded"]:
        withdraw(ctx, row["work_id"])
        ctx.store.update("round", row["id"], {"state": "withdrawn", "ended": ctx.now()})
        title, details = _ask(covered(ctx))
        if give(ctx, label=row["agent_label"] or "", reminded=1, title=f"Reminder: {title}",
                details=f"This is waiting for you. {details}") is None:
            return tell(ctx, row, "Nobody holds the researcher role any more.")
        return "Reminded the researcher."
    return tell(ctx, row, why)


def tell(ctx: Ctx, row: dict[str, Any], why: str) -> str:
    body = (f"{why}\n\nWait gives it the same time again. Look without the researcher lets "
            "Reader's own search cover its topics until the next round. Drop ends this "
            "round; the researcher is asked again at the next one.")
    card_id = cards.notice(ctx, "Reader: the researcher is late", body,
                           subject=f"round:{row['id']}", actions=STALL_ACTIONS)
    ctx.store.update("round", row["id"], {"card_id": card_id or "", "why": why[:500]})
    ctx.emit()
    return why


def look_plain(ctx: Ctx, row: dict[str, Any], why: str) -> str:
    if row["state"] == "open":
        withdraw(ctx, row["work_id"])
    ctx.store.update("round", row["id"], {"state": "withdrawn", "plain": 1,
                                          "ended": ctx.now(), "card_id": None})
    ctx.emit()
    return f"{why} Reader looks for its topics itself until the next round."


def withdraw(ctx: Ctx, work_id: str | None) -> None:
    if not work_id:
        return
    try:
        ctx.app.work.withdraw(work_id)
    except CallError as error:
        log.info("could not withdraw work %s: %s", work_id, error.message)


def on_work(ctx: Ctx, given: Work) -> str:
    """The platform says a round ended or changed hands."""
    row = ctx.store.one("SELECT * FROM round WHERE work_id=?", (given.id,))
    if row is None:
        return "Reader gave no such work; nothing was done."
    if given.state == "reassigned":
        ctx.store.update("round", row["id"], {"agent_label": given.agent_label})
        return "noted"
    if row["state"] != "open":
        return "noted"
    if given.state == "done":
        ctx.store.update("round", row["id"], {"state": "answered", "ended": ctx.now(),
                                              "answered": row["answered"] or ctx.now()})
        return "noted"
    ctx.store.update("round", row["id"], {"state": given.state, "ended": ctx.now()})
    did = "set the round aside" if given.state == "set_aside" else "dropped the round"
    why = f"The researcher {did}" + (f": {given.note}" if given.note else ".")
    fresh = ctx.store.one("SELECT * FROM round WHERE id=?", (row["id"],)) or row
    if str(ctx.cfg().on_stall) == "finish_plain":
        return look_plain(ctx, fresh, why)
    return tell(ctx, fresh, why)


def on_card(ctx: Ctx, decision: Decision) -> str:
    subject = decision.subject or ""
    row_id = int(subject[6:]) if subject.startswith("round:") and subject[6:].isdigit() else 0
    row = ctx.store.one("SELECT * FROM round WHERE id=?", (row_id,))
    if row is None or row["card_id"] != decision.card_id:
        return "That card is no longer current; nothing was done."
    return choose(ctx, row, decision.action)


def choose(ctx: Ctx, row: dict[str, Any], action: str) -> str:
    """Wait, Look without the researcher, or Drop this round."""
    if action == "wait":
        ctx.store.update("round", row["id"], {"card_id": None, "given": ctx.now(),
                                              "state": "open" if row["state"] == "open"
                                              else row["state"]})
        return "waiting"
    if action == "finish_plain":
        return look_plain(ctx, row, "You chose to look without the researcher.")
    if action == "drop":
        if row["state"] == "open":
            withdraw(ctx, row["work_id"])
        ctx.store.update("round", row["id"], {"state": "withdrawn", "ended": ctx.now(),
                                              "card_id": None})
        return "dropped"
    raise WorkError(f"Reader does not know {action!r}. Nothing was done.")


def from_page(ctx: Ctx, row_id: int, action: str) -> str:
    row = ctx.store.one("SELECT * FROM round WHERE id=?", (row_id,))
    if row is None or not row["card_id"]:
        raise WorkError("That round is not waiting for you any more. Nothing was done.")
    cards.withdraw(ctx, row["card_id"])
    return choose(ctx, row, action)


def status(ctx: Ctx) -> dict[str, Any]:
    """What the screen says about the researcher."""
    label = holder(ctx)
    row = latest(ctx)
    waiting = ctx.store.one("SELECT * FROM round WHERE card_id IS NOT NULL AND card_id != '' "
                            "ORDER BY id DESC LIMIT 1")
    return {"held": label is not None, "label": label or "",
            "round": {k: row[k] for k in ("id", "state", "given", "answered", "plain")}
            if row else None,
            "late": {"id": waiting["id"], "why": waiting["why"]} if waiting else None}
