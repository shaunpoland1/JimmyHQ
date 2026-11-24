"""Plain mode: drafting with no agents, one step per run of the ``advance`` job.

Each step has a deadline (``step_timeout_minutes``) and writes one human line (a turn's
gloss) saying what it did. The steps, as ``states.PIECE`` allows them:

    reading        read the source and number its sentences (no model)
    analysing      pictures only: a model that can see looks at each one (``pictures``)
    overlap_check  "have we covered this lately?" (no drafting model; held with a card)
    writing        one draft call on ``role_draft``; the code checks run on the result
    captioning     the same for pictures, on ``role_caption``, from what was seen
    drafted        the code checks run (when a step stopped between the two)
    in_check       one check call on ``role_check``; the code decides whether to revise
    revising       one revise call, at most ``max_rounds`` times, then checked again

A step that cannot finish (a model refuses, a deadline passes) leaves the piece
``stalled`` with a sentence; nothing is tried again until the owner presses Retry.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from jimmyhq_app import CallError

from posts import booking, cards, checked, pictures, prompts, similar, sources, states
from posts.checks.review import usable_points
from posts.checks.text import remove_addresses
from posts.context import Ctx

MINUTE_MS = 60_000
HOUR_MS = 3_600_000


class StepError(Exception):
    """A step could not finish. ``str()`` is the sentence for the owner."""


# --- the job --------------------------------------------------------------------------------


def advance(ctx: Ctx) -> str:
    """One run of the job: tidy what timed out, then carry one piece one step on.
    Returns the line it wrote, for the job's log."""
    expire(ctx)
    pictures.tidy(ctx)
    booking.mark_unsure(ctx)
    cards.nudge(ctx)
    marks = ",".join("?" * len(states.PIECE_WORK))
    for piece in ctx.store.all(f"SELECT * FROM piece WHERE state IN ({marks}) AND "  # noqa: S608
                               "step_started IS NULL ORDER BY updated, id",
                               states.PIECE_WORK):
        said = step(ctx, piece["id"])
        if said is not None:
            return said
    return "Nothing to carry on."


def expire(ctx: Ctx) -> int:
    """A step still marked as running after its deadline stopped without finishing."""
    count = 0
    for piece in ctx.store.all("SELECT * FROM piece WHERE step_started IS NOT NULL AND "
                               "deadline_ms < ?", (ctx.now(),)):
        minutes = int(ctx.cfg().step_timeout_minutes)
        said = (f"The {states.WORDS.get(piece['state'], piece['state'])} step did not finish "
                f"within {minutes} minutes. Press Retry to try it again, or drop the piece.")
        stall(ctx, piece, said)
        count += 1
    return count


def stall(ctx: Ctx, piece: dict[str, Any], sentence: str) -> None:
    ctx.store.add_turn(piece["id"], piece["state"], sentence)
    ctx.store.move_piece(piece["id"], "stalled", error=sentence, step_started=None,
                         deadline_ms=None, owner_note=piece["owner_note"],
                         came_from=piece["came_from"] or piece["state"])
    ctx.emit("posts.changed")


def retry(ctx: Ctx, piece_id: int) -> dict[str, Any]:
    """The owner pressed Retry on a stuck piece, or on a source that could not be read."""
    piece = ctx.store.piece(piece_id)
    if piece is None:
        raise StepError("There is no such piece.")
    if piece["state"] == "stopped_source":
        back = "reading"
    elif piece["state"] == "stalled":
        back = piece["came_from"] or "reading"
    else:
        raise StepError("Only a stuck piece can be retried. Nothing was changed.")
    ctx.store.add_turn(piece_id, back, "You pressed Retry.", role="owner")
    return ctx.store.move_piece(piece_id, back, error="", came_from="")


STEPS: dict[str, Any] = {}


