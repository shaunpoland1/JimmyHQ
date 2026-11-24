"""The flow with agents: who takes each step, the work given and handed back, budgets and
stalls. The code decides every move; an agent's judgment arrives only through the record
tools in ``tools`` and is checked there.

    proposed -> reading -> overlap_check (held_overlap raises the card)
      -> writing (work given to the writer) -> drafted (the code checks run)
      -> in_check (work given to the checker) -> revising (until max_rounds, by the code)
      -> ready (the draft card on the Bench);  and stalled, dropped

Reading and the overlap check are always the code's own, so nothing is asked of a writer
before Posts has read the source and asked "have we covered this lately?". A step whose
role is empty is done the plain way (``plain``) for that step alone, and the piece keeps a
line of who did each step (``credits``).

Record tools may not reach outside the app, so they only write: what goes out (giving
work, a card, withdrawing work) happens here, from the ``advance`` and ``watch`` jobs.

Budgets and stalls, all settings: ``agent_steps_per_piece`` counts the agents' tool calls
on one piece; ``stall_hours_*`` say how long each step may wait; ``on_stall`` says what
then (remind once through the work and then a card to the owner, a card at once, or
finish the plain way). Booking never waits on an agent: no agent has a booking tool.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq_app import CallError, Decision, Work

from posts import cards, checked, credits, plain
from posts.context import Ctx

log = logging.getLogger("posts")

HOUR_MS = 3_600_000
PLAIN = object()  # route's answer: do this step the plain way


class WorkError(ValueError):
    """The owner's choice could not be carried out. The message says why."""

# The steps an agent may take, and the role that takes each.
STEP_ROLE = {"writing": "writer", "revising": "writer", "in_check": "checker"}
STALLING = ("research", "writing", "revising", "in_check")  # the steps a stall clock runs on
CREDIT_STEP = {"writing": "writing", "captioning": "writing", "revising": "writing",
               "in_check": "checking"}
WORDS = {"research": "research", "writing": "writing", "revising": "revising",
         "in_check": "checking"}

WAIT = {"id": "wait", "label": "Wait", "style": "primary", "closes": True}
FINISH_PLAIN = {"id": "finish_plain", "label": "Finish without agents", "closes": True}
DROP = {"id": "drop", "label": "Drop", "style": "danger", "closes": True}
TOOL = "app_posts_"  # how the agents see Posts' tools named


# --- who holds the roles --------------------------------------------------------------------


def holders(ctx: Ctx) -> dict[str, str]:
    """role -> the agent's label ("" when the platform gives none), for each filled role.
    With no ``agents.tools`` grant, or no answer, every role counts as empty.

    A label is for the owner's line only, never for telling agents apart: two agents
    that have not chosen a name share one, and an agent may choose a new name at any
    time. Who an agent is comes with each tool call (``tools.not_own_draft``), and the
    platform itself never lets one agent hold both the writer and the checker role
    (the manifest's ``not_with``)."""
    try:
        found = ctx.app.roles.get()
    except CallError:
        return {}
    return {role: (held.agent_label or "") for role, held in found.items() if held.filled}


def plain_only(ctx: Ctx, piece_id: int) -> bool:
    """The owner chose to finish this piece without agents (or ``on_stall`` did)."""
    return bool(ctx.store.get_meta(f"plain.{piece_id}"))


# --- budgets --------------------------------------------------------------------------------


def steps_used(ctx: Ctx, piece_id: int) -> int:
    return ctx.store.one("SELECT COUNT(*) AS n FROM agent_step WHERE piece_id=?",
                         (piece_id,))["n"]


def spent(ctx: Ctx, piece_id: int) -> bool:
    """Rule ``step_budget``: the agents' tool calls on this piece reached
    ``agent_steps_per_piece``."""
    return steps_used(ctx, piece_id) >= int(ctx.cfg().agent_steps_per_piece)


SPENT = ("This piece's budget of {n} agent steps is spent, so nothing was read and nothing "
         "more will be given to an agent for it. Hand back what you have now with "
         "{tool}, or finish your task and leave it.")


