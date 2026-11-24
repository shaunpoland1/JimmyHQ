"""Topics: what the owner wants Reader to look for, edited on Reader's own screen.

A topic has a name, a few search phrasings, who it is for (the owner, or an audience
described in a sentence), what happens to an article found for it (kept in the list
only, shown on the Bench to read, mined into Memory, offered to other apps; more than
one may be chosen), whether the researcher may look for it, and whether it is paused.

Its health is plain counts (``health``): found, kept, skipped, last found, quiet for N
days. The owner's choices on its articles are what those counts are made of.
"""

from __future__ import annotations

from typing import Any

from reader.context import Ctx
from reader.store import CHOSEN, OUTCOMES, SUGGESTED

DAY_MS = 86_400_000
MAX_NAME = 80
MAX_PHRASING = 200
MAX_PHRASINGS = 8
MAX_AUDIENCE = 300

NO_TOPICS = ("Reader looks for articles on topics you choose, so it has nothing to look "
             "for yet. Add your first topic.")


class TopicError(ValueError):
    """The topic was not saved. The message says why and what to do."""


def _lines(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace("\r\n", "\n").split("\n")
    if not isinstance(value, list):
        return []
    return [" ".join(str(v).split()) for v in value if str(v).strip()]


def topic_problem(values: dict[str, Any]) -> str | None:
    """Rule ``topic_is_usable``: a name, one to eight phrasings of sensible length, and
    outcomes Reader knows. None when the topic can be saved."""
    name = " ".join(str(values.get("name") or "").split())
    if not name:
        return "A topic needs a name, so nothing was saved. Give it a short name."
    if len(name) > MAX_NAME:
        return f"Keep the name to {MAX_NAME} characters. Nothing was saved."
    phrasings = _lines(values.get("phrasings"))
    if not phrasings:
        return ("A topic needs at least one search phrasing, so nothing was saved. Write "
                "what you would type into a search box, one per line.")
    if len(phrasings) > MAX_PHRASINGS:
        return f"Keep it to {MAX_PHRASINGS} phrasings. Nothing was saved."
    if any(len(p) > MAX_PHRASING for p in phrasings):
        return f"Keep each phrasing to {MAX_PHRASING} characters. Nothing was saved."
    if len(str(values.get("audience") or "")) > MAX_AUDIENCE:
        return f"Keep who it is for to {MAX_AUDIENCE} characters. Nothing was saved."
    outcomes = values.get("outcomes")
    if outcomes is not None and (not isinstance(outcomes, list)
                                 or any(o not in OUTCOMES for o in outcomes)):
        return ("What happens to an article must be chosen from: kept in the list, on the "
                "Bench, mined into Memory, offered to other apps. Nothing was saved.")
    return None


def clean_outcomes(outcomes: Any, default: str) -> list[str]:
    """In Reader's order, without repeats. Every article is in the list anyway, so
    "list" stands alone only when nothing else was chosen."""
    chosen = [o for o in OUTCOMES if o in (outcomes if isinstance(outcomes, list)
                                            else [default])]
    others = [o for o in chosen if o != "list"]
    return others or ["list"]


def save(ctx: Ctx, values: dict[str, Any]) -> dict[str, Any]:
    """Add a topic, or change one (``id``)."""
    problem = topic_problem(values)
    if problem:
        raise TopicError(problem)
    store, now = ctx.store, ctx.now()
    name = " ".join(str(values["name"]).split())
    clash = store.one("SELECT id FROM topic WHERE name=? COLLATE NOCASE", (name,))
    row = {"name": name, "phrasings": _lines(values.get("phrasings")),
           "audience": " ".join(str(values.get("audience") or "").split()),
           "outcomes": clean_outcomes(values.get("outcomes"),
                                      str(ctx.cfg().default_outcome)),
           "researcher": 1 if values.get("researcher", True) else 0,
           "paused": 1 if values.get("paused") else 0, "updated": now}
    topic_id = values.get("id")
    if topic_id:
        if store.topic(int(topic_id)) is None:
            raise TopicError("There is no such topic. Nothing was saved.")
        if clash and clash["id"] != int(topic_id):
            raise TopicError(f"You have a topic called {name} already. Nothing was saved. "
                             "Choose another name.")
        store.update("topic", int(topic_id), row)
        return store.topic(int(topic_id)) or {}
    if clash:
        raise TopicError(f"You have a topic called {name} already. Nothing was saved. "
                         "Choose another name, or change that one.")
    return store.topic(store.insert("topic", {**row, "created": now})) or {}


def pause(ctx: Ctx, topic_id: int, paused: bool = True) -> dict[str, Any]:
    if ctx.store.topic(topic_id) is None:
        raise TopicError("There is no such topic. Nothing was changed.")
    ctx.store.update("topic", topic_id, {"paused": 1 if paused else 0, "updated": ctx.now()})
    return ctx.store.topic(topic_id) or {}


def remove(ctx: Ctx, topic_id: int) -> bool:
    """A topic is removed; its articles stay, with their topic shown as removed."""
    if ctx.store.topic(topic_id) is None:
        raise TopicError("There is no such topic. Nothing was removed.")
    ctx.store.conn.execute("DELETE FROM topic WHERE id=?", (topic_id,))
    ctx.store.conn.commit()
    return True


def active(ctx: Ctx) -> list[dict[str, Any]]:
    return [t for t in ctx.store.topics() if not t["paused"]]


def audience_line(topic: dict[str, Any] | None, given: str = "") -> str:
    """Who an article is for, in words: the owner ("you"), or the sentence given."""
    said = (given or (topic or {}).get("audience") or "").strip()
    return "you" if not said or said.lower() in ("owner", "me", "you") else said


# --- health ---------------------------------------------------------------------------------


def health(ctx: Ctx, topic: dict[str, Any]) -> dict[str, Any]:
    """Plain counts for one topic: found (suggested), kept (kept or sent on), skipped,
    read later, waiting, when it last found something and how many days it has been
    quiet (since then, or since it was made)."""
    marks = ",".join("?" * len(SUGGESTED))
    rows = ctx.store.all(f"SELECT state, COALESCE(suggested, created) AS at FROM article "  # noqa: S608
                         f"WHERE topic_id=? AND state IN ({marks})", (topic["id"], *SUGGESTED))
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in SUGGESTED}
    last = max((r["at"] for r in rows), default=None)
    since = last if last is not None else topic["created"]
    searched = ctx.store.one("SELECT MAX(created) AS at FROM search WHERE topic_id=?",
                             (topic["id"],))
    return {"found": len(rows), "kept": counts["kept"] + counts["sent"],
            "skipped": counts["skipped"], "later": counts["later"], "waiting": counts["new"],
            "decided": sum(counts[s] for s in CHOSEN), "last_found_ms": last,
            "last_searched_ms": searched["at"] if searched else None,
            "quiet_days": max(0, (ctx.now() - since) // DAY_MS),
            "quiet": is_quiet(ctx.now(), since, int(ctx.cfg().quiet_days))}


def is_quiet(now_ms: int, since_ms: int, quiet_days: int) -> bool:
    """Rule ``topic_gone_quiet``: nothing found for ``quiet_days`` or more."""
    return now_ms - since_ms >= quiet_days * DAY_MS


def votes(ctx: Ctx, topic_id: int, state_names: tuple[str, ...], most: int) -> list[str]:
    """Titles of the owner's latest choices for a topic, newest first."""
    if most <= 0:
        return []
    marks = ",".join("?" * len(state_names))
    return [r["title"] for r in ctx.store.all(
        f"SELECT title FROM article WHERE topic_id=? AND state IN ({marks}) AND title != '' "  # noqa: S608
        "ORDER BY decided DESC LIMIT ?", (topic_id, *state_names, most))]
