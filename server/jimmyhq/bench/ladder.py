"""The permission ladder: how far each actor may go with each action.

A row is (actor, tool, action) at one of three levels:

- ``read``     may look, may not act
- ``approve``  may act once the owner approves it on the Bench
- ``alone``    may act without asking; what it did is filed as a "done" card,
               with an undo where the caller can offer one

The rules, all enforced here and again inside the fold so that no event on the
Tape can get round them:

- a row nobody has set is ``approve``;
- only the owner can raise a level; anything can lower one;
- nothing is barred from ``alone`` for good: the owner may raise any row, in one
  step. The cautious categories (``bench.caution_categories``: sending, deleting,
  spending and changing access, unless the owner changed the list) only say which
  kinds of action a screen should start at "ask" and say a calm word about;
- a "No" or an "Undo" on a card that belongs to an ``alone`` row drops the row
  back to ``approve`` on its own, unless the owner switched that off
  (``bench.undo_drops_alone``). The choice is written into the decision when it
  is made, so a replay reads the same answer whatever the setting says later.

Some categories could once never be ``alone``. An attempt from that time that was
refused stays refused however often the Tape is replayed: only a level the owner
set after the choice became theirs counts for those categories.

Callers declare the category of every action they check. The ladder also keeps
a tally of the owner's approvals and declines per row, so a screen can suggest
a promotion. Suggesting is all it does.
"""

from __future__ import annotations

import json
from typing import Any

from jimmyhq.bench import cards, rules
from jimmyhq.bench.rules import LEVELS, BenchError
from jimmyhq.core import Core
from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

# The categories that could never be "alone" before the owner was given the choice.
# They decide one thing now: how an event from that time is read. A level set since
# then says so itself (``unbarred`` on ``ladder.set``).
ONCE_BARRED = frozenset({"money", "message_person", "delete", "access"})
CAUTION_SETTING = "bench.caution_categories"

DEFAULT_LEVEL = "approve"


class LadderError(BenchError):
    """A ladder rule was broken. Carries a status and a sentence like BenchError."""


def _key(actor: str, tool: str, action: str) -> str:
    return json.dumps([actor, tool, action], ensure_ascii=False)


def _settled(known: str | None, declared: str | None) -> str | None:
    """A row's category after someone declares one. A row once known to be in a
    category that was barred keeps it: calling it something else later must not be
    a way to turn an old refusal into a yes."""
    if known in ONCE_BARRED:
        return known
    return declared or known


def cautious(core: Core, category: str | None) -> bool:
    """Rule ``caution_picks_the_default``: whether this kind of action is one the
    owner wants to start at "ask". It picks a default and a sentence on a screen.
    It never refuses a level the owner chooses."""
    return category is not None and category in set(core.settings.get(CAUTION_SETTING))


def owner_may_choose_any_level(core: Core, **asked: Any) -> None:
    """Rule ``owner_chooses_any_level``: the answer the ladder gives the hands
    module when the owner sets a level (``hands.set_level_gate``). Always yes: no
    second question and no limit that must be set first. Who is asking is checked
    where the level is saved: only the signed-in owner can raise one."""
    return None


def _held_back(row: dict[str, Any], category: str | None) -> bool:
    """True for "alone" that came from before the owner had the choice, on a row in
    a category that was barred then. It was never granted, so it is not granted."""
    return row["level"] == "alone" and not row.get("free") and category in ONCE_BARRED


def _rank(level: str) -> int:
    return LEVELS.index(level)


def _blank(actor: str, tool: str, action: str) -> dict[str, Any]:
    return {"actor": actor, "tool": tool, "action": action, "level": DEFAULT_LEVEL,
            "category": None, "approvals": 0, "declines": 0, "undos": 0, "streak": 0,
            "acted": 0, "set_at": None, "set_by": None, "free": False}


