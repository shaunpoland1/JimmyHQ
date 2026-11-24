"""Pages, rules and lessons: the owner's configuration, kept as records.

A page is one publishing identity: how it sounds, when it posts, through which accounts,
and which tags it must carry. A first run has none, and says how to add one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from posts.store import Store

NO_PAGES = ("You have no brands yet. Add one under Brands: give it a name and one sentence "
            "on how it sounds, and Posts can start drafting for it.")
_HOUR = re.compile(r"^([01]\d|2[0-3]):[0-5]\d\Z")
MAX_SAMPLES = 10_000
MAX_REGISTER = 400


class PageError(ValueError):
    """What the owner gave cannot be kept. The message says what to change."""


def _text(value: Any, field: str, most: int, *, required: bool = False) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        article = "an" if field[:1] in "aeiou" else "a"
        raise PageError(f"A brand needs {article} {field}. Nothing was saved.")
    if len(text) > most:
        raise PageError(f"The {field} is {len(text)} characters; keep it to {most}. Nothing "
                        "was saved.")
    return text


def check_timezone(value: Any) -> str:
    """'' (the owner's own) or an IANA name this computer knows."""
    name = _text(value, "time zone", 64)
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise PageError(f"\"{name}\" is not a time zone Posts knows. Choose another "
                        "from the list, or \"Use mine\" to follow yours. Nothing was "
                        "saved.") from None
    return name


def check_hours(value: Any) -> list[str]:
    hours = value if isinstance(value, list) else [h for h in str(value or "").split(",")]
    out = sorted({str(h).strip() for h in hours if str(h).strip()})
    if not out:
        raise PageError("A brand needs at least one posting time, such as \"08:00\". Nothing "
                        "was saved.")
    for hour in out:
        if not _HOUR.match(hour):
            raise PageError(f"\"{hour}\" is not a time of day. Write posting times as "
                            "\"08:00\" or \"17:30\". Nothing was saved.")
    return out


def check_accounts(value: Any, known: Iterable[str] | None = None) -> list[dict[str, str]]:
    """Each account a scheduler, an id and a label. With ``known``, the scheduler must be
    one Posts offers; the same account may not be listed twice."""
    offered = set(known) if known is not None else None
    out: list[dict[str, str]] = []
    for number, raw in enumerate(value or [], start=1):
        if not isinstance(raw, dict):
            raise PageError("Each account is a scheduler, an account id and a label. Nothing "
                            "was saved.")
        account = {"scheduler": _text(raw.get("scheduler"), "account's scheduler", 40,
                                      required=True),
                   "account_id": _text(raw.get("account_id"), "account id", 120,
                                       required=True),
                   "label": _text(raw.get("label"), "account label", 80)}
        if offered is not None and account["scheduler"] not in offered:
            raise PageError(f"Account {number} is on a scheduler Posts does not offer "
                            f"(\"{account['scheduler']}\"). Choose one from the list, or "
                            "remove it. Nothing was saved.")
        if any(a["scheduler"] == account["scheduler"] and a["account_id"] ==
               account["account_id"] for a in out):
            raise PageError(f"Account {number} is listed twice. Remove one. Nothing was "
                            "saved.")
        out.append(account)
    return out


def check_book_through(value: Any, known: Iterable[str] | None = None) -> str:
    """'' (follow Posts' setting) or the id of a tool Posts offers."""
    chosen = _text(value, "tool to book through", 40)
    if chosen and known is not None and chosen not in set(known):
        raise PageError(f"This brand is set to book through \"{chosen}\", which Posts does "
                        "not offer. Choose one from the list, or \"Posts' setting\". "
                        "Nothing was saved.")
    return chosen


def check_tags(value: Any) -> list[str]:
    tags = value if isinstance(value, list) else str(value or "").split()
    return [t for t in (str(x).strip() for x in tags) if t][:20]


def save_page(store: Store, given: dict[str, Any], *,
              known: Iterable[str] | None = None) -> dict[str, Any]:
    """Add a page, or change one when ``id`` is given. ``known`` is the schedulers Posts
    offers: an account must be on one of them, and so must the tool the page books
    through (``book_through``, '' for Posts' setting)."""
    values = {
        "name": _text(given.get("name"), "name", 80, required=True),
        "register": _text(given.get("register"), "sentence on how it sounds", MAX_REGISTER),
        "samples": _text(given.get("samples"), "writing samples", MAX_SAMPLES),
        "timezone": check_timezone(given.get("timezone")),
        "hours": check_hours(given.get("hours", ["08:00"])),
        "accounts": check_accounts(given.get("accounts"), known),
        "tags": check_tags(given.get("tags")),
        "updated": store.now(),
    }
    if "book_through" in given or given.get("id") is None:
        # A save that does not mention it (an older caller) leaves a page's choice alone.
        values["book_through"] = check_book_through(given.get("book_through"), known)
    clash = store.one("SELECT id FROM page WHERE name=?", (values["name"],))
    page_id = given.get("id")
    if clash and clash["id"] != page_id:
        raise PageError(f"You already have a brand called \"{values['name']}\". Choose another "
                        "name. Nothing was saved.")
    if page_id is None:
        page_id = store.insert("page", {**values, "created": store.now()})
    else:
        if store.page(int(page_id)) is None:
            raise PageError("There is no such brand. Nothing was saved.")
        store.update("page", int(page_id), values)
    return store.page(int(page_id)) or {}


def need_page(store: Store, page_id: Any) -> dict[str, Any]:
    if not store.pages():
        raise PageError(NO_PAGES)
    try:
        page = store.page(int(page_id))
    except (TypeError, ValueError):
        page = None
    if page is None:
        raise PageError("Choose one of your brands for this piece. Nothing was started.")
    if page.get("paused"):
        raise PageError(f"{page['name']} is paused, so nothing was started. Resume it under "
                        "Brands, or choose another brand.")
    return page


# --- pausing and removing a page ------------------------------------------------------------

# A post in one of these is queued, or on its way to or at the scheduler.
ON_THE_WAY = ("queued", "booking", "booked", "unsure", "missing", "failed")
_PIECE_DONE = ("closed", "dropped")


def known_page(store: Store, page_id: Any, nothing: str) -> dict[str, Any]:
    try:
        page = store.page(int(page_id))
    except (TypeError, ValueError):
        page = None
    if page is None:
        raise PageError(f"There is no such brand. {nothing}")
    return page


def set_paused(store: Store, page_id: Any, paused: Any) -> dict[str, Any]:
    """Pause a page, or resume it. A paused page keeps everything it has; it only gets
    no new work: no daily ask to the researcher, no idea from Reader, no new piece."""
    page = known_page(store, page_id, "Nothing was changed.")
    store.update("page", page["id"], {"paused": int(bool(paused)), "updated": store.now()})
    return store.page(page["id"]) or {}


def removal(store: Store, page_id: Any) -> dict[str, Any]:
    """Rule ``a_page_with_posts_on_the_way_stays``: what removing a page would do, and
    whether it may be done. ``refused`` is the sentence that says why not: a page with a
    post queued or booked cannot be removed. Otherwise the counts say what goes with it
    (``working``: pieces still open, ``drafts`` of them waiting for the owner; ``rules``
    of its own) and what is kept (``kept``: posts that went out or were dropped)."""
    page = known_page(store, page_id, "Nothing was removed.")
    marks = ", ".join("?" * len(ON_THE_WAY))
    on_the_way = store.one(f"SELECT COUNT(*) AS n FROM post WHERE page_id=? AND state IN "  # noqa: S608
                           f"({marks})", (page["id"], *ON_THE_WAY))["n"]
    working = store.one("SELECT COUNT(*) AS n FROM piece WHERE page_id=? AND state NOT IN "
                        "(?, ?)", (page["id"], *_PIECE_DONE))["n"]
    drafts = store.one("SELECT COUNT(*) AS n FROM post o JOIN piece i ON i.id = o.piece_id "
                       "WHERE o.page_id=? AND o.state IN ('draft', 'revising') AND "
                       "i.state NOT IN (?, ?)", (page["id"], *_PIECE_DONE))["n"]
    rules = store.one("SELECT COUNT(*) AS n FROM rule WHERE page_id=?", (page["id"],))["n"]
    kept = store.one("SELECT COUNT(*) AS n FROM post WHERE page_id=? AND state IN "
                     "('went_out', 'declined')", (page["id"],))["n"]
    refused = ""
    if on_the_way:
        refused = (f"{page['name']} has {on_the_way} post{'' if on_the_way == 1 else 's'} "
                   "queued or booked, so it was not removed. Cancel each one in the Queue or "
                   "let it go out first, or pause the brand instead.")
    return {"id": page["id"], "name": page["name"], "refused": refused,
            "on_the_way": on_the_way, "working": working, "drafts": drafts, "rules": rules,
            "kept": kept}


def save_rule(store: Store, given: dict[str, Any]) -> dict[str, Any]:
    kind = given.get("kind")
    if kind not in ("phrase", "tag", "question"):
        raise PageError("A rule is a phrase to flag, a tag a post must carry, or a question "
                        "to ask on every draft. Nothing was saved.")
    page_id = given.get("page_id")
    if page_id is not None and store.page(int(page_id)) is None:
        raise PageError("There is no such brand. Nothing was saved.")
    rule_id = store.insert("rule", {
        "page_id": page_id, "kind": kind,
        "text": _text(given.get("text"), "rule's text", 300, required=True),
        "says": _text(given.get("says"), "rule's explanation", 300), "created": store.now()})
    return store.one("SELECT * FROM rule WHERE id=?", (rule_id,)) or {}


_QUOTED = re.compile(r"[\"“]([^\"”]{3,200})[\"”]")


def quoted_spans(note: str) -> list[str]:
    """What the owner put in quotation marks in a note: each becomes a phrase to flag."""
    return [m.group(1).strip() for m in _QUOTED.finditer(note or "") if m.group(1).strip()]


def add_lesson(store: Store, *, page_id: int, post_id: int, kind: str, reason: str = "",
               note: str = "") -> int:
    """A decline reason or a send-back note, kept for every later draft of the page."""
    return store.insert("lesson", {"page_id": page_id, "post_id": post_id, "kind": kind,
                                   "reason": reason[:200], "note": note[:2000],
                                   "quotes": quoted_spans(note), "created": store.now()})
