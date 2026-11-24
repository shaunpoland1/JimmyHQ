"""Writing, review and repair in plain mode, one step per run of the ``advance`` job.

    outlining    an outline the owner can see
    drafting     the article, from the numbered sources; the code checks run on it
    reviewing    one review call on ``role_review``, held to its shape; then the code
                 decides: repair, or on to the owner
    repairing    the writer repairs what is open; the candidate is checked by the code
    rechecking   the candidate is reviewed; it is kept only if the open findings strictly
                 drop, and otherwise thrown away (rule ``a repair must reduce``)
    revising     after the owner sent it back: a new version from the owner's note

How many repair passes run is ``repair_passes``, counted by the code.
"""

from __future__ import annotations

import difflib
from typing import Any

from articles import cards, checked, checks, prompts, render, review, sources
from articles.context import Ctx
from articles.steps import StepError, data_of, made_by, turn

CHANGE_WORDS = 12
CHANGES_KEPT = 6
OPENING_CHARS = 160


# --- what the writer is given ---------------------------------------------------------------


def lessons(ctx: Ctx, site_id: int) -> list[str]:
    most = int(ctx.cfg().lessons_shown)
    if most <= 0:
        return []
    out = []
    for lesson in ctx.store.lessons(site_id, limit=most):
        if lesson["kind"] == "decline":
            out.append(f"Declined ({lesson['reason'] or 'no reason given'}). {lesson['note']}")
        elif lesson["kind"] == "send_back":
            out.append(f"Sent back: {lesson['note']}")
        else:
            out.append(f"Edited before approving: {lesson['note']}")
    return [line.strip()[:400] for line in out]


def openings(ctx: Ctx, site_id: int, skip: int) -> list[str]:
    most = int(ctx.cfg().openings_shown)
    if most <= 0:
        return []
    out = []
    for piece in ctx.store.all("SELECT id FROM piece WHERE site_id=? AND id != ? AND revision "
                               "> 0 ORDER BY id DESC LIMIT ?", (site_id, skip, most)):
        revision = ctx.store.revision(piece["id"])
        text = render.plain_text(revision["body"]) if revision else ""
        if text:
            out.append(text.split("\n", 1)[0][:OPENING_CHARS])
    return out


def _ask(ctx: Ctx, piece: dict[str, Any], *, role: str, prompt: str, schema: dict[str, Any],
         untrusted: list[str], what: str) -> tuple[dict[str, Any], Any]:
    site = ctx.store.site(piece["site_id"]) or {}
    system = prompts.voice(site, lessons(ctx, piece["site_id"]),
                           openings(ctx, piece["site_id"], piece["id"]))
    reply = ctx.app.model.ask(prompt, role=role, system=system, json_schema=schema,
                              untrusted=untrusted or None)
    return data_of(reply, what), reply


def material(ctx: Ctx, piece: dict[str, Any]) -> list[str]:
    """The untrusted text writing rests on: the topic when it came from readers, the
    research summary, the outline, then the numbered sources."""
    cfg = ctx.cfg()
    out = [*prompts.topic_block(piece)]
    if piece["summary"]:
        out.append(prompts.summary_block(piece["summary"]))
    if piece["outline"]:
        out.append(prompts.outline_block(piece["outline"]))
    out += sources.blocks(ctx.store.sources(piece["id"]), int(cfg.material_chars))
    return out


# --- the steps ------------------------------------------------------------------------------


def take_outline(data: dict[str, Any]) -> dict[str, Any]:
    sections = []
    for section in data.get("sections") or []:
        if not isinstance(section, dict) or not isinstance(section.get("heading"), str):
            continue
        points = [checked.clean_title(p)[:300] for p in section.get("points") or []
                  if isinstance(p, str) and p.strip()]
        sections.append({"heading": checked.clean_title(section["heading"]), "points": points})
    title = checked.clean_title(data.get("title"))
    if not title or not sections:
        raise StepError("The outline came back without a title or sections, so it was not "
                        "kept and nothing was written.")
    return {"title": title, "sections": sections}


def outline_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    site = ctx.store.site(piece["site_id"]) or {}
    data, reply = _ask(ctx, piece, role=str(cfg.role_write),
                       prompt=prompts.outline_prompt(piece, site),
                       schema=prompts.OUTLINE_SCHEMA, untrusted=material(ctx, piece),
                       what="outline")
    outline = take_outline(data)
    ctx.store.move(piece["id"], "drafting", outline=outline)
    return turn(ctx, piece["id"], "outlining", f"Outlined “{outline['title']}” in "
                f"{len(outline['sections'])} sections.", role=str(cfg.role_write),
                **made_by(reply), data={"outline": outline})


def _kept_line(revision: dict[str, Any], results: list[dict[str, Any]]) -> str:
    aside = f" Taken out: {'; '.join(revision['aside'])}." if revision["aside"] else ""
    return f"Code checks: {checked.summary(results)}{aside}"


