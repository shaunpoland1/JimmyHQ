"""Coverage and research in plain mode, one step per run of the ``advance`` job.

    coverage_check  "have we written about this lately?" (no spending; held with a card)
    searching       the topic as a few search phrasings, each searched through the platform
    reading         each page read and numbered, the unreadable and short left out, the rest
                    ranked (``rank``); too few good sources stops the piece before writing
    summarising     a research summary whose every statement cites (source, sentence)

Search results and pages are web text: they reach a model only as the platform's
``untrusted`` argument.
"""

from __future__ import annotations

from typing import Any

from jimmyhq_app import CallError

from articles import cards, coverage, markup, prompts, rank, sources
from articles.context import Ctx
from articles.steps import StepError, cites_of, data_of, made_by, turn

PHRASE_MOST = 200
SUMMARY_TEXT_MOST = 600


# --- coverage ---------------------------------------------------------------------------------


def coverage_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    found = coverage.compare(ctx, piece)
    said = coverage.describe(found, ctx.now())
    note = f" {found['note']}" if found["note"] else ""
    if found["close"]:
        ctx.store.move(piece["id"], "held_coverage", coverage=found)
        cards.file_coverage(ctx, piece["id"])
        return turn(ctx, piece["id"], "coverage_check", f"{said} Held for you before "
                    f"anything is spent on it.{note}")
    ctx.store.move(piece["id"], "searching", coverage=found)
    return turn(ctx, piece["id"], "coverage_check", f"{said} Research comes next.{note}")


# --- searching ------------------------------------------------------------------------------


def may_start(ctx: Ctx, piece: dict[str, Any]) -> bool:
    """Rule ``articles a day``: research starts only while today's ``pieces_per_day`` are
    not spent; the article then counts for today."""
    today = ctx.day(ctx.now())
    most = int(ctx.cfg().pieces_per_day)
    if not piece["writing_day"] and most > 0:
        started = ctx.store.one("SELECT COUNT(*) AS n FROM piece WHERE writing_day=?",
                                (today,))["n"]
        if started >= most:
            if ctx.store.get_meta(f"waiting.{piece['id']}") != today:
                ctx.store.set_meta(f"waiting.{piece['id']}", today)
                turn(ctx, piece["id"], piece["state"], f"Today's {most} articles have "
                     "started, so this one waits for tomorrow.")
            return False
    ctx.store.update("piece", piece["id"], {"writing_day": piece["writing_day"] or today})
    return True


def take_phrasings(data: Any, most: int) -> list[str]:
    """Rule ``phrasings are short searches``: text, no web address, no repeats, at most
    ``most``."""
    out: list[str] = []
    for raw in (data or {}).get("phrasings") or [] if isinstance(data, dict) else []:
        if not isinstance(raw, str):
            continue
        text, found = markup.remove_addresses(" ".join(raw.split()))
        text = text.strip()
        if 3 <= len(text) <= PHRASE_MOST and not found and text.lower() not in \
                {p.lower() for p in out}:
            out.append(text)
        if len(out) >= most:
            break
    return out


def fallback_phrasings(piece: dict[str, Any], most: int) -> list[str]:
    found = [piece["topic"]]
    if piece["angle"]:
        found.append(f"{piece['topic']} {piece['angle']}"[:PHRASE_MOST])
    return found[:most]


def search_step(ctx: Ctx, piece: dict[str, Any]) -> str | None:
    if not may_start(ctx, piece):
        return None
    cfg = ctx.cfg()
    site = ctx.store.site(piece["site_id"]) or {}
    most = int(cfg.search_phrasings)
    note = ""
    try:
        reply = ctx.app.model.ask(prompts.phrasings_prompt(piece, site, most),
                                  role=str(cfg.role_research), json_schema=prompts.PHRASINGS_SCHEMA,
                                  untrusted=prompts.topic_block(piece) or None)
        phrasings = take_phrasings(reply.data if reply.data is not None else
                                   _loose(reply.text), most)
    except CallError as error:
        phrasings = []
        note = f" The model gave no phrasings ({error.message.rstrip('.')})."
    if not phrasings:
        phrasings = fallback_phrasings(piece, most)
        note += " Searched the topic as written."
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for phrasing in phrasings:
        try:
            results = ctx.app.web.search(phrasing, limit=int(cfg.results_per_search))
        except CallError as error:
            ctx.store.move(piece["id"], "stopped_research", phrasings=phrasings,
                           error=f"The web could not be searched: {error.message} Nothing "
                                 "was written. Press Retry once it can, or drop it.")
            return turn(ctx, piece["id"], "searching", ctx.store.piece(piece["id"])["error"])
        for result in results:
            url = sources.check_url(result.url)
            if url is None or sources.normal_url(url) in seen:
                continue
            seen.add(sources.normal_url(url))
            found.append({"url": url, "title": " ".join(result.title.split())[:300],
                          "phrasing": phrasing})
    found = found[:int(cfg.max_reads)]
    if not found:
        error = ("The searches found nothing to read. Nothing was written. Press Retry to "
                 "search again, or drop it and start with another topic.")
        ctx.store.move(piece["id"], "stopped_research", phrasings=phrasings, error=error)
        return turn(ctx, piece["id"], "searching", error)
    ctx.store.move(piece["id"], "reading", phrasings=phrasings, candidates=found)
    return turn(ctx, piece["id"], "searching", f"Searched {len(phrasings)} ways and found "
                f"{len(found)} pages to read.{note}")