def spend(ctx: Ctx, piece_id: int, agent: str, role: str | None, tool: str, *,
          submit: bool = False) -> str | None:
    """Count one agent step on the piece. Past the budget a reading tool is refused with
    the sentence returned; a submission is still taken (it is "what you have") but no
    longer counted, so the count never passes the budget."""
    if spent(ctx, piece_id):
        if submit:
            return None
        hand_back = "submit_review" if role == "checker" or tool == "review_packet" \
            else "submit_draft"
        return SPENT.format(n=int(ctx.cfg().agent_steps_per_piece), tool=TOOL + hand_back)
    ctx.store.insert("agent_step", {"piece_id": piece_id, "agent": agent[:120], "role": role,
                                    "tool": tool, "created": ctx.now()})
    return None


# --- the work rows --------------------------------------------------------------------------


def waiting(ctx: Ctx, piece_id: int, step: str) -> dict[str, Any] | None:
    """The work given for this step that has not been handed back."""
    return ctx.store.one("SELECT * FROM agent_work WHERE piece_id=? AND step=? AND "
                         "state='open' AND answered IS NULL ORDER BY id DESC LIMIT 1",
                         (piece_id, step))


def answer(ctx: Ctx, piece_id: int, step: str, *, partial: bool = False) -> None:
    """An agent handed back this step (a record tool calls this: it only writes)."""
    row = waiting(ctx, piece_id, step)
    if row is None:
        return
    if partial:
        ctx.store.update("agent_work", row["id"], {"partial": row["partial"] + 1})
    else:
        ctx.store.update("agent_work", row["id"], {"answered": ctx.now()})


def answer_research(ctx: Ctx, page_id: int) -> None:
    for row in ctx.store.all("SELECT id FROM agent_work WHERE step='research' AND page_id=? "
                             "AND state='open' AND answered IS NULL", (page_id,)):
        ctx.store.update("agent_work", row["id"], {"answered": ctx.now()})


def _withdraw(ctx: Ctx, work_id: str | None) -> None:
    if not work_id:
        return
    try:
        ctx.app.work.withdraw(work_id)
    except CallError as error:
        log.info("could not withdraw work %s: %s", work_id, error.message)


def _hours(ctx: Ctx, step: str) -> int:
    """How long a step may wait for its agent: ``stall_hours_research``, ``_write`` or
    ``_check``."""
    cfg = ctx.cfg()
    if step == "research":
        return int(cfg.stall_hours_research)
    if step == "in_check":
        return int(cfg.stall_hours_check)
    return int(cfg.stall_hours_write)


def _give(ctx: Ctx, *, role: str, step: str, title: str, details: str, page_id: int,
          piece_id: int | None, label: str, reminded: int = 0) -> int | None:
    """Give one piece of work and keep its row. None when nobody holds the role now."""
    subject = f"piece:{piece_id}" if piece_id else f"page:{page_id}"
    try:
        work_id = ctx.app.work.give(role, title[:200], details=details[:4000], subject=subject,
                                    due_ms=ctx.now() + _hours(ctx, step) * HOUR_MS
                                    if step in STALLING else None)
    except CallError as error:
        if error.code == "not_found":
            return None
        raise
    return ctx.store.insert("agent_work", {
        "piece_id": piece_id, "page_id": page_id, "step": step, "role": role,
        "work_id": work_id, "state": "open", "reminded": reminded,
        "agent_label": label or None, "given": ctx.now()})


