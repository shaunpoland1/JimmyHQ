"""The flow with agents: who takes each step, the work given and handed back, budgets and
stalls. The code decides every move; an agent's judgment arrives only through the record
tools in ``tools`` and is checked there.

    coverage_check (the code, before anything is spent)
      -> searching: research given to the researcher (``researching``), or the plain way
      -> outlining and drafting: the writer, or the plain way; the code checks every draft
      -> reviewing: the checker, or the plain review; then the code decides
      -> repairing and rechecking: the writer and the checker again, until ``repair_passes``
      -> ready: the owner's card.  And stalled, dropped.

A step whose role is empty is done the plain way (``plain``) for that step alone, and the
article keeps a line of who did each step (``credits``). Record tools may not reach
outside the app, so they only write: what goes out (giving work, a card, withdrawing work)
happens here, from the ``advance`` and ``watch`` jobs.

Budgets and stalls, all settings: ``agent_steps_per_piece`` counts the agents' tool
calls on one article, ``agent_searches_per_piece`` and ``agent_reads_per_piece`` the
researcher's searches and pages; ``stall_hours_*`` say how long each step may wait;
``on_stall`` says what then (remind once and then a card to the owner, a card at once, or
finish the plain way). Publishing never waits on an agent: no agent has a tool for it.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError, Decision, Work

from articles import cards, credits, research, states, writing
from articles.context import Ctx

log = logging.getLogger("articles")

HOUR_MS = 3_600_000
PLAIN = object()  # route's answer: do this step the plain way
TOOL = "app_articles_"  # how the agents see Articles' tools named

# Each state an agent may take, the step of work it belongs to, and the role for it.
STEP_OF = {"searching": "research", "researching": "research", "outlining": "writing",
           "drafting": "writing", "repairing": "repairing", "revising": "revising",
           "reviewing": "reviewing", "rechecking": "rechecking"}
ROLE_OF = {"research": "researcher", "writing": "writer", "repairing": "writer",
           "revising": "writer", "reviewing": "checker", "rechecking": "checker"}
STATES_OF = {step: {s for s, st in STEP_OF.items() if st == step} for step in ROLE_OF}
WORDS = {"research": "research", "writing": "writing", "repairing": "repairing",
         "revising": "revising", "reviewing": "checking", "rechecking": "checking the repair"}

WAIT = {"id": "wait", "label": "Wait", "style": "primary", "closes": True}
FINISH_PLAIN = {"id": "finish_plain", "label": "Finish without agents", "closes": True}
DROP = {"id": "drop", "label": "Drop", "style": "danger", "closes": True}


class WorkError(ValueError):
    """The owner's choice could not be carried out. The message says why."""


# --- who holds the roles --------------------------------------------------------------------


def holders(ctx: Ctx) -> dict[str, str] | None:
    """role -> the agent's label ("" when the platform gives none), for each filled role.
    None when this app may not work with agents (no ``agents.tools`` grant, or no answer):
    every role then counts as empty.

    A label is for the owner's line only, never for telling agents apart: who an agent is
    comes with each tool call (``tools.not_own_article``)."""
    try:
        found = ctx.app.roles.get()
    except CallError:
        return None
    return {role: (held.agent_label or "") for role, held in found.items() if held.filled}


def plain_only(ctx: Ctx, piece_id: int) -> bool:
    """The owner chose to finish this article without agents (or ``on_stall`` did)."""
    return bool(ctx.store.get_meta(f"plain.{piece_id}"))


# --- budgets --------------------------------------------------------------------------------


def used(ctx: Ctx, piece_id: int, tool: str | None = None) -> int:
    if tool is None:
        return ctx.store.one("SELECT COUNT(*) AS n FROM agent_step WHERE piece_id=?",
                             (piece_id,))["n"]
    return ctx.store.one("SELECT COUNT(*) AS n FROM agent_step WHERE piece_id=? AND tool=?",
                         (piece_id, tool))["n"]


def spent(ctx: Ctx, piece_id: int) -> bool:
    """Rule ``step_budget``: the agents' tool calls on this article reached
    ``agent_steps_per_piece``."""
    return used(ctx, piece_id) >= int(ctx.cfg().agent_steps_per_piece)


SPENT = ("Article {piece}'s budget of {n} agent steps is spent, so nothing was done and "
         "nothing more will be spent on it. Hand back what you have now with {tool}, or "
         "finish your task with a note and leave it.")
