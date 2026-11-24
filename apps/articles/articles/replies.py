"""Readers' replies, and the ideas for articles found in them. Private.

The owner pastes replies to an email (many at once). The code splits them, cleans each,
and keeps them in the app's PRIVATE store (``replies.db``, opened with
``app.private_db``: deleted rows are overwritten). They are never written on the Tape,
never offered to Memory, never given to an agent's tool and never shown to another app;
the page is told only counts. Each piece of cleaning is a named function:

- ``split``: a new reply starts after a separator line (``---``, ``===``, ``___``,
  ``***``) or at a ``From:`` line;
- ``strip_quoted``: the quoted email ("On ... wrote:", forwarded or original-message
  markers, and lines starting ``>``) and everything after it is cut;
- ``strip_headers``: mail header lines (From, To, Cc, Subject, Date, Sent, Reply-To);
- ``strip_signature``: a ``-- `` line, "Sent from my ...", or a sign-off ("Thanks,",
  "Best,") and the short lines after it;
- ``mask_contacts``: email addresses become [email], phone numbers [phone], web
  addresses [link];
- then too-short replies are dropped and repeats (by a hash of the cleaned text) kept once.

What is NOT removed: a name, a place or any other detail a reader wrote inside the body
of a reply. The page's hint says so.

"Find ideas" sends the cleaned replies, as untrusted text and off the record, to the
``role_ideas`` model, on the owner's own machines unless ``ideas_hosted`` is on. Each
idea must cite the replies it rests on by number; the code drops an idea that cites none,
that copies a run of a reply's words, that carries contact details, or that repeats an
idea proposed or dismissed before. Replies are marked read only once ideas are kept, so a
failed look can simply be tried again.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from jimmyhq_app import CallError, word_overlap

from articles import markup
from articles.context import Ctx

AUDIENCE_MOST = 60
PASTE_MOST = 400_000
_SEPARATOR = re.compile(r"^\s*(?:-{3,}|={3,}|_{3,}|\*{3,})\s*$")
_FROM = re.compile(r"^\s*From:\s", re.I)
_QUOTED_START = re.compile(
    r"^\s*(?:On\s.{0,200}\swrote:|-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,}|"
    r"Begin forwarded message:|-----Original Message-----)", re.I)
_HEADER = re.compile(r"^\s*(?:From|To|Cc|Bcc|Subject|Date|Sent|Reply-To):\s", re.I)
_SENT_FROM = re.compile(r"^\s*Sent from my\b", re.I)
_SIGN_OFF = re.compile(r"^\s*(?:thanks|thank you|many thanks|best|best wishes|regards|kind "
                       r"regards|warm regards|cheers|sincerely|yours|all the best|love|"
                       r"take care)[\s,.!]*$", re.I)
# An address is looked for only where a run of its characters begins, never in the middle of
# one: otherwise the search starts again at every letter of a long run and takes time in
# proportion to its square.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
SIGN_OFF_TAIL = 3  # short lines after a sign-off that are a name or a title

IDEAS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ideas": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "angle": {"type": "string"},
                       "why": {"type": "string"},
                       "replies": {"type": "array", "items": {"type": "integer"}}},
        "required": ["title", "angle", "why", "replies"]}}},
    "required": ["ideas"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS reply (
  id INTEGER PRIMARY KEY, created INTEGER NOT NULL, audience TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL, sha TEXT NOT NULL UNIQUE, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS idea (
  id INTEGER PRIMARY KEY, created INTEGER NOT NULL, audience TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL, angle TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '',
  reply_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'new'
  CHECK (status IN ('new', 'started', 'dismissed')), piece_id INTEGER, decided INTEGER);
"""

HINT = ("Paste the replies here, many at once: put a line of --- between them, or paste "
        "them with their From: lines. Articles takes out quoted earlier mail, mail headers, "
        "signatures and sign-offs, and turns email addresses, phone numbers and web "
        "addresses into [email], [phone] and [link]. It does not take out names, places "
        "or other details a reader wrote in the reply itself, so leave out anything you "
        "would not want kept. Replies stay in a private store on this computer: never on "
        "your record, never in Memory, never shown to agents or other apps. Finding ideas "
        "sends them to a model on your own computer unless you allow a hosted one under "
        "Articles' settings. Delete every reply, below, removes them all for good; the ideas "
        "already found stay.")