def _ask_for(ctx: Ctx, piece: dict[str, Any], step: str) -> tuple[str, str]:
    """The task's title and details, which point the agent at its tools."""
    page = ctx.store.page(piece["page_id"]) or {}
    name, n = page.get("name", "a brand"), piece["id"]
    if step == "in_check":
        return (f"Check the draft of piece {n} for {name}",
                f"Read it with {TOOL}review_packet (piece={n}), then hand back your review "
                f"with {TOOL}submit_review (piece={n}). You did not write it; check it "
                "against the article's own sentences.")
    if step == "revising":
        why = "the owner sent it back with a note" if piece["owner_note"] \
            else "the checker raised points"
        return (f"Revise the draft of piece {n} for {name}",
                f"{why[0].upper()}{why[1:]}. Read them with {TOOL}brief (piece={n}), then "
                f"hand back the new draft with {TOOL}submit_draft (piece={n}).")
    return (f"Write a post for {name} (piece {n})",
            f"Read the material with {TOOL}brief (piece={n}) and your earlier drafts with "
            f"{TOOL}my_past (page={piece['page_id']}), then hand back the draft with "
            f"{TOOL}submit_draft (piece={n}).")


# --- routing a step -------------------------------------------------------------------------


def route(ctx: Ctx, piece: dict[str, Any]) -> Any:
    """Who takes this piece's step now. ``PLAIN``: do it the plain way. None: it is with
    an agent, or waits. A string: what was done (work given, a review taken)."""
    state = piece["state"]
    if state == "overlap_check":
        return overlap(ctx, piece)
    role = STEP_ROLE.get(state)
    if role is None or plain_only(ctx, piece["id"]):
        return PLAIN
    if state == "in_check":
        review = pending_review(ctx, piece)
        if review is not None:
            return decide_review(ctx, piece, review)
    row = waiting(ctx, piece["id"], state)
    if row is not None:
        if row["partial"] and spent(ctx, piece["id"]):
            return hand_on(ctx, piece, row)
        return None
    found = holders(ctx)
    if role not in found:
        return PLAIN
    if spent(ctx, piece["id"]):
        return budget_spent(ctx, piece, state, role)
    if state == "writing" and not plain.may_start_writing(ctx, piece):
        return None
    title, details = _ask_for(ctx, piece, state)
    label = found[role]
    if _give(ctx, role=role, step=state, title=title, details=details,
             page_id=piece["page_id"], piece_id=piece["id"], label=label) is None:
        return PLAIN
    who = f"the {role}" + (f" ({label})" if label else "")
    return _turn(ctx, piece["id"], state, f"Given to {who}: {title}.")


def after_plain(ctx: Ctx, piece: dict[str, Any]) -> None:
    """A plain step finished: the piece remembers the plain way did it."""
    step = CREDIT_STEP.get(piece["state"])
    if step:
        why = "the owner chose to finish without agents" if plain_only(ctx, piece["id"]) \
            else ""
        credits.add(ctx.store, piece["id"], step, "plain", note=why)


def _turn(ctx: Ctx, piece_id: int, step: str, gloss: str, **more: Any) -> str:
    ctx.store.add_turn(piece_id, step, gloss, **more)
    return gloss


def overlap(ctx: Ctx, piece: dict[str, Any]) -> str:
    """The code's own overlap check, before any writing is asked for. When it holds a
    piece a researcher proposed, the researcher is told, through its work."""
    said = plain.overlap_step(ctx, piece)
    held = ctx.store.piece(piece["id"]) or {}
    found = credits.latest(ctx.store, piece["id"], "research")
    if held.get("state") == "held_overlap" and found and found["by"] == "agent":
        label = holders(ctx).get("researcher", "")
        _give(ctx, role="researcher", step="note", page_id=piece["page_id"],
              piece_id=piece["id"], label=label,
              title=f"Your proposal, piece {piece['id']}, was held as too close",
              details=f"{said} The owner decides whether it is drafted anyway. Look at "
                      f"{TOOL}recent (page={piece['page_id']}) before you propose again, "
                      "and choose something the brand has not covered.")
    return said


def hand_on(ctx: Ctx, piece: dict[str, Any], row: dict[str, Any]) -> str:
    """The budget is spent and the writer kept a draft back: what there is goes on."""
    ctx.store.update("agent_work", row["id"], {"answered": ctx.now()})
    post = ctx.store.post_of(piece["id"])
    more = {"round": piece["round"] + 1, "owner_note": ""} if piece["state"] == "revising" \
        else {}
    ctx.store.move_piece(piece["id"], "drafted", **more)
    credits.add(ctx.store, piece["id"], "writing", "agent", role="writer",
                label=row["agent_label"], note="kept back; the budget was spent")
    if post:
        checked.run(ctx, post["id"])
    return _turn(ctx, piece["id"], piece["state"], "The piece's agent steps are spent, so "
                 "the draft the writer kept back goes on as it is.")


