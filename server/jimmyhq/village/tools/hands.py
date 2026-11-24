"""Outside integrations for an agent: the bridge between the village and ``jimmyhq.hands``.

Each action the owner switched on for an agent is one tool for that agent, named after
the account and the action, with only the arguments the owner left open. The tools are
built for each beat from the owner's rules (``offered``), so an agent with no rules has
none, and there is no other switch. They are offered while working and exploring.

Everything that decides whether a call may leave is in the hands core: the level, the
limits, the caps, the repeat cache, the exactly-once ledger. This module does three things
with the core's answer:

- **It ran** (``Called``). The result is a line written here or by the core, and it is the
  only thing logged. What the integration brought back is shown once, in the next view,
  framed as untrusted. An action that changed something without asking leaves a "done"
  card for the owner, with Undo where the provider can undo.
- **It needs the owner's yes** (``NeedsApproval``). Nobody was called. A card goes to the
  Bench showing exactly what would be done: the action, the account, every argument as it
  would be sent, who receives it and how many. The agent now waits on the owner
  (``World.submit``). It cannot change the request; it can withdraw it. The executor in
  ``jimmyhq.hands.execute`` performs the frozen request when the owner approves.
- **It was refused** (``HandRefused``). The core's sentence goes to the agent. A limit or
  a missing set-up does not frustrate.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from jimmyhq import hands
from jimmyhq.hands import execute
from jimmyhq.hands.rules import ladder_row
from jimmyhq.village.comms import Refused, benchlink, owner
from jimmyhq.village.tools import logged
from jimmyhq.village.tools.seams import EXPLORE, WORK, category_of, refuse, refuse_as, text

log = logging.getLogger("jimmyhq.village.tools")

CAPABILITY = "hands"
KIND = "hand"
MODES = (WORK, EXPLORE)
WITHDRAW = "withdraw_request"
RESERVED = frozenset({"thought"})  # every tool's first argument is the agent's thought
LOGGED_CHARS = 200  # of one argument, in the log


def said(args: dict[str, Any]) -> dict[str, Any]:
    """An agent's arguments as the log keeps them: the start of each, scrubbed."""
    short = {k: (v[:LOGGED_CHARS] if isinstance(v, str) else v) for k, v in (args or {}).items()}
    return logged.args(short)


# --- which tools --------------------------------------------------------------------------


async def offered(v: Any, agent_id: str, taken: Any = ()) -> list[Any]:
    """Rule ``hand_tools_from_rules``: this agent's integration tools right now, one for
    each action the owner switched on for it, as the hands core lists them. A name the
    village already uses, or an argument called "thought", cannot be offered."""
    from jimmyhq.village.catalog import Tool

    out, taken = [], {*taken, WITHDRAW}
    for spec in await hands.tools_for(v.core, agent_id):
        if spec.name in taken or RESERVED & set(spec.params):
            log.warning("the integration tool %s cannot be offered to %s", spec.name, agent_id)
            continue
        out.append(Tool(name=spec.name, kind=KIND, modes=MODES, description=spec.description,
                        params=spec.params, required=list(spec.required),
                        run=_runner(spec.name), capability=CAPABILITY))
    return out


def _runner(tool: str) -> Any:
    async def run(ctx: Any, args: dict[str, Any]) -> Any:
        return await use(ctx, tool, args)

    return run


# --- one call -----------------------------------------------------------------------------


def _done(ctx: Any, result: str, kept: dict[str, Any], *, ok: bool = True,
          shown: str | None = None) -> Any:
    from jimmyhq.village.catalog import Done

    return Done(result=result, args_logged=kept, category=category_of(ctx.mode), ok=ok,
                shown=shown)


async def use(ctx: Any, tool: str, args: dict[str, Any]) -> Any:
    """Run one integration tool as the hands core allows it."""
    v, kept = ctx.v, said(args)
    try:
        out = await hands.call(v.core, agent=ctx.agent_id, tool=tool, arguments=args)
    except hands.HandRefused as e:
        refuse(e.rule, e.sentence, frustrate=e.frustrate, args_logged=kept)
    if isinstance(out, hands.NeedsApproval):
        return ask(ctx, out, kept)
    return called(ctx, out, kept)


def called(ctx: Any, out: Any, kept: dict[str, Any]) -> Any:
    """Rule ``hand_result_is_village_written``: what is logged about a call is a line
    written by JimmyHQ. Whatever the integration said, an error included, is shown once
    and never logged."""
    v = ctx.v
    if out.ok is None:
        return _done(ctx, f"{out.tool}: the outcome is unknown, and it will not be tried "
                          "again", kept, ok=False, shown=out.shown)
    if not out.ok:
        return _done(ctx, f"{out.tool} did not work", kept, ok=False,
                     shown=f"{out.tool} did not work. What came back:\n\n"
                           f"{hands.shape.framed(out.shown)}")
    result = out.result
    if out.effect != "read" and out.level == "alone" and out.run_id and not out.cached:
        card_id = execute.file_done(v.core, out.run_id, approved=False)
        if card_id:
            result += f"; {owner(v)} was told (card {card_id})"
    return _done(ctx, result, kept, shown=out.shown)