HAND_BACK = {"researcher": "submit_summary", "writer": "submit_article",
             "checker": "submit_review"}


def spend(ctx: Ctx, piece_id: int, agent: str, role: str | None, tool: str, *,
          submit: bool = False, hand_back: str = "") -> str | None:
    """Count one agent step on the article. Past the budget a reading tool is refused
    with the sentence returned; a submission is still taken (it is "what you have") but
    no longer counted, so the count never passes the budget."""
    if spent(ctx, piece_id):
        if submit:
            return None
        back = hand_back or HAND_BACK.get(role or "", "the tool that hands back your work")
        return SPENT.format(piece=piece_id, n=int(ctx.cfg().agent_steps_per_piece),
                            tool=TOOL + back)
    ctx.store.insert("agent_step", {"piece_id": piece_id, "agent": agent[:120], "role": role,
                                    "tool": tool, "created": ctx.now()})
    return None


def left_of(ctx: Ctx, piece_id: int, tool: str) -> int:
    """How many searches (``tool="search"``) or page reads (``"read"``) are left."""
    cfg = ctx.cfg()
    most = int(cfg.agent_searches_per_piece if tool == "search" else cfg.agent_reads_per_piece)
    return max(0, most - used(ctx, piece_id, tool))


# --- the work rows --------------------------------------------------------------------------


def waiting(ctx: Ctx, piece_id: int, step: str) -> dict[str, Any] | None:
    """The work given for this step that has not been handed back."""
    return ctx.store.one("SELECT * FROM agent_work WHERE piece_id=? AND step=? AND "
                         "state='open' AND answered IS NULL ORDER BY id DESC LIMIT 1",
                         (piece_id, step))


def answer(ctx: Ctx, piece_id: int, step: str) -> None:
    """An agent handed back this step (a record tool calls this: it only writes)."""
    row = waiting(ctx, piece_id, step)
    if row is not None:
        ctx.store.update("agent_work", row["id"], {"answered": ctx.now()})


def _withdraw(ctx: Ctx, work_id: str | None) -> None:
    if not work_id:
        return
    try:
        ctx.app.work.withdraw(work_id)
    except CallError as error:
        log.info("could not withdraw work %s: %s", work_id, error.message)


def hours(ctx: Ctx, step: str) -> int:
    """How long a step may wait for its agent: ``stall_hours_research``, ``_write`` or
    ``_check``."""
    cfg = ctx.cfg()
    if step == "research":
        return int(cfg.stall_hours_research)
    if step in ("reviewing", "rechecking"):
        return int(cfg.stall_hours_check)
    return int(cfg.stall_hours_write)


def give(ctx: Ctx, piece: dict[str, Any], step: str, label: str, *,
         reminded: int = 0) -> int | None:
    """Give one piece of work and keep its row. None when nobody holds the role now."""
    title, details = ask_for(ctx, piece, step)
    if reminded:
        title, details = f"Reminder: {title}", f"This is waiting for you. {details}"
    try:
        work_id = ctx.app.work.give(ROLE_OF[step], title[:200], details=details[:4000],
                                    subject=f"piece:{piece['id']}",
                                    due_ms=ctx.now() + hours(ctx, step) * HOUR_MS)
    except CallError as error:
        if error.code == "not_found":
            return None
        raise
    return ctx.store.insert("agent_work", {
        "piece_id": piece["id"], "step": step, "role": ROLE_OF[step], "work_id": work_id,
        "state": "open", "reminded": reminded, "agent_label": label or None,
        "given": ctx.now()})