class ReplyError(ValueError):
    """Nothing was kept or found. The message says why and what to do instead."""


class ReplyStore:
    """The private store. Its own database, deleted for good with ``forget``."""

    def __init__(self, db: Any, clock: Any):
        self.db = db
        self.now = clock
        self.db.conn.row_factory = _dict_row
        self.db.conn.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return list(self.db.execute(sql, params).fetchall())

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        return self.db.execute(sql, params).fetchone()


def _dict_row(cursor: Any, row: tuple) -> dict[str, Any]:
    return {d[0]: v for d, v in zip(cursor.description, row, strict=True)}


# --- cleaning, one rule each ----------------------------------------------------------------


def split(text: str) -> list[str]:
    """Rule ``where a reply starts``: after a separator line, or at a From: line."""
    pieces: list[list[str]] = [[]]
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _SEPARATOR.match(line):
            pieces.append([])
            continue
        if _FROM.match(line) and any(x.strip() for x in pieces[-1]) and \
                not _quoting(pieces[-1]):
            pieces.append([])
        pieces[-1].append(line)
    return ["\n".join(p).strip() for p in pieces if "\n".join(p).strip()]


def _quoting(lines: list[str]) -> bool:
    """Whether a reply so far has reached its quoted email (a From: after it is quoted)."""
    return any(_QUOTED_START.match(line) for line in lines)


def strip_quoted(text: str) -> str:
    """Rule ``no quoted mail``: everything from the quoted email on, and quote lines."""
    out = []
    for line in text.split("\n"):
        if _QUOTED_START.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out)


def strip_headers(text: str) -> str:
    """Rule ``no mail headers``."""
    return "\n".join(line for line in text.split("\n") if not _HEADER.match(line))


def strip_signature(text: str) -> str:
    """Rule ``no signature``: from a ``-- `` line or "Sent from my", or a sign-off line
    with at most ``SIGN_OFF_TAIL`` short lines after it."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.rstrip() in ("--", "-- ") or _SENT_FROM.match(line):
            return "\n".join(lines[:i])
        if _SIGN_OFF.match(line):
            rest = [x for x in lines[i + 1:] if x.strip()]
            if len(rest) <= SIGN_OFF_TAIL and all(len(x.split()) <= 6 for x in rest):
                return "\n".join(lines[:i])
    return text


_YEARS = re.compile(r"^(1[89]|20)\d\d\s*[-–]\s*(1[89]|20)?\d\d$")
PHONE_DIGITS = 7  # digits that make a run of numbers a phone number, not a date or a range


def mask_contacts(text: str) -> str:
    """Rule ``no contact details``: email addresses, web addresses and phone numbers
    masked."""
    text = _EMAIL.sub("[email]", text)
    text = markup.URL.sub("[link]", text)
    return _PHONE.sub(lambda m: "[phone]" if is_phone(m.group(0)) else m.group(0), text)


def is_phone(found: str) -> bool:
    """Enough digits for a phone number, and not a span of years such as 2019-2021."""
    if _YEARS.match(found.strip()):
        return False
    return sum(ch.isdigit() for ch in found) >= PHONE_DIGITS


CUT_SLACK = 400  # kept past the limit while contacts are masked, so none is cut in half


def clean(text: str, most: int) -> str:
    text = strip_signature(strip_headers(strip_quoted(text)))
    # Cut to the limit before anything is searched, so a paste of any size costs no more
    # than a short one. The cut falls between words: no address is left half masked.
    text = " ".join(text.split())
    if len(text) > most + CUT_SLACK:
        text = text[:most + CUT_SLACK]
        words = text.rsplit(None, 1)
        if len(words) == 2:
            text = words[0]
    text = mask_contacts(text)
    return " ".join(text.split())[:most]


def fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode("utf-8")).hexdigest()


# --- pasting --------------------------------------------------------------------------------


def paste(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Keep the pasted replies, cleaned. Answers counts only."""
    text = str(args.get("text") or "")
    if not text.strip():
        raise ReplyError("You pasted nothing. Nothing was kept. Paste the replies, then "
                         "press Keep these replies.")
    if len(text) > PASTE_MOST:
        raise ReplyError(f"That paste is over {PASTE_MOST} characters. Nothing was kept. "
                         "Paste the replies in smaller batches.")
    audience = " ".join(str(args.get("audience") or "").split())[:AUDIENCE_MOST]
    cfg = ctx.cfg()
    store = ctx.replies
    kept = short = repeats = 0
    for raw in split(text):
        reply = clean(raw, int(cfg.reply_max_chars))
        if len(reply) < int(cfg.reply_min_chars):
            short += 1
            continue
        sha = fingerprint(reply)
        if store.one("SELECT id FROM reply WHERE sha=?", (sha,)):
            repeats += 1
            continue
        store.db.execute("INSERT INTO reply (created, audience, text, sha) VALUES (?, ?, ?, ?)",
                         (ctx.now(), audience, reply, sha))
        kept += 1
    store.db.commit()
    ctx.emit()
    return {"kept": kept, "too_short": short, "repeats": repeats, **status(ctx)}