def budget_spent(ctx: Ctx, piece: dict[str, Any], step: str, role: str) -> str:
    """Nothing more is given to an agent: the owner chooses, on a card."""
    n = int(ctx.cfg().agent_steps_per_piece)
    why = (f"Its budget of {n} agent steps is spent before the {WORDS[step]}, so no more work "
           "was given to an agent.")
    row_id = ctx.store.insert("agent_work", {
        "piece_id": piece["id"], "page_id": piece["page_id"], "step": step, "role": role,
        "state": "budget", "why": why, "given": ctx.now()})
    return tell(ctx, ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row_id,)) or {},
                why)


# --- reviews --------------------------------------------------------------------------------


def pending_review(ctx: Ctx, piece: dict[str, Any]) -> dict[str, Any] | None:
    post = ctx.store.post_of(piece["id"])
    if post is None:
        return None
    return ctx.store.one("SELECT * FROM review WHERE post_id=? AND revision=? AND used IS "
                         "NULL ORDER BY id DESC LIMIT 1", (post["id"], post["revision"]))


def decide_review(ctx: Ctx, piece: dict[str, Any], review: dict[str, Any]) -> str:
    """The code, never the model, decides: revise while points stand and rounds are left
    (``max_rounds``), otherwise the draft goes to the owner."""
    post = ctx.store.post_of(piece["id"]) or {}
    points = list(review["points"])
    ctx.store.update("review", review["id"], {"used": ctx.now()})
    results = checked.run(ctx, post["id"], checker_points=points)
    rounds = int(ctx.cfg().max_rounds)
    if review["verdict"] == "revise" and points and piece["round"] < rounds:
        ctx.store.move_piece(piece["id"], "revising")
        return _turn(ctx, piece["id"], "in_check", f"The checker raised {len(points)} point"
                     f"{'s' if len(points) != 1 else ''}; the draft goes back to be revised.")
    ctx.store.move_piece(piece["id"], "ready")
    cards.file_draft(ctx, post["id"])
    settle(ctx, piece["id"])
    stop = " The code stopped the revisions here." if review["verdict"] == "revise" else ""
    return _turn(ctx, piece["id"], "in_check", f"Checked.{stop} On your Bench: "
                 f"{checked.summary(results)}")


def settle(ctx: Ctx, piece_id: int) -> None:
    """The piece left the agents' hands: withdraw any work still open for it."""
    for row in ctx.store.all("SELECT * FROM agent_work WHERE piece_id=? AND state='open'",
                             (piece_id,)):
        if row["answered"] is None:
            _withdraw(ctx, row["work_id"])
        ctx.store.update("agent_work", row["id"], {"state": "closed", "ended": ctx.now()})


# --- when work comes back -------------------------------------------------------------------


def on_work(ctx: Ctx, work: Work) -> str:
    """The platform says a piece of work ended or changed hands."""
    row = ctx.store.one("SELECT * FROM agent_work WHERE work_id=?", (work.id,))
    if row is None:
        return "Posts gave no such work; nothing was done."
    if work.state == "reassigned":
        ctx.store.update("agent_work", row["id"], {"agent_label": work.agent_label})
        if row["piece_id"]:
            _turn(ctx, row["piece_id"], row["step"], f"The {row['role']} role passed to "
                  f"{work.agent_label or 'another agent'}.")
        return "noted"
    if row["state"] != "open":
        return "noted"
    ctx.store.update("agent_work", row["id"], {"state": work.state, "ended": ctx.now()})
    if row["answered"] is not None or row["step"] == "note" or row["card_id"]:
        return "noted"  # handed back, only a note, or the owner was told already
    said = {"done": "finished its task without handing anything back",
            "set_aside": "set the task aside", "dropped": "dropped the task"}.get(
        work.state, work.state)
    why = f"The {row['role']} {said}" + (f": {work.note}" if work.note else ".")
    if row["piece_id"]:
        _turn(ctx, row["piece_id"], row["step"], why[:300])
    fresh = ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row["id"],)) or row
    if str(ctx.cfg().on_stall) == "finish_plain" and row["piece_id"]:
        return finish_plain(ctx, fresh, why)
    return tell(ctx, fresh, why)


