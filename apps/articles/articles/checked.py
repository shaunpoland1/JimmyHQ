"""Revisions and their checks: every version of an article is kept, sanitised, and checked
by the code, whoever wrote it.

``new_revision`` is the only way article text is stored: the sanitiser runs first
(``markup.sanitise``), so no revision ever holds a tag outside the allowed set or a link
that is not a source. ``run`` runs every check on a revision and keeps what they said.
An answer the owner gave to a question ("yes" or "no") stays with the same question in
later revisions; "fixed" does not, so a question that comes back after "fixed" is asked
again.
"""

from __future__ import annotations

import re
from typing import Any

from articles import checks, credits, lessons, markup
from articles.checks import Draft, Material
from articles.context import Ctx

KEEP_ANSWERS = ("yes", "no")
TITLE_MOST = 200


class AnswerError(ValueError):
    """The answer cannot be kept. The message says what to do."""


def sources_by_number(ctx: Ctx, piece_id: int) -> dict[int, dict[str, Any]]:
    return {s["n"]: s for s in ctx.store.sources(piece_id)}


def clean_title(text: Any) -> str:
    title = re.sub(r"<[^>]*>", "", str(text or ""))
    title, _ = markup.remove_addresses(markup.strip_marks(title))
    return " ".join(title.split())[:TITLE_MOST]


def new_revision(ctx: Ctx, piece: dict[str, Any], *, title: Any, body: Any, kind: str,
                 kept: bool | None = True, notes: list[str] | None = None) -> dict[str, Any]:
    """Store one version of the article, sanitised. ``kept`` None is a repair waiting to
    be compared; True makes it the article's current version."""
    most = int(ctx.cfg().article_max_chars)
    raw = str(body or "")
    aside = list(notes or [])
    if len(raw) > most:
        raw = raw[:most]
        aside.append(f"the text past {most} characters")
    clean = markup.sanitise(raw, set(sources_by_number(ctx, piece["id"])))
    aside += clean.removed
    n = ctx.store.next_revision(piece["id"])
    revision_id = ctx.store.insert("revision", {
        "piece_id": piece["id"], "n": n, "kind": kind, "title": clean_title(title) or
        piece["topic"][:TITLE_MOST], "body": clean.html, "aside": aside,
        "kept": None if kept is None else int(kept), "created": ctx.now()})
    if kept:
        ctx.store.update("piece", piece["id"], {"revision": n})
    return ctx.store.one("SELECT * FROM revision WHERE id=?", (revision_id,)) or {}


def material(ctx: Ctx, revision: dict[str, Any]) -> Material:
    """What the checks look at besides the article. When an agent wrote or checked it,
    the same-model-role warning does not apply: the writer and the checker are told apart
    by the agent's own identity instead (``tools.not_own_article``)."""
    cfg = ctx.cfg()
    piece = ctx.store.piece(revision["piece_id"]) or {}
    roles = ("", "") if credits.any_agent(ctx.store, revision["piece_id"]) else (
        str(cfg.role_write), str(cfg.role_review))
    flagged = lessons.flagged_phrases(ctx.store, piece.get("site_id") or 0,
                                      int(cfg.flagged_phrases))
    return Material(sources=sources_by_number(ctx, revision["piece_id"]),
                    review=revision["review"], review_aside=int(revision["review_aside"]),
                    roles=roles, flagged=tuple(flagged))


def run(ctx: Ctx, revision_id: int) -> list[dict[str, Any]]:
    """Every check on one revision, kept, with the owner's earlier answers carried over."""
    store = ctx.store
    revision = store.one("SELECT * FROM revision WHERE id=?", (revision_id,))
    if revision is None:
        raise AnswerError("There is no such version of the article.")
    draft = Draft(revision["title"], revision["body"], list(revision["aside"]))
    results = checks.run_all(draft, material(ctx, revision), ctx.cfg())
    earlier = {(r["name"], r["key"]): r for r in store.all(
        "SELECT c.* FROM check_result c JOIN revision v ON v.id=c.revision_id WHERE "
        "v.piece_id=? AND v.n<? AND c.answer IS NOT NULL ORDER BY v.n",
        (revision["piece_id"], revision["n"]))}
    store.conn.execute("DELETE FROM check_result WHERE revision_id=?", (revision_id,))
    for result in results:
        before = earlier.get((result.name, result.key))
        answer = before["answer"] if before and before["answer"] in KEEP_ANSWERS \
            and result.level == "ask" else None
        store.insert("check_result", {
            "revision_id": revision_id, "name": result.name, "key": result.key,
            "level": result.level, "detail": result.detail, "evidence": list(result.evidence),
            "answer": answer, "answered": before["answered"] if answer else None})
    kept = store.checks(revision_id)
    store.update("revision", revision_id, {"open_count": checks.open_count(kept)})
    return kept


def answer(ctx: Ctx, check_id: int, given: str) -> dict[str, Any]:
    """The owner's answer to a question: yes, no or fixed. It is kept with the check."""
    if given not in checks.ANSWERS:
        raise AnswerError("Answer a question with yes, no or fixed. Nothing was kept.")
    row = ctx.store.one("SELECT * FROM check_result WHERE id=?", (check_id,))
    if row is None or row["level"] != "ask":
        raise AnswerError("That is not a question on an article. Nothing was kept.")
    ctx.store.update("check_result", check_id, {"answer": given, "answered": ctx.now()})
    ctx.store.update("revision", row["revision_id"], {
        "open_count": checks.open_count(ctx.store.checks(row["revision_id"]))})
    return ctx.store.one("SELECT * FROM check_result WHERE id=?", (check_id,)) or {}


def summary(results: list[dict[str, Any]]) -> str:
    """\"2 questions open, 1 warning.\""""
    asks = checks.open_count(results)
    warns = sum(1 for r in results if r["level"] == "warn")
    parts = [f"{asks} question{'s' if asks != 1 else ''} open" if asks else "",
             f"{warns} warning{'s' if warns != 1 else ''}" if warns else ""]
    said = ", ".join(p for p in parts if p)
    return f"{said[0].upper()}{said[1:]}." if said else "Every check passed."