def draft_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    site = ctx.store.site(piece["site_id"]) or {}
    data, reply = _ask(ctx, piece, role=str(cfg.role_write),
                       prompt=prompts.article_prompt(piece, site),
                       schema=prompts.ARTICLE_SCHEMA, untrusted=material(ctx, piece),
                       what="article")
    if not str(data.get("body") or "").strip():
        raise StepError("The model gave back an empty article, so nothing was kept.")
    revision = checked.new_revision(ctx, piece, title=data.get("title"), body=data["body"],
                                    kind="draft")
    results = checked.run(ctx, revision["id"])
    ctx.store.move(piece["id"], "reviewing")
    gloss = str(data.get("gloss") or "Wrote the article.")[:200]
    return turn(ctx, piece["id"], "drafting", f"{gloss} {_kept_line(revision, results)}",
                role=str(cfg.role_write), **made_by(reply))


def ask_review(ctx: Ctx, piece: dict[str, Any], revision: dict[str, Any]
               ) -> tuple[list[dict[str, Any]], int, Any]:
    """The review of one revision, held to its shape. ``StepError`` when it is not."""
    cfg = ctx.cfg()
    most = int(cfg.review_findings_max)
    kept_sources = ctx.store.sources(piece["id"])
    reply = ctx.app.model.ask(
        prompts.review_prompt(most), role=str(cfg.role_review),
        json_schema=review.REVIEW_SCHEMA,
        untrusted=[*sources.blocks(kept_sources, int(cfg.material_chars)),
                   prompts.article_block(revision)])
    try:
        findings, aside = review.read_review(review.answer_data(reply), revision,
                                             kept_sources, most)
    except review.ReviewShapeError as error:
        raise StepError(f"The review's answer was not in the shape Articles asked for ({error}), "
                        "so it was not read as finding nothing. Nothing was kept from it.") \
            from None
    ctx.store.update("revision", revision["id"], {"review": findings, "review_aside": aside})
    return findings, aside, reply


def _to_owner(ctx: Ctx, piece: dict[str, Any], why: str) -> str:
    ctx.store.move(piece["id"], "ready")
    cards.file_article(ctx, piece["id"])
    return why


def review_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    revision = ctx.store.revision(piece["id"])
    if revision is None:
        raise StepError("This article has no text to review.")
    findings, aside, reply = ask_review(ctx, piece, revision)
    return after_review(ctx, piece, revision, findings, aside,
                        {"role": str(cfg.role_review), **made_by(reply)})


def after_review(ctx: Ctx, piece: dict[str, Any], revision: dict[str, Any],
                 findings: list[dict[str, Any]], aside: int, role: dict[str, Any],
                 by: str = "Reviewed") -> str:
    """The code, never the reviewer, decides: a repair while questions are open and
    passes are left (``repair_passes``), otherwise on to the owner. The same whoever
    reviewed it."""
    cfg = ctx.cfg()
    results = checked.run(ctx, revision["id"])
    left = checks.open_count(results)
    said = (f"{by}: {len(findings)} finding{'s' if len(findings) != 1 else ''}"
            f"{f', {aside} set aside' if aside else ''}. {checked.summary(results)}")
    if left and piece["passes"] < int(cfg.repair_passes):
        ctx.store.move(piece["id"], "repairing")
        return turn(ctx, piece["id"], "reviewing", f"{said} A repair comes next.", **role)
    stop = " The code stopped the repairs here." if left else ""
    return turn(ctx, piece["id"], "reviewing", _to_owner(ctx, piece, f"{said}{stop} On your "
                "Bench."), **role)


def repair_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    site = ctx.store.site(piece["site_id"]) or {}
    revision = ctx.store.revision(piece["id"])
    if revision is None:
        raise StepError("This article has no text to repair.")
    results = ctx.store.checks(revision["id"])
    data, reply = _ask(ctx, piece, role=str(cfg.role_write),
                       prompt=prompts.repair_prompt(piece, site), schema=prompts.ARTICLE_SCHEMA,
                       untrusted=[*sources.blocks(ctx.store.sources(piece["id"]),
                                                  int(cfg.material_chars)),
                                  prompts.article_block(revision),
                                  prompts.findings_block(results)], what="repair")
    if not str(data.get("body") or "").strip():
        raise StepError("The repair came back empty, so nothing was kept.")
    candidate = checked.new_revision(ctx, piece, title=data.get("title"), body=data["body"],
                                     kind="repair", kept=None)
    checked.run(ctx, candidate["id"])
    ctx.store.move(piece["id"], "rechecking", passes=piece["passes"] + 1)
    gloss = str(data.get("gloss") or "Repaired the article.")[:200]
    return turn(ctx, piece["id"], "repairing", f"Repair pass {piece['passes'] + 1}: {gloss} "
                "Its review comes next.", role=str(cfg.role_write), **made_by(reply))