def status(ctx: Ctx) -> dict[str, Any]:
    store = ctx.replies
    count = store.one("SELECT COUNT(*) AS n, SUM(used=0) AS unread FROM reply") or {}
    return {"replies": int(count.get("n") or 0), "unread": int(count.get("unread") or 0),
            "hint": HINT}


def forget(ctx: Ctx) -> dict[str, Any]:
    """Delete every reply for good. Ideas stay, without the replies they rested on."""
    gone = ctx.replies.db.purge("reply", "1=1")
    return {"deleted": gone, **status(ctx)}


# --- finding ideas --------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def copies_reply(text: str, replies: list[str], words: int) -> bool:
    """Rule ``an idea is in its own words``: it repeats no run of ``words`` words in a
    row from any reply."""
    mine = _norm(text).split()
    runs = {" ".join(mine[i:i + words]) for i in range(len(mine) - words + 1)}
    return any(run and run in _norm(reply) for run in runs for reply in replies)


def repeats_idea(title: str, earlier: list[str], share: float) -> bool:
    """Rule ``never proposed again``: the same title as an idea proposed or dismissed
    before, or sharing ``share`` of its three-word runs."""
    mine = _norm(title)
    return any(mine == _norm(t) or word_overlap(title, t) >= share for t in earlier)


def take_ideas(data: Any, replies: list[dict[str, Any]], earlier: list[str], cfg: Any
               ) -> tuple[list[dict[str, Any]], int]:
    """The ideas that hold to every rule, and how many were set aside."""
    raw = data.get("ideas") if isinstance(data, dict) else None
    kept: list[dict[str, Any]] = []
    aside = 0
    texts = [r["text"] for r in replies]
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or len(kept) >= int(cfg.ideas_per_run):
            aside += 1
            continue
        title = " ".join(str(item.get("title") or "").split())[:200]
        angle = " ".join(str(item.get("angle") or "").split())[:600]
        why = " ".join(str(item.get("why") or "").split())[:600]
        cited = sorted({n for n in item.get("replies") or [] if isinstance(n, int)
                        and not isinstance(n, bool) and 1 <= n <= len(replies)})
        words = f"{title} {angle} {why}"
        if (not cited or len(title) < int(cfg.idea_min_title_chars)
                or copies_reply(words, texts, int(cfg.idea_copied_words))
                or mask_contacts(words) != words or '"' in words or "“" in words
                or repeats_idea(title, earlier + [k["title"] for k in kept],
                                float(cfg.idea_same_share))):
            aside += 1
            continue
        kept.append({"title": title, "angle": angle, "why": why,
                     "reply_ids": [replies[n - 1]["id"] for n in cited]})
    return kept, aside


def ideas_prompt(most: int) -> str:
    return (f"Group the readers' replies below into at most {most} ideas for articles. Each "
            "idea gives a short title, an angle, why readers want it, and \"replies\": the "
            "numbers of the replies it rests on. Prefer what several readers raised. Write "
            "in your own words: no quotations, no names, nothing that could identify a "
            "reader. Leave out any idea like one on the list of earlier ideas.")