# --- watching for stalls --------------------------------------------------------------------


def stalled_for(ctx: Ctx, row: dict[str, Any]) -> bool:
    """Rule ``stall_hours``: open work not handed back within its step's hours."""
    return ctx.now() - row["given"] >= _hours(ctx, row["step"]) * HOUR_MS


def watch(ctx: Ctx) -> str:
    """The ``watch`` job: tidy cards for work that came back, act on late work as
    ``on_stall`` says, and ask the researcher for ideas. Returns a line for the log."""
    tidy(ctx)
    done = []
    for row in ctx.store.all("SELECT * FROM agent_work WHERE state='open' AND answered IS "
                             "NULL AND card_id IS NULL AND step != 'note' ORDER BY id"):
        if not stalled_for(ctx, row):
            continue
        done.append(late(ctx, row))
    done.extend(ask_researcher(ctx))
    return " ".join(done) or "Nothing late."


def late(ctx: Ctx, row: dict[str, Any]) -> str:
    hours = _hours(ctx, row["step"])
    who = f"the {row['role']}" + (f" ({row['agent_label']})" if row["agent_label"] else "")
    why = f"Stuck at {WORDS[row['step']]} for {hours} hours: {who} has not handed it back."
    policy = str(ctx.cfg().on_stall)
    if policy == "finish_plain" and row["piece_id"]:
        return finish_plain(ctx, row, why)
    if policy == "remind_then_tell" and not row["reminded"]:
        return remind(ctx, row)
    return tell(ctx, row, why)


def remind(ctx: Ctx, row: dict[str, Any]) -> str:
    """Once, through the work: the old task is withdrawn and the same one given again,
    marked as a reminder, so the clock starts again for it."""
    piece = ctx.store.piece(row["piece_id"]) if row["piece_id"] else None
    if piece is not None:
        title, details = _ask_for(ctx, piece, row["step"])
    else:
        title, details = _research_ask(ctx, ctx.store.page(row["page_id"]) or {})
    _withdraw(ctx, row["work_id"])
    ctx.store.update("agent_work", row["id"], {"state": "withdrawn", "ended": ctx.now()})
    again = _give(ctx, role=row["role"], step=row["step"], page_id=row["page_id"],
                  piece_id=row["piece_id"], label=row["agent_label"] or "", reminded=1,
                  title=f"Reminder: {title}", details=f"This is waiting for you. {details}")
    if again is None:
        return tell(ctx, row, f"Nobody holds the {row['role']} role any more, so the "
                    f"{WORDS[row['step']]} stopped.")
    if piece is not None:
        _turn(ctx, piece["id"], row["step"], f"Reminded the {row['role']} once.")
    return f"Reminded the {row['role']}."