class LadderLens(Lens):
    """One entry per row that has ever been set, used or decided on.

    It also follows which cards with a row are still open (id and filer, no
    more), so a decision is tallied only if it took effect. A ``bench.decided``
    about a card that had already closed is one the owner was told did not
    count; the Tape can hold such events from before card writes were guarded.
    """

    name = "ladder"
    version = 3
    kinds = ("ladder.set", "ladder.acted", "bench.filed", "bench.withdrawn", "bench.decided")

    def initial(self) -> dict[str, Any]:
        return {"rows": {}, "open": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        p = event.payload
        if event.kind == "bench.filed":
            if p["ladder"]:
                state["open"].setdefault(p["card_id"], event.actor)
            return state
        if event.kind == "bench.withdrawn":
            if event.actor in ("owner", state["open"].get(p["card_id"])):
                state["open"].pop(p["card_id"], None)
            return state
        if event.kind == "bench.decided":
            if event.actor != "owner" or p["card_id"] not in state["open"]:
                return state  # not the owner's click, or the card had already closed
            if p["closes"]:
                del state["open"][p["card_id"]]
        where = p["ladder"] if event.kind == "bench.decided" else p
        if not where:
            return state  # a card that belongs to no row
        key = _key(where["actor"], where["tool"], where["action"])
        row = state["rows"].get(key) or _blank(where["actor"], where["tool"], where["action"])
        category = _settled(row["category"], where.get("category"))
        if event.kind == "ladder.set":
            if _rank(p["level"]) > _rank(row["level"]) and event.actor != "owner":
                return state  # only the owner raises
            free = bool(p.get("unbarred"))
            if p["level"] == "alone" and not free and category in ONCE_BARRED:
                return state  # refused when it was asked, so refused on every replay
            row.update(level=p["level"], set_at=event.ts, set_by=event.actor,
                       free=free and p["level"] == "alone")
        elif event.kind == "ladder.acted":
            row["acted"] += 1
        elif event.actor != "owner":
            return state  # only the owner's own click counts as a decision
        elif p["action"] == cards.APPROVE:
            row["approvals"] += 1
            row["streak"] += 1
        elif p["action"] in (cards.NO, cards.UNDO):
            row["declines" if p["action"] == cards.NO else "undos"] += 1
            row["streak"] = 0
            if row["level"] == "alone" and not p.get("keeps_level"):
                # Trust is taken back the moment it is misplaced. The owner can
                # raise the row again; nothing else can.
                row.update(level="approve", set_at=event.ts, set_by="bench", free=False)
        else:
            return state
        row["category"] = category
        if _held_back(row, category):
            # Raised before the owner had the choice, and it turned out to be a kind
            # that was barred then.
            row.update(level="approve", set_at=event.ts, set_by="bench")
        state["rows"][key] = row
        return state


class _Service:
    def __init__(self) -> None:
        self.lens = LadderLens()


def _rows(core: Core) -> dict[str, dict[str, Any]]:
    return core.lenses.read(core.service("bench.ladder", lambda _: _Service()).lens)["rows"]


def _names(actor: Any, tool: Any, action: Any) -> tuple[str, str, str]:
    return (rules.line(actor, "actor", 80), rules.name(tool, "tool", rules.NAME),
            rules.name(action, "action", rules.NAME))


def _category(value: Any, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    return rules.name(value, "category", rules.SLUG, 40)


def suggests_promotion(row: dict[str, Any], streak_needed: int) -> bool:
    """Rule ``promotion_is_a_suggestion``: after enough approvals in a row, a row
    that still asks may be suggested for "alone". Nothing here changes a level."""
    return row["level"] == "approve" and row["streak"] >= streak_needed


def _view(core: Core, row: dict[str, Any]) -> dict[str, Any]:
    shown = {k: v for k, v in row.items() if k != "free"}
    return {**shown, "cautious": cautious(core, row["category"]),
            "could_promote": suggests_promotion(
                row, int(core.settings.get("bench.promotion_streak")))}


def check(core: Core, actor: str, tool: str, action: str, category: str) -> str:
    """How far ``actor`` may go with this action: "read", "approve" or "alone".

    ``category`` says what kind of action it is ("send", "delete", "publish",
    your own word for anything else). The answer is the row's level: the owner
    may have set any row to "alone".
    """
    actor, tool, action = _names(actor, tool, action)
    category = _category(category, required=True)
    row = _rows(core).get(_key(actor, tool, action))
    if row is None:
        return DEFAULT_LEVEL
    if _held_back(row, category) or _held_back(row, row["category"]):
        return "approve"
    return row["level"]


def set_level(core: Core, actor: str, tool: str, action: str, level: str, *, by: str,
              category: str | None = None) -> dict[str, Any]:
    """Set a row's level and return the row. ``by`` is who is asking: anything
    may lower a level, only "owner" may raise one."""
    actor, tool, action = _names(actor, tool, action)
    category = _category(category, required=False)
    if level not in LEVELS:
        raise LadderError(422, f"level must be one of: {', '.join(LEVELS)}.")
    row = _rows(core).get(_key(actor, tool, action)) or _blank(actor, tool, action)
    category = _settled(row["category"], category)
    if _rank(level) > _rank(row["level"]) and by != "owner":
        raise LadderError(403, "Only the owner can give an actor more freedom. Ask them to "
                               "raise this on the ladder.")
    renewed = level == "alone" and not row.get("free")  # the owner says it again, freely
    if level != row["level"] or category != row["category"] or renewed:
        core.tape.print("ladder.set", {"actor": actor, "tool": tool, "action": action,
                                       "level": level, "category": category,
                                       "unbarred": True},
                        actor=by, subject=f"ladder:{actor}/{tool}/{action}")
    return row_of(core, actor, tool, action)


def row_of(core: Core, actor: str, tool: str, action: str) -> dict[str, Any]:
    row = _rows(core).get(_key(actor, tool, action)) or _blank(actor, tool, action)
    return _view(core, row)


def table(core: Core) -> dict[str, Any]:
    """Every known row with its tally, for the ladder screen."""
    rows = sorted(_rows(core).values(), key=lambda r: (r["actor"], r["tool"], r["action"]))
    return {"rows": [_view(core, r) for r in rows], "levels": list(LEVELS),
            "default_level": DEFAULT_LEVEL,
            "caution_categories": list(core.settings.get(CAUTION_SETTING)),
            "promotion_streak": int(core.settings.get("bench.promotion_streak"))}


def record_alone_action(core: Core, *, actor: str, tool: str, action: str, category: str,
                        title: str, summary: str = "", body: str = "", undo: bool = False,
                        kind: str | None = None, source: str | None = None,
                        cause: str | None = None, **card: Any) -> str:
    """Record that ``actor`` acted without asking, and file the "done" card that
    tells the owner. Returns the card id.

    Call it after the action, and only when ``check`` answered "alone": it
    refuses otherwise. Pass ``undo=True`` when the action can be taken back; the
    card then carries an Undo button, and the caller reacts to that decision
    (``on_decision``, or ``bench.decided`` events on the Tape).
    """
    if check(core, actor, tool, action, category) != "alone":
        raise LadderError(403, f"{actor} may not do this without asking. File a card on the "
                               "Bench and wait for the owner's approval.")
    card_id = cards.file_card(
        core, filed_by=actor, cause=cause, kind=kind or f"{tool}.{action}",
        source=source or tool, title=title, summary=summary, body=body, lane="done",
        undo=undo, ladder={"actor": actor, "tool": tool, "action": action,
                           "category": category}, **card,
    )
    core.tape.print("ladder.acted", {"actor": actor, "tool": tool, "action": action,
                                     "category": category, "card_id": card_id},
                    actor=actor, subject=f"ladder:{actor}/{tool}/{action}", cause=cause)
    return card_id