def ask_for(ctx: Ctx, piece: dict[str, Any], step: str) -> tuple[str, str]:
    """The task's title and details, which point the agent at its tools. The topic is
    not repeated here: the tools give it, as material."""
    site = ctx.store.site(piece["site_id"]) or {}
    name, n = site.get("name", "a site"), piece["id"]
    if step == "research":
        return (f"Research article {n} for {name}",
                f"Start with {TOOL}assignment (piece={n}). Search with {TOOL}search, have "
                f"Articles read pages with {TOOL}read, see what you have with {TOOL}sources, "
                f"leave out weak ones with {TOOL}drop_source, then hand back the research "
                f"summary with {TOOL}submit_summary (piece={n}).")
    if step == "writing":
        return (f"Write article {n} for {name}",
                f"Read the material with {TOOL}brief (piece={n}) and your earlier articles "
                f"with {TOOL}my_past (site={piece['site_id']}). Hand back the outline with "
                f"{TOOL}submit_outline, then the article with {TOOL}submit_article "
                f"(piece={n}).")
    if step == "repairing":
        return (f"Repair article {n} for {name}",
                f"The checks and the review left questions open. Read them with {TOOL}brief "
                f"(piece={n}), then hand back the repaired article with "
                f"{TOOL}submit_article (piece={n}).")
    if step == "revising":
        return (f"Revise article {n} for {name}",
                f"The owner sent it back with a note. Read it with {TOOL}brief (piece={n}), "
                f"then hand back the new version with {TOOL}submit_article (piece={n}).")
    what = "the repair of article" if step == "rechecking" else "article"
    return (f"Check {what} {n} for {name}",
            f"Read it with {TOOL}review_packet (piece={n}), then hand back your review with "
            f"{TOOL}submit_review (piece={n}). You did not write it; check it against the "
            "numbered sources.")


# --- routing a step -------------------------------------------------------------------------


def route(ctx: Ctx, piece: dict[str, Any]) -> Any:
    """Who takes this article's step now. ``PLAIN``: do it the plain way. None: it is
    with an agent, or waits. A string: what was done (work given, a review decided)."""
    state = piece["state"]
    step = STEP_OF.get(state)
    if step is None:
        return PLAIN  # the coverage check, and the plain research's own steps
    if state == "researching":
        return research_route(ctx, piece)
    if plain_only(ctx, piece["id"]):
        return PLAIN
    if state in ("reviewing", "rechecking"):
        review = pending_review(ctx, piece)
        if review is not None:
            return decide_review(ctx, piece, review)
    if waiting(ctx, piece["id"], step) is not None:
        return None
    role = ROLE_OF[step]
    found = holders(ctx)
    if found is None or role not in found:
        return PLAIN
    if spent(ctx, piece["id"]):
        return budget_spent(ctx, piece, step)
    if state == "searching" and not research.may_start(ctx, piece):
        return None
    if give(ctx, piece, step, found[role]) is None:
        return PLAIN
    if state == "searching":
        ctx.store.move(piece["id"], "researching")
    who = f"the {role}" + (f" ({found[role]})" if found[role] else "")
    return _turn(ctx, piece["id"], state, f"Given to {who}: {ask_for(ctx, piece, step)[0]}.")


def research_route(ctx: Ctx, piece: dict[str, Any]) -> str | None:
    """An article being researched: it waits while its researcher has the work
    (``watch`` looks after late work). With no work open (Retry after a stall, say) it is
    given again, or done the plain way when nobody holds the role or the owner chose."""
    if not plain_only(ctx, piece["id"]) and waiting(ctx, piece["id"], "research"):
        return None
    found = holders(ctx) or {}
    if not plain_only(ctx, piece["id"]) and "researcher" in found:
        if spent(ctx, piece["id"]):
            return budget_spent(ctx, piece, "research")
        if give(ctx, piece, "research", found["researcher"]) is not None:
            return _turn(ctx, piece["id"], "researching", "Given to the researcher again.")
    ctx.store.move(piece["id"], "searching")
    return _turn(ctx, piece["id"], "researching", "The research is done the plain way from "
                 "here.")


def after_plain(ctx: Ctx, piece_id: int, was: str) -> None:
    """A plain step finished: the article remembers the plain way did it, and why."""
    step = credits.PLAIN_STEP.get(was)
    piece = ctx.store.piece(piece_id) or {}
    if not step or piece.get("state") in (was, "stalled", None):
        return
    if plain_only(ctx, piece_id):
        why = "you chose to finish without agents"
    else:
        why = f"no agent holds the {ROLE_OF[STEP_OF.get(was, 'research')]} role"
    credits.add(ctx.store, piece_id, step, "plain", note=why)


def _turn(ctx: Ctx, piece_id: int, step: str, gloss: str, **more: Any) -> str:
    ctx.store.add_turn(piece_id, step, gloss, **more)
    return gloss