def choices(row: dict[str, Any], piece: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What the owner may do about work that waits on them: Wait, Finish without agents,
    Drop. Waiting makes no sense once the budget is spent or the agent has ended it."""
    acts = [WAIT, FINISH_PLAIN, DROP] if piece is not None else [WAIT, DROP]
    if row["state"] == "budget" or row["state"] in ("done", "set_aside", "dropped"):
        acts = [a for a in acts if a is not WAIT] if piece is not None else [DROP]
    return acts


def needs_owner(ctx: Ctx) -> list[dict[str, Any]]:
    """Agent work the owner was told about and has not yet decided: a stuck piece (late
    work, or its budget spent) or a researcher that did not answer. Each row carries
    ``why`` and the ``actions`` that resolve it, as the card offers them."""
    out = []
    for row in ctx.store.all("SELECT * FROM agent_work WHERE card_id IS NOT NULL AND "
                             "why != '' AND state IN ('open', 'budget', 'done', 'set_aside', "
                             "'dropped') AND answered IS NULL ORDER BY id"):
        piece = ctx.store.piece(row["piece_id"]) if row["piece_id"] else None
        if piece is not None and (piece["state"] != "stalled"
                                  or (piece["came_from"] or row["step"]) != row["step"]):
            continue
        if piece is None and row["piece_id"]:
            continue
        out.append({"work_id": row["id"], "piece_id": row["piece_id"],
                    "page_id": row["page_id"], "step": row["step"], "role": row["role"],
                    "why": row["why"],
                    "actions": [{"id": a["id"], "label": a["label"]}
                                for a in choices(row, piece)]})
    return out


def tell(ctx: Ctx, row: dict[str, Any], why: str) -> str:
    """A card to the owner: Wait, Finish without agents, or Drop. The piece is marked
    stuck meanwhile; the agent may still hand its work back."""
    piece = ctx.store.piece(row["piece_id"]) if row.get("piece_id") else None
    page = ctx.store.page(row["page_id"]) or {}
    if piece is not None and piece["state"] == row["step"]:
        ctx.store.move_piece(piece["id"], "stalled", error=why[:500], came_from=row["step"])
        _turn(ctx, piece["id"], row["step"], why)
    acts = choices(row, piece)
    what = f"piece {piece['id']}" if piece else f"ideas for {page.get('name', 'a brand')}"
    meaning = {"wait": "Wait gives it the same time again", "finish_plain":
               "Finish without agents does the rest the plain way",
               "drop": "Drop stops it" if piece else "Drop stops asking until tomorrow"}
    body = f"{why}\n\n" + ". ".join(meaning[a["id"]] for a in acts) + "."
    card_id = None
    try:
        card_id = ctx.app.bench.file("notice", f"{page.get('name', 'Posts')}: {what} is "
                                     "stuck"[:200], body=body, subject=f"work:{row['id']}",
                                     actions=acts)
    except CallError as error:
        log.warning("could not tell the owner about work %s: %s", row["id"], error.message)
    ctx.store.update("agent_work", row["id"], {"card_id": card_id or "", "why": why[:500]})
    ctx.emit("posts.changed")
    return why


def finish_plain(ctx: Ctx, row: dict[str, Any], why: str) -> str:
    """The rest of this piece is done the plain way."""
    if row.get("state") == "open" and row.get("answered") is None:
        _withdraw(ctx, row.get("work_id"))
    ctx.store.update("agent_work", row["id"], {"state": "withdrawn", "ended": ctx.now()})
    ctx.store.set_meta(f"plain.{row['piece_id']}", True)
    piece = ctx.store.piece(row["piece_id"]) or {}
    if piece.get("state") == "stalled":
        ctx.store.move_piece(piece["id"], piece["came_from"] or row["step"], error="",
                             came_from="")
    _turn(ctx, row["piece_id"], row["step"], f"{why} The rest is done without agents.")
    ctx.emit("posts.changed")
    return why


def tidy(ctx: Ctx) -> None:
    """Work handed back after its card was filed: the card is withdrawn."""
    for row in ctx.store.all("SELECT * FROM agent_work WHERE answered IS NOT NULL AND "
                             "card_id IS NOT NULL AND card_id != ''"):
        cards._withdraw(ctx, row["card_id"])  # noqa: SLF001
        ctx.store.update("agent_work", row["id"], {"card_id": ""})


def on_card(ctx: Ctx, decision: Decision) -> str:
    """The owner's choice on a stuck-work card."""
    subject = decision.subject or ""
    row_id = int(subject[5:]) if subject.startswith("work:") and subject[5:].isdigit() else 0
    row = ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row_id,))
    if row is None or row["card_id"] != decision.card_id:
        return "That card is no longer current; nothing was done."
    return choose(ctx, row, decision.action)