def _loose(text: str) -> Any:
    import json  # noqa: PLC0415

    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# --- reading and ranking --------------------------------------------------------------------


def read_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    store = ctx.store
    store.conn.execute("DELETE FROM source WHERE piece_id=?", (piece["id"],))
    store.conn.commit()
    pages: list[dict[str, Any]] = []
    left_out = 0
    for candidate in piece["candidates"]:
        try:
            page = sources.read(ctx.app, candidate["url"], min_chars=int(cfg.min_source_chars),
                                keep_chars=int(cfg.source_keep_chars))
        except sources.SourceProblem as problem:
            store.insert("source", {"piece_id": piece["id"], "url": candidate["url"],
                                    "host": sources.host_of(candidate["url"]),
                                    "title": candidate["title"], "kept": 0, "why": str(problem),
                                    "created": ctx.now()})
            left_out += 1
            continue
        pages.append({"url": page.url, "final_url": page.final_url,
                      "host": sources.host_of(page.final_url or page.url),
                      "title": page.title or candidate["title"], "text": page.text,
                      "sentences": page.sentences})
    ranked = rank.rank(pages, cfg)
    n = 0
    for item in ranked:
        if item.kept:
            n += 1
        store.insert("source", {"piece_id": piece["id"], "n": n if item.kept else None,
                                "url": item.page["url"], "final_url": item.page["final_url"],
                                "host": item.page["host"], "title": item.page["title"],
                                "text": item.page["text"] if item.kept else "",
                                "sentences": item.page["sentences"] if item.kept else [],
                                "kept": int(item.kept), "weak": int(item.weak), "why": item.why,
                                "score": item.score, "created": ctx.now()})
    least = int(cfg.min_sources)
    if n < least:
        error = (f"Only {n} good source{'s' if n != 1 else ''} could be used, and an article "
                 f"needs at least {least}: {left_out} page{'s' if left_out != 1 else ''} could "
                 f"not be read or were too short, and {len(ranked) - n} were left out by the "
                 "ranking. Nothing was written. Press Retry to search again, or drop it.")
        store.move(piece["id"], "stopped_research", error=error)
        return turn(ctx, piece["id"], "reading", error)
    store.move(piece["id"], "summarising")
    return turn(ctx, piece["id"], "reading", f"Read {len(pages) + left_out} pages and kept "
                f"{n} sources, S1 to S{n}; {left_out + len(ranked) - n} left out, each with "
                "its reason.")


# --- the research summary -------------------------------------------------------------------


def take_summary(data: dict[str, Any], sentences: dict[tuple[int, int], str],
                 most: int) -> tuple[list[dict[str, Any]], int]:
    """Rule ``every statement cites``: statements that cite at least one real source
    sentence, at most ``most``; the others are set aside and counted."""
    kept, aside = [], 0
    for item in data.get("statements") or []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            aside += 1
            continue
        text, _ = markup.remove_addresses(" ".join(item["text"].split()))
        cites = cites_of(item.get("cites"), sentences)
        if not text.strip() or not cites or len(kept) >= most:
            aside += 1
            continue
        kept.append({"text": text.strip()[:SUMMARY_TEXT_MOST], "cites": cites})
    return kept, aside


def summary_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    cfg = ctx.cfg()
    kept_sources = ctx.store.sources(piece["id"])
    sentences = sources.sentence_map(kept_sources)
    most = int(cfg.summary_statements_max)
    reply = ctx.app.model.ask(
        prompts.summary_prompt(piece, most), role=str(cfg.role_research),
        json_schema=prompts.SUMMARY_SCHEMA,
        untrusted=[*prompts.topic_block(piece),
                   *sources.blocks(kept_sources, int(cfg.material_chars))])
    data = data_of(reply, "research summary")
    summary, aside = take_summary(data, sentences, most)
    if not summary:
        raise StepError("The research summary cited no sentence of the sources, so none of it "
                        "was kept and nothing was written.")
    ctx.store.move(piece["id"], "outlining", summary=summary)
    tail = f" {aside} set aside for citing no real sentence." if aside else ""
    return turn(ctx, piece["id"], "summarising", f"Summarised the research in {len(summary)} "
                f"statements, each citing its sources.{tail}", role=str(cfg.role_research),
                **made_by(reply))