def budget_spent(ctx: Ctx, piece: dict[str, Any], step: str) -> str:
    """Nothing more is given to an agent: the owner chooses, on a card."""
    n = int(ctx.cfg().agent_steps_per_piece)
    why = (f"Its budget of {n} agent steps is spent before the {WORDS[step]}, so no more work "
           "was given to an agent.")
    row_id = ctx.store.insert("agent_work", {
        "piece_id": piece["id"], "step": step, "role": ROLE_OF[step], "state": "budget",
        "why": why, "given": ctx.now()})
    return tell(ctx, ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row_id,)) or {},
                why)


# --- reviews --------------------------------------------------------------------------------


def reviewed_revision(ctx: Ctx, piece: dict[str, Any]) -> dict[str, Any] | None:
    """The version a review is about: the current one, or the repair being compared."""
    step = piece.get("step") or piece["state"]
    if step == "rechecking":
        return writing.candidate_of(ctx, piece["id"])
    return ctx.store.revision(piece["id"])


def pending_review(ctx: Ctx, piece: dict[str, Any]) -> dict[str, Any] | None:
    revision = reviewed_revision(ctx, piece)
    if revision is None:
        return None
    return ctx.store.one("SELECT * FROM agent_review WHERE revision_id=? AND used IS NULL "
                         "ORDER BY id DESC LIMIT 1", (revision["id"],))


def decide_review(ctx: Ctx, piece: dict[str, Any], review: dict[str, Any]) -> str:
    """The checker's review is in: the code, never the checker, decides what follows, by
    the same rules as the plain review (``writing.after_review``, ``after_recheck``)."""
    ctx.store.update("agent_review", review["id"], {"used": ctx.now()})
    role = {"role": "checker"}
    revision = ctx.store.one("SELECT * FROM revision WHERE id=?", (review["revision_id"],))
    if revision is None:
        return "The reviewed version is gone; nothing was decided."
    if piece["state"] == "rechecking":
        current = ctx.store.revision(piece["id"]) or revision
        said = writing.after_recheck(ctx, piece, current, revision, role)
    else:
        said = writing.after_review(ctx, piece, revision, list(revision["review"] or []),
                                    int(revision["review_aside"]), role,
                                    by=f"The checker said {review['verdict']}")
    settle_if_done(ctx, piece["id"])
    return said


def settle_if_done(ctx: Ctx, piece_id: int) -> None:
    """Work still open for a step the article has left is withdrawn and closed."""
    piece = ctx.store.piece(piece_id) or {}
    here = piece.get("came_from") if piece.get("state") == "stalled" else piece.get("state")
    for row in ctx.store.all("SELECT * FROM agent_work WHERE piece_id=? AND state='open'",
                             (piece_id,)):
        if here in STATES_OF.get(row["step"], set()) and row["answered"] is None:
            continue
        if row["answered"] is None:
            _withdraw(ctx, row["work_id"])
        ctx.store.update("agent_work", row["id"], {"state": "closed", "ended": ctx.now()})


# --- when work comes back -------------------------------------------------------------------


def on_work(ctx: Ctx, work: Work) -> str:
    """The platform says a piece of work ended or changed hands."""
    row = ctx.store.one("SELECT * FROM agent_work WHERE work_id=?", (work.id,))
    if row is None:
        return "Articles gave no such work; nothing was done."
    if work.state == "reassigned":
        ctx.store.update("agent_work", row["id"], {"agent_label": work.agent_label})
        _turn(ctx, row["piece_id"], row["step"], f"The {row['role']} role passed to "
              f"{work.agent_label or 'another agent'}.")
        return "noted"
    if row["state"] != "open":
        return "noted"
    ctx.store.update("agent_work", row["id"], {"state": work.state, "ended": ctx.now()})
    if row["answered"] is not None or row["card_id"]:
        return "noted"  # handed back, or the owner was told already
    said = {"done": "finished its task without handing its work back",
            "set_aside": "set the task aside", "dropped": "dropped the task"}.get(
        work.state, work.state)
    why = f"The {row['role']} {said}" + (f": {work.note}" if work.note else ".")
    _turn(ctx, row["piece_id"], row["step"], why[:300])
    fresh = ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row["id"],)) or row
    if str(ctx.cfg().on_stall) == "finish_plain":
        return finish_plain(ctx, fresh, why)
    return tell(ctx, fresh, why)


# --- watching for stalls --------------------------------------------------------------------


def stalled_for(ctx: Ctx, row: dict[str, Any]) -> bool:
    """Rule ``stall_hours``: open work not handed back within its step's hours."""
    return ctx.now() - row["given"] >= hours(ctx, row["step"]) * HOUR_MS