def step(ctx: Ctx, piece_id: int) -> str | None:
    """Carry one piece one step. None when the piece had to wait (its day's drafts are
    spent, or an agent has the work), so another may go instead.

    ``work.route`` says first whether an agent takes this step; only when it answers
    ``work.PLAIN`` (the role is empty, or the owner chose to finish without agents) is
    the plain step run, and the piece then records that the plain way did it."""
    from posts import work  # noqa: PLC0415 - work builds on this module

    piece = ctx.store.piece(piece_id)
    if piece is None or piece["state"] not in STEPS:
        return None
    timeout = int(ctx.cfg().step_timeout_minutes) * MINUTE_MS
    ctx.store.update("piece", piece_id, {"step_started": ctx.now(),
                                         "deadline_ms": ctx.now() + timeout})
    piece = ctx.store.piece(piece_id) or piece
    try:
        routed = work.route(ctx, piece)
        if routed is work.PLAIN:
            said = STEPS[piece["state"]](ctx, piece)
            if said is not None:
                work.after_plain(ctx, piece)
        else:
            said = routed
    except (StepError, CallError) as error:
        sentence = error.message if isinstance(error, CallError) else str(error)
        current = ctx.store.piece(piece_id) or piece
        stall(ctx, {**current, "came_from": current["state"]},
              f"{sentence} Press Retry to try again, or drop the piece.")
        return sentence
    finally:
        ctx.store.update("piece", piece_id, {"step_started": None, "deadline_ms": None})
    ctx.emit("posts.changed")
    return said


def _turn(ctx: Ctx, piece_id: int, step_name: str, gloss: str, **more: Any) -> str:
    ctx.store.add_turn(piece_id, step_name, gloss, **more)
    return gloss


# --- reading and overlap: no drafting model -------------------------------------------------


def read_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    if not piece["source_url"]:
        ctx.store.move_piece(piece["id"], "overlap_check")
        return _turn(ctx, piece["id"], "reading", "No article to read: this piece is drafted "
                     "from your topic and notes.")
    cfg = ctx.cfg()
    try:
        found = sources.read(ctx.app, piece["source_url"], min_chars=int(cfg.min_source_chars),
                             keep_chars=int(cfg.source_keep_chars))
    except sources.SourceProblem as problem:
        ctx.store.move_piece(piece["id"], "stopped_source", error=str(problem))
        return _turn(ctx, piece["id"], "reading", str(problem))
    ctx.store.move_piece(piece["id"], "overlap_check", source_text=found.text,
                         sentences=found.sentences, source_title=found.title[:300])
    return _turn(ctx, piece["id"], "reading", f"Read the article: {len(found.text)} characters "
                 f"in {len(found.sentences)} numbered sentences.")


def overlap_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    overlap = similar.compare(ctx.app, ctx.store, ctx.cfg(), page_id=piece["page_id"],
                              text=similar.piece_text(piece), now_ms=ctx.now(),
                              skip_piece=piece["id"])
    said = similar.describe(overlap, ctx.now())
    note = f" {overlap.note}" if overlap.note else ""
    if overlap.tier != "none":
        ctx.store.move_piece(piece["id"], "held_overlap", overlap=overlap.to_dict())
        cards.file_overlap(ctx, piece["id"])
        return _turn(ctx, piece["id"], "overlap_check", f"{said} Held for you before anything "
                     f"is spent on it.{note}")
    nxt = "captioning" if piece["way"] == "pictures" else "writing"
    ctx.store.move_piece(piece["id"], nxt, overlap=overlap.to_dict())
    return _turn(ctx, piece["id"], "overlap_check", f"{said} Drafting comes next.{note}")


# --- the model steps ------------------------------------------------------------------------


def _data(reply: Any, what: str) -> dict[str, Any]:
    data = reply.data
    if data is None:
        try:
            data = json.loads(reply.text)
        except (TypeError, ValueError):
            data = None
    if not isinstance(data, dict):
        raise StepError(f"The model's {what} could not be read as the shape Posts asked for.")
    return data


def _made_by(reply: Any) -> dict[str, Any]:
    """Which model answered, and whether it ran on the owner's own machines."""
    said = dataclasses.asdict(reply)
    return {"model": said.get("model") or "", "local": said.get("local")}


def _openings(ctx: Ctx, page_id: int) -> list[str]:
    rows = ctx.store.all("SELECT body FROM post WHERE page_id=? ORDER BY id DESC LIMIT ?",
                         (page_id, prompts.MAX_OPENINGS))
    return [r["body"].strip().split("\n", 1)[0][:160] for r in rows if r["body"].strip()]


def articles(piece: dict[str, Any]) -> dict[int, str]:
    """The articles a draft may link to, by number: only ones Posts actually read."""
    return {1: piece["source_url"]} if piece["source_url"] and piece["sentences"] else {}


