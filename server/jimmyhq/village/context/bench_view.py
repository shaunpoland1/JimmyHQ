"""What the agent has on the owner's Bench, and the owner's recent answers.

An agent that cannot see what happened to its work fills the gap with a guess: it
resends, rechecks, or counts its record by mood. So the view shows what is waiting,
what was decided in the last day and when, what is being held back and why, and the
whole tally since it was born.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.rules import rule
from jimmyhq.village.runtime import from_ms

# A card's status in the ``cards`` table (``comms/benchlink.py``), as the view groups it.
GROUPS = {"open": "open", "approved": "approved", "returned": "not_yet", "declined": "denied",
          "reopened": "reopened", "replaced": "replaced"}

LIMIT_IS_ON_READING = ("  (The limit is on what {owner} reads in a day, not on what you make: "
                       "keep doing useful work.)")


def _kind(card: dict[str, Any]) -> str:
    return str(card.get("kind", "")).rsplit(".", 1)[-1]  # village.work -> work


def _group(status: Any) -> str:
    return GROUPS.get(str(status or "open"), str(status))


def verdict_words(kind: str, group: str) -> str:
    if kind == "hand":  # a request to use an integration: the note says what became of it
        return "approved" if group == "approved" else "did not approve"
    if group == "approved":
        return "noted" if kind == "finding" else "approved (final)" if kind == "work" \
            else "approved"
    if group in ("not_yet", "replaced"):
        return {"work": "asked for changes to", "finding": "asked for more on"}.get(
            kind, "said not yet to")
    if group == "reopened":
        return "reopened"
    return "dismissed" if kind == "finding" else "said no to"


def dated(v: Any, ts_ms: Any) -> str:
    """When a note was written, so "tomorrow" in it has a day to count from."""
    if not ts_ms:
        return "undated"
    return f"written {v.when(from_ms(ts_ms)):%a %d %b %H:%M}"


def all_cards(v: Any, agent_id: str, seen: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every card this agent ever filed, for the tally."""
    return list(v.ask("comms.benchlink", "cards", agent_id, default=None) or seen)


def revision_sent(card: dict[str, Any], cards: list[dict[str, Any]],
                  queued: list[dict[str, Any]]) -> str:
    """For work the owner asked changes to: say so once the revision has gone or is
    queued. Otherwise the note still reads as an open request and gets rechecked. A
    revision with changed files is a new card that names the one it replaces."""
    files = set(card.get("paths") or [])
    if _group(card.get("status")) not in ("not_yet", "replaced") or _kind(card) != "work":
        return ""
    later = next((b for b in cards if b.get("card_id") != card.get("card_id")
                  and ((b.get("extra") or {}).get("replaces") == card.get("card_id")
                       or (b.get("filed_ts") or 0) >= (card.get("decided_ts") or 0)
                       and files & set(b.get("paths") or []))), None)
    if later:
        return f" (You sent your revision as {later['card_id']}; this one is answered.)"
    if any(files & set(q.get("paths") or []) for q in queued):
        return (" (Your revision is queued for tomorrow morning and goes by itself; this one "
                "is answered.)")
    return ""


@rule("bench_view", "village.context.bench_hours")
def bench_text(v: Any, agent_id: str) -> str:
    owner = v.owner_name()
    cap_owner = owner[:1].upper() + owner[1:]
    now, keep = v.clock.now(), int(v.ctx_s("bench_hours")) * 3600
    seen = [dict(c) for c in v.ask("comms.benchlink", "view", agent_id, default=[]) or []]
    queued = list(v.ask("comms.work", "queued", agent_id, default=[]) or [])
    held = list(v.ask("comms.findings", "queued", agent_id, default=[]) or [])
    cards = all_cards(v, agent_id, seen)
    lines = []
    for card in seen:
        group = _group(card.get("status"))
        if group == "open":
            # A finished task is told, not asked: nothing about it is waiting.
            lines.append(f"- {card['card_id']} told {owner} (they can reopen it): "
                         f"{card['title']}" if _kind(card) == "done"
                         else f"- {card['card_id']} waiting for {owner}: {card['title']}")
        elif group == "replaced" and not card.get("note"):
            continue  # an unanswered card whose new version took its place
        elif now - from_ms(card.get("decided_ts")) <= keep:
            note = (f'. The note ({dated(v, card.get("decided_ts"))}): "{card["note"]}"'
                    if card.get("note") else "")
            lines.append(f"- {cap_owner} {verdict_words(_kind(card), group)} {card['card_id']}: "
                         f"{card['title']}{note}{revision_sent(card, cards, queued)}")
    for q in queued:
        lines.append(f"- held back from {owner}'s bench: {q['title']}. {q['problem']}; it goes "
                     "by itself once that is done" if q.get("problem")
                     else f"- queued for tomorrow morning (goes by itself): {q['title']}")
    for q in held:
        lines.append(f"- finding held for {owner}'s bench (goes by itself when there is "
                     f"room): {q['title']}")
    if queued or held:
        lines.append(LIMIT_IS_ON_READING.format(owner=owner))
    if lines and cards:
        cards = [c for c in cards if _group(c.get("status")) != "replaced"]
        n = {k: sum(1 for c in cards if _group(c.get("status")) == k)
             for k in ("approved", "denied", "not_yet", "open")}
        lines.append(f"  (All told, since you were born: {len(cards)} items from you; "
                     f"{n['approved']} approved, {n['denied']} no, {n['not_yet']} not yet, "
                     f"{n['open']} waiting.)")
    return (f"On {owner}'s bench:\n" + "\n".join(lines)) if lines else ""