def watch(ctx: Ctx) -> str:
    """The ``watch`` job: tidy what came back or moved on, and act on late work as
    ``on_stall`` says. Returns a line for the log."""
    tidy(ctx)
    done = []
    for row in ctx.store.all("SELECT * FROM agent_work WHERE state='open' AND answered IS "
                             "NULL AND card_id IS NULL ORDER BY id"):
        if stalled_for(ctx, row):
            done.append(late(ctx, row))
    return " ".join(done) or "Nothing late."


def late(ctx: Ctx, row: dict[str, Any]) -> str:
    who = f"the {row['role']}" + (f" ({row['agent_label']})" if row["agent_label"] else "")
    why = (f"Stuck at {WORDS[row['step']]} for {hours(ctx, row['step'])} hours: {who} has "
           "not handed it back.")
    policy = str(ctx.cfg().on_stall)
    if policy == "finish_plain":
        return finish_plain(ctx, row, why)
    if policy == "remind_then_tell" and not row["reminded"]:
        return remind(ctx, row)
    return tell(ctx, row, why)


def remind(ctx: Ctx, row: dict[str, Any]) -> str:
    """Once, through the work: the old task is withdrawn and the same one given again,
    marked as a reminder, so the clock starts again for it."""
    piece = ctx.store.piece(row["piece_id"]) or {}
    _withdraw(ctx, row["work_id"])
    ctx.store.update("agent_work", row["id"], {"state": "withdrawn", "ended": ctx.now()})
    again = give(ctx, piece, row["step"], row["agent_label"] or "", reminded=1) \
        if piece else None
    if again is None:
        return tell(ctx, row, f"Nobody holds the {row['role']} role any more, so the "
                    f"{WORDS[row['step']]} stopped.")
    _turn(ctx, row["piece_id"], row["step"], f"Reminded the {row['role']} once.")
    return f"Reminded the {row['role']}."


def choices(row: dict[str, Any]) -> list[dict[str, Any]]:
    """What the owner may do about work that waits on them: Wait, Finish without agents,
    Drop. Waiting makes no sense once the budget is spent or the agent has ended it."""
    if row["state"] == "budget" or row["state"] in ("done", "set_aside", "dropped"):
        return [FINISH_PLAIN, DROP]
    return [WAIT, FINISH_PLAIN, DROP]


def needs_owner(ctx: Ctx) -> list[dict[str, Any]]:
    """Agent work the owner was told about and has not yet decided. Each row carries
    ``why`` and the ``actions`` that resolve it, as the card offers them."""
    out = []
    for row in ctx.store.all("SELECT * FROM agent_work WHERE card_id IS NOT NULL AND "
                             "why != '' AND answered IS NULL AND state IN ('open', 'budget', "
                             "'done', 'set_aside', 'dropped') ORDER BY id"):
        piece = ctx.store.piece(row["piece_id"]) or {}
        if piece.get("state") != "stalled":
            continue
        out.append({"work_id": row["id"], "piece_id": row["piece_id"], "step": row["step"],
                    "role": row["role"], "why": row["why"],
                    "actions": [{"id": a["id"], "label": a["label"]} for a in choices(row)]})
    return out


def tell(ctx: Ctx, row: dict[str, Any], why: str) -> str:
    """A card to the owner: Wait, Finish without agents, or Drop. The article is marked
    stuck meanwhile; the agent may still hand its work back."""
    piece = ctx.store.piece(row["piece_id"]) or {}
    site = ctx.store.site(piece.get("site_id")) or {}
    if piece.get("state") in STATES_OF.get(row["step"], set()):
        ctx.store.move(piece["id"], "stalled", error=why[:500], came_from=piece["state"],
                       step_started=None, deadline_ms=None)
        _turn(ctx, piece["id"], row["step"], why)
    acts = choices(row)
    meaning = {"wait": "Wait gives it the same time again", "finish_plain":
               "Finish without agents does the rest the plain way", "drop": "Drop stops it"}
    body = f"{why}\n\n" + ". ".join(meaning[a["id"]] for a in acts) + "."
    card_id = None
    try:
        card_id = ctx.app.bench.file(
            "notice", f"{site.get('name', 'Articles')}: article {row['piece_id']} is "
            "stuck"[:200], body=body, subject=f"work:{row['id']}", actions=acts)
    except CallError as error:
        log.warning("could not tell the owner about work %s: %s", row["id"], error.message)
    ctx.store.update("agent_work", row["id"], {"card_id": card_id or "", "why": why[:500]})
    ctx.emit()
    return why