def take_draft(piece: dict[str, Any], data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """What a model wrote, made safe to keep: addresses it wrote are taken out, links are
    the addresses of the articles it named by number, claims keep only real sentence
    numbers. Returns the draft and a line on what was set aside."""
    body, removed = remove_addresses(str(data.get("body") or ""))
    title, removed_title = remove_addresses(str(data.get("title") or ""))
    removed += removed_title
    if not body:
        raise StepError("The model gave back an empty draft.")
    known = articles(piece)
    asked = [n for n in data.get("links") or [] if isinstance(n, int)]
    links = list(dict.fromkeys(known[n] for n in asked if n in known))
    count = len(piece["sentences"])
    claims = []
    for claim in data.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        cited = [n for n in claim.get("sentences") or [] if isinstance(n, int)
                 and 1 <= n <= count]
        claims.append({"text": str(claim.get("text") or "")[:500], "sentences": cited})
    notes = []
    if removed:
        notes.append(f"took out {len(removed)} address{'es' if len(removed) != 1 else ''} the "
                     "model wrote itself")
    if len(asked) != len([n for n in asked if n in known]):
        notes.append("set aside links to articles Posts did not read")
    return ({"title": title[:200], "body": body[:5000], "claims": claims, "links": links},
            "; ".join(notes))


def _ask(ctx: Ctx, piece: dict[str, Any], *, role: str, prompt: str, schema: dict[str, Any],
         untrusted: list[str], what: str) -> tuple[dict[str, Any], Any]:
    page = ctx.store.page(piece["page_id"]) or {}
    system = prompts.voice(page, ctx.store.lessons(piece["page_id"]),
                           _openings(ctx, piece["page_id"]))
    reply = ctx.app.model.ask(prompt, role=role, system=system, json_schema=schema,
                              untrusted=untrusted or None)
    return _data(reply, what), reply


def _keep_post(ctx: Ctx, piece: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    post = store.post_of(piece["id"])
    if post is None:
        post_id = store.insert("post", {"piece_id": piece["id"], "page_id": piece["page_id"],
                                        "state": "draft", "revision": 1, "created": ctx.now(),
                                        "updated": ctx.now(), **draft})
        return store.post(post_id) or {}
    new_state = "draft" if post["state"] in ("draft", "revising") else post["state"]
    return store.move_post(post["id"], new_state, revision=post["revision"] + 1, **draft)


def _given(ctx: Ctx, piece: dict[str, Any]) -> list[str]:
    """The untrusted text a draft rests on: the article's sentences, or what a model saw
    in the owner's pictures."""
    return [*prompts.source_block(piece), *pictures.blocks(ctx, piece)]


def may_start_writing(ctx: Ctx, piece: dict[str, Any]) -> bool:
    """Rule ``pieces_a_day``: a piece starts drafting (or captioning) only while today's
    ``pieces_per_day`` are not spent; it then counts for today. Whoever writes it."""
    today = ctx.day(ctx.now())
    most = int(ctx.cfg().pieces_per_day)
    if not piece["writing_day"] and most > 0:
        started = ctx.store.one("SELECT COUNT(*) AS n FROM piece WHERE writing_day=?",
                                (today,))["n"]
        if started >= most:
            if ctx.store.get_meta(f"waiting.{piece['id']}") != today:
                ctx.store.set_meta(f"waiting.{piece['id']}", today)
                _turn(ctx, piece["id"], piece["state"], f"Today's {most} drafts are spent, "
                      "so this piece waits for tomorrow.")
            return False
    ctx.store.update("piece", piece["id"], {"writing_day": piece["writing_day"] or today})
    return True


def write_step(ctx: Ctx, piece: dict[str, Any]) -> str | None:
    """``writing``, and ``captioning`` for pictures: one call, then the code checks."""
    cfg = ctx.cfg()
    if not may_start_writing(ctx, piece):
        return None
    if piece["way"] == "pictures":
        role = str(cfg.role_caption)
        prompt = prompts.caption_prompt(piece, pictures.owner_notes(ctx, piece))
    else:
        role, prompt = str(cfg.role_draft), prompts.draft_prompt(piece)
    data, reply = _ask(ctx, piece, role=role, prompt=prompt, schema=prompts.DRAFT_SCHEMA,
                       untrusted=_given(ctx, piece), what="draft")
    draft, aside = take_draft(piece, data)
    post = _keep_post(ctx, piece, draft)
    pictures.link_post(ctx, piece["id"], post["id"])
    ctx.store.move_piece(piece["id"], "drafted")
    results = checked.run(ctx, post["id"], checker_points=None)
    ctx.store.move_piece(piece["id"], "in_check")
    gloss = str(data.get("gloss") or "Drafted the post.")[:200]
    return _turn(ctx, piece["id"], piece["state"], f"{gloss} Code checks: "
                 f"{checked.summary(results)}{f' Posts {aside}.' if aside else ''}",
                 role=role, **_made_by(reply), data={"draft": draft})


def drafted_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    post = ctx.store.post_of(piece["id"])
    if post is None:
        raise StepError("This piece has no draft to check.")
    results = checked.run(ctx, post["id"])
    ctx.store.move_piece(piece["id"], "in_check")
    return _turn(ctx, piece["id"], "drafted", f"Code checks: {checked.summary(results)}")


def _draft_block(post: dict[str, Any]) -> str:
    return f"Draft:\n{post['title']}\n\n{post['body']}"


def check_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    post = ctx.store.post_of(piece["id"])
    if post is None:
        raise StepError("This piece has no draft to check.")
    data, reply = _ask(ctx, piece, role=str(cfg.role_check), prompt=prompts.check_prompt(),
                       schema=prompts.CHECK_SCHEMA,
                       untrusted=[*_given(ctx, piece), _draft_block(post)], what="check")
    points, dropped = usable_points([p for p in data.get("points") or []
                                     if isinstance(p, dict)][:3], post["body"],
                                    len(piece["sentences"]))
    results = checked.run(ctx, post["id"], checker_points=points)
    revise = data.get("verdict") == "revise" and points and piece["round"] < int(cfg.max_rounds)
    gloss = str(data.get("gloss") or "Checked the draft.")[:200]
    tail = f" {dropped} point{'s' if dropped != 1 else ''} set aside." if dropped else ""
    model = {"role": str(cfg.role_check), **_made_by(reply),
             "data": {"verdict": data.get("verdict"), "points": points}}
    if revise:
        ctx.store.move_piece(piece["id"], "revising")
        return _turn(ctx, piece["id"], "in_check", f"{gloss} {len(points)} point"
                     f"{'s' if len(points) != 1 else ''} to revise.{tail}", **model)
    ctx.store.move_piece(piece["id"], "ready")
    cards.file_draft(ctx, post["id"])
    stop = " The code stopped the revisions here." if data.get("verdict") == "revise" else ""
    return _turn(ctx, piece["id"], "in_check", f"{gloss}{stop} On your Bench: "
                 f"{checked.summary(results)}{tail}", **model)


def revise_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    post = ctx.store.post_of(piece["id"])
    if post is None:
        raise StepError("This piece has no draft to revise.")
    points = ctx.store.get_meta(f"checker.{post['id']}") or []
    if piece["owner_note"]:
        points = []  # the owner's word comes first; the checker looks again afterwards
    untrusted = [*_given(ctx, piece), _draft_block(post)]
    if points:
        untrusted.append(prompts.points_block(points))
    base = (prompts.caption_prompt(piece, pictures.owner_notes(ctx, piece))
            if piece["way"] == "pictures" else None)
    role = str(cfg.role_caption if piece["way"] == "pictures" else cfg.role_draft)
    data, reply = _ask(ctx, piece, role=role,
                       prompt=prompts.revise_prompt(piece, piece["owner_note"], bool(points),
                                                    base=base),
                       schema=prompts.DRAFT_SCHEMA, untrusted=untrusted, what="revision")
    draft, aside = take_draft(piece, data)
    post = _keep_post(ctx, piece, draft)
    ctx.store.move_piece(piece["id"], "drafted", round=piece["round"] + 1, owner_note="")
    results = checked.run(ctx, post["id"], checker_points=[])
    ctx.store.move_piece(piece["id"], "in_check")
    gloss = str(data.get("gloss") or "Revised the draft.")[:200]
    return _turn(ctx, piece["id"], "revising", f"{gloss} Code checks: "
                 f"{checked.summary(results)}{f' Posts {aside}.' if aside else ''}",
                 role=role, **_made_by(reply), data={"draft": draft})


STEPS.update({"reading": read_step, "analysing": pictures.analyse_step,
              "overlap_check": overlap_step, "writing": write_step, "captioning": write_step,
              "drafted": drafted_step, "in_check": check_step, "revising": revise_step})