# --- asking the owner ---------------------------------------------------------------------


def _value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def card_fields(v: Any, need: Any) -> dict[str, Any]:
    """Rule ``hand_card_shows_exactly``: the card for a request. The action in plain
    words, the account, every argument in full as it will be sent (the owner's fixed
    values included), who receives it and how many, and the request's fingerprint."""
    name = getattr(v, "name", lambda a: a)(need.agent)
    lines = [f"{name} asks to use \"{need.label}\" on the account \"{need.account}\".",
             "Nothing has been done yet. If you approve, exactly this is sent, and nothing "
             "else:", ""]
    for arg in sorted(need.arguments):
        lines += [f"--- {arg} ---", _value(need.arguments[arg]), ""]
    if not need.arguments:
        lines += ["(This action takes no arguments.)", ""]
    lines.append("--- end of what is sent ---")
    details = [("Account", need.account), ("Action", f"{need.label} ({need.action})"),
               ("What it does", need.effect)]
    if need.recipients:
        details.append((f"Goes to {len(need.recipients)}",
                        ", ".join(need.recipients)))
    if need.amount is not None:
        details.append(("Amount", _value(need.amount)))
    details += [("Can be undone", "Yes" if need.can_undo else "No"),
                ("Fingerprint", need.fingerprint)]
    why = None if need.why == "ask" else (
        f"This usually runs without asking. It is asking now because {need.why}.")
    return {"title": f"{name} asks: {need.label} on {need.account}",
            "summary": f"Waits for your yes: {len(need.arguments)} values"
                       + (f", to {len(need.recipients)} recipients" if need.recipients else "")
                       + ". Nothing has been done yet.",
            "body": "\n".join(lines), "why": why or "", "details": details}


def ask(ctx: Any, need: Any, kept: dict[str, Any]) -> Any:
    """File the card for a request, tie it to the frozen request, and start the wait."""
    v, agent_id = ctx.v, ctx.agent_id
    try:
        row = benchlink.file(
            v, agent_id, benchlink.HAND, **card_fields(v, need),
            actions=[dict(a) for a in execute.REQUEST_ACTIONS],
            ladder=ladder_row(need.agent, need.tool, need.effect),
            extra={"fingerprint": need.fingerprint, "tool": need.tool,
                   "account": need.account})
    except Refused as e:
        hands.decline(v.core, need.fingerprint)  # no card, so nobody could ever approve it
        refuse_as(e, kept)
    hands.attach_card(v.core, need.fingerprint, row["card_id"])
    return _done(ctx, f"asked {owner(v)} to approve {need.tool} (card {row['card_id']}). "
                      f"Nothing was done yet. You are waiting on {owner(v)}: do other work, "
                      "and do not ask again", kept)


async def withdraw_request(ctx: Any, args: dict[str, Any]) -> Any:
    """Rule ``hand_request_withdrawn``: an agent takes back its own unanswered request.
    The request is closed for good before the card goes, so a yes that arrives at the
    same moment performs nothing."""
    v, agent_id = ctx.v, ctx.agent_id
    card_id = benchlink.card_id_of(text(args, "card"))
    kept = {"card": card_id[:40]}
    card = benchlink.get(v, card_id)
    if (card is None or card["agent"] != agent_id or card["kind"] != benchlink.HAND
            or card["status"] != benchlink.OPEN):
        mine = [c["card_id"] for c in benchlink.waiting(v, agent_id)
                if c["kind"] == benchlink.HAND]
        refuse("hand_request_withdrawn",
               f"Nothing was withdrawn: {card_id[:40] or 'that'} is not a request of yours "
               f"that {owner(v)} has not answered. You have {len(mine)} such requests"
               + (": " + ", ".join(mine[-5:]) if mine else "") + ".", args_logged=kept)
    if not hands.decline(v.core, card["extra"].get("fingerprint", "")):
        refuse("hand_request_withdrawn",
               f"Nothing was withdrawn: {owner(v)} has already answered {card_id}. The "
               "answer will show in your bench view.", frustrate=False, args_logged=kept)
    benchlink.replace(v, card, "The agent withdrew this request.")
    benchlink.rebuild_waiting(v, agent_id)
    return _done(ctx, f"withdrew the request {card_id}: it will not be performed", kept)


SPECS = [
    {"name": WITHDRAW, "kind": "self", "modes": MODES, "capability": CAPABILITY,
     "run": withdraw_request,
     "description": "Take back a request to use an integration that {owner} has not "
                    "answered yet. A request cannot be changed once made: if it was wrong, "
                    "withdraw it and ask again.",
     "params": {"card": {"type": "string",
                         "description": "The card's id, as shown in your bench view."}},
     "required": ["card"]},
]

RULES = [
    ("hand_tools_from_rules", "hands.max_tools_per_agent", offered),
    ("hand_result_is_village_written", None, called),
    ("hand_card_shows_exactly", None, card_fields),
    ("hand_request_withdrawn", None, withdraw_request),
]