def find(ctx: Ctx) -> dict[str, Any]:
    """Ask a model for ideas in the replies not read yet. Keeps the ones that hold."""
    cfg = ctx.cfg()
    store = ctx.replies
    replies = store.all("SELECT id, audience, text FROM reply WHERE used=0 ORDER BY id LIMIT ?",
                        (int(cfg.ideas_replies_per_run),))
    if not replies:
        raise ReplyError("There are no new replies to look at. Nothing was asked. Paste some "
                         "first.")
    earlier = [r["title"] for r in store.all("SELECT title FROM idea ORDER BY id")]
    listed = "\n".join(f"[{n}]{f' ({r["audience"]})' if r["audience"] else ''} {r['text']}"
                       for n, r in enumerate(replies, 1))
    untrusted = [f"Readers' replies:\n{listed}"]
    if earlier:
        untrusted.append("Earlier ideas, not to propose again:\n"
                         + "\n".join(f"- {t}" for t in earlier[-200:]))
    try:
        reply = ctx.app.model.ask(ideas_prompt(int(cfg.ideas_per_run)),
                                  role=str(cfg.role_ideas), json_schema=IDEAS_SCHEMA,
                                  untrusted=untrusted, stay_local=not bool(cfg.ideas_hosted),
                                  off_record=True)
    except CallError as error:
        raise ReplyError(f"No ideas were found: {error.message} The replies are kept, and "
                         "you can try again.") from None
    data = reply.data
    if data is None:
        import json  # noqa: PLC0415

        try:
            data = json.loads(reply.text)
        except ValueError:
            data = None
    kept, aside = take_ideas(data, replies, earlier, cfg)
    audience = next((r["audience"] for r in replies if r["audience"]), "")
    for idea in kept:
        store.db.execute("INSERT INTO idea (created, audience, title, angle, why, reply_ids) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (ctx.now(), audience, idea["title"], idea["angle"], idea["why"],
                          _json(idea["reply_ids"])))
    marks = ",".join("?" * len(replies))
    store.db.execute(f"UPDATE reply SET used=1 WHERE id IN ({marks})",  # noqa: S608
                     tuple(r["id"] for r in replies))
    store.db.commit()
    ctx.emit()
    return {"found": len(kept), "set_aside": aside, "local": answered_here(reply), **status(ctx)}


def answered_here(reply: Any) -> bool:
    """Whether a model on the owner's own machines answered."""
    return bool(getattr(reply, "local", False))


def _json(value: Any) -> str:
    import json  # noqa: PLC0415

    return json.dumps(value)


def ideas(ctx: Ctx) -> dict[str, Any]:
    """The ideas waiting, as the page shows them: no reply's text, only how many."""
    import json  # noqa: PLC0415

    rows = ctx.replies.all("SELECT * FROM idea WHERE status='new' ORDER BY id DESC")
    return {"ideas": [{"id": r["id"], "title": r["title"], "angle": r["angle"],
                       "why": r["why"], "audience": r["audience"],
                       "replies": len(json.loads(r["reply_ids"]))} for r in rows],
            **status(ctx)}


def _idea(ctx: Ctx, idea_id: int) -> dict[str, Any]:
    row = ctx.replies.one("SELECT * FROM idea WHERE id=?", (idea_id,))
    if row is None or row["status"] != "new":
        raise ReplyError("That idea is not waiting any more. Nothing was changed.")
    return row


def start(ctx: Ctx, idea_id: int, site_id: Any) -> dict[str, Any]:
    """Start an article from an idea. The article holds the idea's title and angle (the
    model's own words) and its id; never a reply."""
    from articles.ways import start_piece  # noqa: PLC0415

    idea = _idea(ctx, idea_id)
    piece = start_piece(ctx, site_id=site_id, way="idea", topic=idea["title"],
                        angle=idea["angle"], origin=f"idea:{idea_id}")
    ctx.replies.db.execute("UPDATE idea SET status='started', piece_id=?, decided=? WHERE id=?",
                           (piece["id"], ctx.now(), idea_id))
    ctx.replies.db.commit()
    return piece


def dismiss(ctx: Ctx, idea_id: int) -> dict[str, Any]:
    """Dismiss an idea. Its title stays, so it is never proposed again."""
    _idea(ctx, idea_id)
    ctx.replies.db.execute("UPDATE idea SET status='dismissed', decided=? WHERE id=?",
                           (ctx.now(), idea_id))
    ctx.replies.db.commit()
    ctx.emit()
    return ideas(ctx)