def changes(before: str, after: str, most: int = CHANGES_KEPT) -> list[str]:
    """What changed between two versions, in plain words."""
    old, new = before.split(), after.split()

    def cut(words: list[str]) -> str:
        return " ".join(words[:CHANGE_WORDS]) + (" …" if len(words) > CHANGE_WORDS else "")
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(a=old, b=new,
                                                      autojunk=False).get_opcodes():
        if op == "replace":
            out.append(f"changed “{cut(old[a1:a2])}” to “{cut(new[b1:b2])}”")
        elif op == "delete":
            out.append(f"took out “{cut(old[a1:a2])}”")
        elif op == "insert":
            out.append(f"added “{cut(new[b1:b2])}”")
    return out[:most]


def strictly_fewer(candidate_open: int, current_open: int) -> bool:
    """Rule ``a repair must reduce``: a repair is kept only when it leaves strictly fewer
    open findings than the version it repairs."""
    return candidate_open < current_open


def candidate_of(ctx: Ctx, piece_id: int) -> dict[str, Any] | None:
    """The repair waiting to be compared, if there is one."""
    return ctx.store.one("SELECT * FROM revision WHERE piece_id=? AND kept IS NULL "
                         "ORDER BY n DESC LIMIT 1", (piece_id,))


def recheck_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    current = ctx.store.revision(piece["id"])
    candidate = candidate_of(ctx, piece["id"])
    if current is None or candidate is None:
        raise StepError("There is no repair waiting to be checked.")
    _findings, _aside, reply = ask_review(ctx, piece, candidate)
    return after_recheck(ctx, piece, current, candidate,
                         {"role": str(cfg.role_review), **made_by(reply)})


def after_recheck(ctx: Ctx, piece: dict[str, Any], current: dict[str, Any],
                  candidate: dict[str, Any], role: dict[str, Any]) -> str:
    """Rule ``a repair must reduce``, decided by the code whoever reviewed the repair."""
    cfg = ctx.cfg()
    results = checked.run(ctx, candidate["id"])
    now_open = checks.open_count(results)
    was_open = int(current["open_count"] or 0)
    if strictly_fewer(now_open, was_open):
        said = changes(render.plain_text(current["body"]), render.plain_text(candidate["body"]))
        ctx.store.update("revision", candidate["id"], {"kept": 1, "changes": said})
        ctx.store.update("piece", piece["id"], {"revision": candidate["n"]})
        line = (f"Repair pass {piece['passes']} kept: open findings went from {was_open} to "
                f"{now_open}. It {'; '.join(said) or 'changed only its marks'}.")
        if now_open and piece["passes"] < int(cfg.repair_passes):
            ctx.store.move(piece["id"], "repairing")
            return turn(ctx, piece["id"], "rechecking", f"{line} Another pass comes next.",
                        **role)
        return turn(ctx, piece["id"], "rechecking", _to_owner(ctx, piece, f"{line} On your "
                    "Bench."), **role)
    ctx.store.update("revision", candidate["id"], {"kept": 0})
    line = (f"Repair pass {piece['passes']} thrown away: it left {now_open} open finding"
            f"{'s' if now_open != 1 else ''}, and the version before had {was_open}. The "
            "earlier version stands.")
    return turn(ctx, piece["id"], "rechecking", _to_owner(ctx, piece, f"{line} On your "
                "Bench."), **role)


def revise_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    site = ctx.store.site(piece["site_id"]) or {}
    revision = ctx.store.revision(piece["id"])
    if revision is None:
        raise StepError("This article has no text to revise.")
    data, reply = _ask(ctx, piece, role=str(cfg.role_write),
                       prompt=prompts.revise_prompt(piece, site, piece["owner_note"]),
                       schema=prompts.ARTICLE_SCHEMA,
                       untrusted=[*sources.blocks(ctx.store.sources(piece["id"]),
                                                  int(cfg.material_chars)),
                                  prompts.article_block(revision)], what="revision")
    if not str(data.get("body") or "").strip():
        raise StepError("The revision came back empty, so nothing was kept.")
    new = checked.new_revision(ctx, piece, title=data.get("title"), body=data["body"],
                               kind="owner_note")
    ctx.store.update("revision", new["id"], {"changes": changes(
        render.plain_text(revision["body"]), render.plain_text(new["body"]))})
    results = checked.run(ctx, new["id"])
    ctx.store.move(piece["id"], "reviewing", passes=0, owner_note="")
    gloss = str(data.get("gloss") or "Revised the article from your note.")[:200]
    return turn(ctx, piece["id"], "revising", f"{gloss} {_kept_line(new, results)}",
                role=str(cfg.role_write), **made_by(reply))
