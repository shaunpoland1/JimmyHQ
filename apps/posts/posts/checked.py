"""Running the checks on a post and keeping what they said, revision by revision.

``run(ctx, post_id)`` is called on every revision and every edit, whoever made it. The
overlap is measured again on the new text first. An answer the owner gave to a question
("yes" or "no") stays with the same question on the next revision; "fixed" does not,
so a question that comes back after "fixed" is asked again.
"""

from __future__ import annotations

from typing import Any

from posts import checks, credits, pictures, similar
from posts.checks import Draft, Material, Result
from posts.context import Ctx

KEEP_ANSWERS = ("yes", "no")


class AnswerError(ValueError):
    """The answer cannot be kept. The message says what to do."""


def model_roles(ctx: Ctx, piece: dict[str, Any]) -> tuple[str, str]:
    """The plain-mode model roles that wrote and checked this piece, for the warning when
    they are the same. A step an agent did has no model role here (agents are kept apart
    by their roles instead)."""
    cfg = ctx.cfg()
    wrote = credits.latest(ctx.store, piece["id"], "writing") if piece else None
    checked_by = credits.latest(ctx.store, piece["id"], "checking") if piece else None
    plain_writer = cfg.role_caption if piece and piece["way"] == "pictures" else cfg.role_draft
    writer = "" if wrote and wrote["by"] == "agent" else str(plain_writer)
    checker = "" if checked_by and checked_by["by"] == "agent" else str(cfg.role_check)
    return writer, checker


def material(ctx: Ctx, post: dict[str, Any], piece: dict[str, Any], *,
             checker_points: list[dict[str, Any]] | None = None,
             overlap: dict[str, Any] | None = None, overlap_text: str = "") -> Material:
    store, cfg = ctx.store, ctx.cfg()
    page = store.page(post["page_id"]) or {}
    lessons = store.lessons(post["page_id"], limit=200)
    recent = store.all("SELECT id, body FROM post WHERE id != ? ORDER BY id DESC LIMIT ?",
                       (post["id"], int(cfg.recycled_drafts)))
    return Material(
        sentences=list(piece["sentences"]),
        notes="\n".join([piece["notes"], *pictures.owner_notes(ctx, piece)]).strip(),
        source_url=piece["source_url"], way=piece["way"], rules=store.rules(post["page_id"]),
        page_tags=list(page.get("tags") or []),
        send_back_quotes=[q for lesson in lessons if lesson["kind"] == "send_back"
                          for q in lesson["quotes"]],
        recent=[(r["id"], r["body"]) for r in recent], overlap=overlap,
        overlap_text=overlap_text, checker_points=checker_points,
        roles=model_roles(ctx, piece),
        pictures=pictures.likeness(ctx, piece) if piece["way"] == "pictures" else [],
        like_days=int(cfg.dup_days))


def draft_of(post: dict[str, Any], piece: dict[str, Any]) -> Draft:
    return Draft(title=post["title"], body=post["body"], claims=list(post["claims"]),
                 links=list(post["links"]), sponsored=bool(piece["sponsored"]))


def run(ctx: Ctx, post_id: int, *, checker_points: list[dict[str, Any]] | None = None,
        local: bool = False) -> list[dict[str, Any]]:
    """Every check on the post's current revision. ``local=True`` asks no model (the
    overlap is measured by shared words), for an agent's ``record`` tool; the job runs
    them all again with embeddings before the draft goes on."""
    store = ctx.store
    post = store.post(post_id)
    if post is None:
        raise AnswerError("There is no such post.")
    piece = store.piece(post["piece_id"]) or {}
    overlap = similar.compare(ctx.app, store, ctx.cfg(), page_id=post["page_id"],
                              text=similar.opening(post["title"], post["body"]),
                              now_ms=ctx.now(), skip_piece=post["piece_id"],
                              use_embeddings=not local)
    if checker_points is None:  # keep the checker's word on an edit of the same draft
        earlier = store.get_meta(f"checker.{post_id}")
        checker_points = earlier if isinstance(earlier, list) else None
    else:
        store.set_meta(f"checker.{post_id}", checker_points)
    given = material(ctx, post, piece, checker_points=checker_points,
                     overlap=overlap.to_dict(), overlap_text=similar.describe(overlap, ctx.now()))
    results = checks.run_all(draft_of(post, piece), given, ctx.cfg())
    return keep(ctx, post, results)


def keep(ctx: Ctx, post: dict[str, Any], results: list[Result]) -> list[dict[str, Any]]:
    store = ctx.store
    earlier = {(r["name"], r["key"]): r for r in store.all(
        "SELECT * FROM check_result WHERE post_id=? AND revision<? AND answer IS NOT NULL "
        "ORDER BY revision", (post["id"], post["revision"]))}
    store.conn.execute("DELETE FROM check_result WHERE post_id=? AND revision=?",
                       (post["id"], post["revision"]))
    for result in results:
        before = earlier.get((result.name, result.key))
        answer = before["answer"] if before and before["answer"] in KEEP_ANSWERS \
            and result.level == "ask" else None
        store.insert("check_result", {
            "post_id": post["id"], "revision": post["revision"], "name": result.name,
            "key": result.key, "level": result.level, "detail": result.detail,
            "evidence": list(result.evidence), "answer": answer,
            "answered": before["answered"] if answer else None})
    return store.checks(post["id"], post["revision"])


def answer(ctx: Ctx, check_id: int, given: str) -> dict[str, Any]:
    """The owner's answer to a question: yes, no or fixed. It is kept with the check."""
    if given not in checks.ANSWERS:
        raise AnswerError("Answer a question with yes, no or fixed. Nothing was kept.")
    store = ctx.store
    row = store.one("SELECT * FROM check_result WHERE id=?", (check_id,))
    if row is None or row["level"] != "ask":
        raise AnswerError("That is not a question on a draft. Nothing was kept.")
    store.update("check_result", check_id, {"answer": given, "answered": ctx.now()})
    return store.one("SELECT * FROM check_result WHERE id=?", (check_id,)) or {}


def summary(results: list[dict[str, Any]]) -> str:
    """\"2 questions open, 1 warning.\""""
    asks = checks.open_questions(results)
    warns = sum(1 for r in results if r["level"] == "warn")
    parts = [f"{asks} question{'s' if asks != 1 else ''} open" if asks else "",
             f"{warns} warning{'s' if warns != 1 else ''}" if warns else ""]
    said = ", ".join(p for p in parts if p)
    return f"{said[0].upper()}{said[1:]}." if said else "Every check passed."