def plain_state(came_from: str) -> str:
    """Where an article goes on the plain way from the state it stopped in."""
    return "searching" if came_from == "researching" else came_from


def finish_plain(ctx: Ctx, row: dict[str, Any], why: str) -> str:
    """The rest of this article is done the plain way."""
    if row.get("state") == "open" and row.get("answered") is None:
        _withdraw(ctx, row.get("work_id"))
    ctx.store.update("agent_work", row["id"], {"state": "withdrawn", "ended": ctx.now()})
    ctx.store.set_meta(f"plain.{row['piece_id']}", True)
    piece = ctx.store.piece(row["piece_id"]) or {}
    if piece.get("state") == "stalled":
        ctx.store.move(piece["id"], plain_state(piece["came_from"] or row["step"]), error="",
                       came_from="")
    elif piece.get("state") == "researching":
        ctx.store.move(piece["id"], "searching")
    _turn(ctx, row["piece_id"], row["step"], f"{why} The rest is done without agents.")
    ctx.emit()
    return why


def tidy(ctx: Ctx) -> None:
    """Work handed back after its card was filed: the card is withdrawn. Work for a step
    the article has left is closed."""
    for row in ctx.store.all("SELECT * FROM agent_work WHERE answered IS NOT NULL AND "
                             "card_id IS NOT NULL AND card_id != ''"):
        cards.withdraw(ctx, row["card_id"])
        ctx.store.update("agent_work", row["id"], {"card_id": ""})
    for row in ctx.store.all("SELECT DISTINCT piece_id FROM agent_work WHERE state='open'"):
        settle_if_done(ctx, row["piece_id"])


def on_card(ctx: Ctx, decision: Decision) -> str:
    """The owner's choice on a stuck-work card."""
    subject = decision.subject or ""
    row_id = int(subject[5:]) if subject.startswith("work:") and subject[5:].isdigit() else 0
    row = ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row_id,))
    if row is None or row["card_id"] != decision.card_id:
        return "That card is no longer current; nothing was done."
    return choose(ctx, row, decision.action)


def from_page(ctx: Ctx, row_id: int, action: str) -> str:
    """The same choice made on Articles' own page: the card, if there is one, is
    withdrawn."""
    row = ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row_id,))
    if row is None or row["card_id"] is None or not row["why"]:
        raise WorkError("That work is not waiting for you any more. Nothing was done.")
    if action not in {a["id"] for a in choices(row)}:
        raise WorkError(f"You cannot choose {action!r} for this work now. Nothing was done.")
    cards.withdraw(ctx, row["card_id"] or None)
    return choose(ctx, row, action)


def choose(ctx: Ctx, row: dict[str, Any], action: str) -> str:
    """Wait, Finish without agents, or Drop, for one piece of work. Afterwards the row
    has no card (NULL), so it no longer waits on the owner."""
    if action not in {a["id"] for a in choices(row)}:
        return f"Articles cannot {action!r} this work now; nothing was done."
    ctx.store.update("agent_work", row["id"], {"card_id": None})
    piece = ctx.store.piece(row["piece_id"]) or {}
    if action == "wait":
        if piece.get("state") == "stalled":
            ctx.store.move(piece["id"], piece["came_from"] or row["step"], error="",
                           came_from="")
        _turn(ctx, row["piece_id"], row["step"], "You chose to wait.", role="owner")
        ctx.store.update("agent_work", row["id"], {"given": ctx.now()})
        ctx.emit()
        return "waiting"
    if action == "finish_plain":
        ctx.store.add_turn(row["piece_id"], row["step"], "You chose to finish it without "
                           "agents.", role="owner")
        return finish_plain(ctx, row, "You chose to finish it without agents.")
    _withdraw(ctx, row["work_id"] if row["state"] == "open" else None)
    ctx.store.update("agent_work", row["id"], {"state": "withdrawn", "ended": ctx.now()})
    if piece and states.allowed(piece["state"], "dropped"):
        from articles import actions  # noqa: PLC0415 - actions reaches the cards

        actions.drop(ctx, piece["id"])
    return "dropped"