def from_page(ctx: Ctx, row_id: int, action: str) -> str:
    """The same choice made on Posts' own page: the card, if there is one, is withdrawn."""
    row = ctx.store.one("SELECT * FROM agent_work WHERE id=?", (row_id,))
    if row is None or row["card_id"] is None or not row["why"]:
        raise WorkError("That work is not waiting for you any more. Nothing was done.")
    piece = ctx.store.piece(row["piece_id"]) if row["piece_id"] else None
    if action not in {a["id"] for a in choices(row, piece)}:
        raise WorkError(f"You cannot choose {action!r} for this work now. Nothing was done.")
    cards._withdraw(ctx, row["card_id"] or None)  # noqa: SLF001
    said = choose(ctx, row, action)
    ctx.emit("posts.changed")
    return said


def choose(ctx: Ctx, row: dict[str, Any], action: str) -> str:
    """Wait, Finish without agents, or Drop, for one piece of work. Afterwards the row
    has no card (NULL), so it no longer waits on the owner."""
    ctx.store.update("agent_work", row["id"], {"card_id": None})
    piece = ctx.store.piece(row["piece_id"]) if row["piece_id"] else None
    if action == "wait":
        if piece is not None and piece["state"] == "stalled":
            ctx.store.move_piece(piece["id"], piece["came_from"] or row["step"], error="",
                                 came_from="")
            _turn(ctx, piece["id"], row["step"], "You chose to wait.", role="owner")
        ctx.store.update("agent_work", row["id"], {"given": ctx.now()})
        return "waiting"
    if action == "finish_plain" and piece is not None:
        ctx.store.add_turn(piece["id"], row["step"], "You chose to finish it without agents.",
                           role="owner")
        return finish_plain(ctx, row, "You chose to finish it without agents.")
    if action == "drop":
        _withdraw(ctx, row["work_id"] if row["state"] == "open" else None)
        ctx.store.update("agent_work", row["id"], {"state": "withdrawn", "ended": ctx.now()})
        if piece is not None:
            from posts import actions  # noqa: PLC0415

            actions.drop(ctx, piece["id"])
        return "dropped"
    return f"Posts does not know the action {action!r}; nothing was done."


# --- asking the researcher ------------------------------------------------------------------


def _research_ask(ctx: Ctx, page: dict[str, Any]) -> tuple[str, str]:
    n = page.get("id")
    return (f"Find something worth posting for {page.get('name', 'a brand')}",
            f"See what the brand covered lately with {TOOL}recent (page={n}) and what went "
            f"well with {TOOL}what_did_well (page={n}). Then propose one article with "
            f"{TOOL}propose (page={n}): its address, the angle, and why the brand's followers "
            "would care. Posts reads it and checks it against what the brand covered before "
            "anyone writes.")


def ask_researcher(ctx: Ctx) -> list[str]:
    """Once a day for each page that is not paused and has room for another piece, when
    the researcher role is held and ``research_daily`` is on."""
    cfg = ctx.cfg()
    found = holders(ctx)
    if not bool(cfg.research_daily) or "researcher" not in found:
        return []
    today, said = ctx.day(ctx.now()), []
    most = int(cfg.pieces_per_day)
    for page in ctx.store.active_pages():  # a paused page gets no new work
        rows = ctx.store.all("SELECT * FROM agent_work WHERE step='research' AND page_id=? "
                             "ORDER BY id DESC", (page["id"],))
        if any(r["state"] == "open" and r["answered"] is None for r in rows):
            continue
        if any(ctx.day(r["given"]) == today for r in rows):
            continue
        started = sum(1 for p in ctx.store.all("SELECT created FROM piece WHERE page_id=?",
                                               (page["id"],)) if ctx.day(p["created"]) == today)
        if most > 0 and started >= most:
            continue
        title, details = _research_ask(ctx, page)
        if _give(ctx, role="researcher", step="research", title=title, details=details,
                 page_id=page["id"], piece_id=None, label=found["researcher"]) is not None:
            said.append(f"Asked the researcher about {page['name']}.")
    return said
