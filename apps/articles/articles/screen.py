"""What the Articles screen asks: everything it shows, shaped for the page.

``calls(ctx)`` adds the page's own calls beside the ones in ``program``. Each returns
plain JSON, and none changes a record.

- ``overview``: every view's lists in one request: the sites, the recent articles with
  their open questions and warnings counted and their last publishing attempt, the
  publishers, the owner's own addresses a WordPress site may name, whether header images
  are switched on, readers' replies as counts only, the ideas waiting, and the decline
  reasons.
- ``article.view``: one article in full: its research (the sources kept and why, those
  left out and why, the summary, the outline), its current version with the evidence for
  each sentence, the checks, every version with what it changed, each step's line, and
  each publishing attempt.
- ``article.sources``: the numbered sentences of an article's sources, in parts no larger
  than ``screen_answer_kb``, so a long source never makes one answer too large to send.

The evidence is worked out here, by the same code and the same keys the checks use, so
what the page lights up is exactly what was checked: each sentence of the article with
what it cites, the place of every number and quotation in it with the check that judged
it, and the other questions raised about it. Text from a source, an article or a model is
returned as it is; the page shows it as text, never as HTML.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from typing import Any

from jimmyhq_app.claims import _NUMBER, MIN_QUOTE_WORDS, QUOTE, quote_key

from articles import checked, coverage, markup, replies, sites, states, waiting
from articles.checks.claims import _key
from articles.context import Ctx
from articles.markup import Claim

# Checks about a whole sentence of the article, kept under a key that ends with it.
SENTENCE_CHECKS = ("uncited", "advice", "cites", "names")
ENVELOPE = 512  # room kept for the answer's own keys around the sentences
PIECE_FIELDS = ("id", "site_id", "way", "origin", "topic", "angle", "notes", "state", "error",
                "round", "passes", "owner_note", "created", "updated")


class ScreenError(ValueError):
    """Nothing was shown. The message says why."""


def _id(args: dict[str, Any]) -> int:
    try:
        return int(args["id"])
    except (KeyError, TypeError, ValueError):
        raise ScreenError("Say which article with \"id\".") from None


def publishers(ctx: Ctx) -> list[dict[str, Any]]:
    """Where a site may publish, and whether each sends anything off this computer."""
    return [{"id": p.id, "label": p.label, "sends_out": bool(p.sends_out)}
            for p in ctx.publishers.values()]


def _counts(ctx: Ctx) -> dict[int, dict[str, int]]:
    """Open questions and warnings on each article's current version."""
    out: dict[int, dict[str, int]] = {}
    for row in ctx.store.all(
            "SELECT v.piece_id, c.level, c.answer FROM check_result c "
            "JOIN revision v ON v.id = c.revision_id "
            "JOIN piece p ON p.id = v.piece_id AND p.revision = v.n"):
        seen = out.setdefault(row["piece_id"], {"questions": 0, "warnings": 0})
        if row["level"] == "ask" and not row["answer"]:
            seen["questions"] += 1
        elif row["level"] == "warn":
            seen["warnings"] += 1
    return out


def _last_attempts(ctx: Ctx) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in ctx.store.all("SELECT id, piece_id, publisher, started, finished, outcome, "
                             "detail FROM attempt ORDER BY id"):
        out[row["piece_id"]] = row
    return out


def _close(piece: dict[str, Any], now_ms: int) -> dict[str, Any] | None:
    """What the coverage check found, in words, once it has run."""
    found = piece.get("coverage") or {}
    if not found.get("method"):
        return None
    return {"said": coverage.describe(found, now_ms), "close": bool(found.get("close")),
            "note": found.get("note") or "", "best": found.get("best")}


def overview(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store, cfg, now = ctx.store, ctx.cfg(), ctx.now()
    labels = {p["id"]: p for p in publishers(ctx)}
    found = [{**s, "publisher_label": labels.get(s["publisher"], {}).get("label",
                                                                        s["publisher"]),
              "sends_out": labels.get(s["publisher"], {}).get("sends_out", False)}
             for s in store.sites()]
    counts = _counts(ctx)
    attempts = _last_attempts(ctx)
    kept = {r["piece_id"]: r["n"] for r in store.all(
        "SELECT piece_id, COUNT(*) AS n FROM source WHERE kept=1 GROUP BY piece_id")}
    pieces = store.all(
        "SELECT p.id, p.site_id, p.way, p.origin, p.topic, p.angle, p.state, p.error, "
        "p.coverage, p.created, p.updated, p.revision, v.title, v.created AS written "
        "FROM piece p LEFT JOIN revision v ON v.piece_id = p.id AND v.n = p.revision "
        "ORDER BY p.updated DESC, p.id DESC LIMIT ?", (int(cfg.screen_rows),))
    for piece in pieces:
        piece["words"] = states.WORDS.get(piece["state"], piece["state"])
        piece["ways_out"] = states.WAYS_OUT.get(piece["state"], "")
        piece["close"] = _close(piece, now)
        del piece["coverage"]
        piece["title"] = piece["title"] or ""
        piece.update(counts.get(piece["id"], {"questions": 0, "warnings": 0}))
        piece["sources"] = kept.get(piece["id"], 0)
        piece["attempt"] = attempts.get(piece["id"])
    status = replies.status(ctx)
    return {"now_ms": now, "sites": found, "hint": "" if found else sites.NO_SITES,
            "publishers": publishers(ctx), "pieces": pieces,
            "hosts": ctx.owner_hosts(), "header_images": bool(cfg.header_images),
            "replies": {k: status[k] for k in ("replies", "unread", "hint")},
            "ideas": replies.ideas(ctx)["ideas"], "ideas_hosted": bool(cfg.ideas_hosted),
            "waiting": waiting.count(ctx),  # the sidebar's number: the page says the same
            "single_keys": bool(cfg.single_keys),
            "cite_style": str(cfg.cite_style),
            "decline_reasons": [str(r) for r in cfg.decline_reasons or [] if str(r).strip()]}


# --- one article ------------------------------------------------------------------------------


def _suffix(claim: Claim) -> str:
    """The end of every key a check keeps about this sentence (``checks.claims._key``)."""
    return _key("", claim)


def claim_marks(claim: Claim, by_key: dict[tuple[str, str], dict[str, Any]]
                ) -> list[dict[str, Any]]:
    """Where each number and quotation sits in the sentence, with the id of the check that
    judged it under the key that check keeps. A number inside a quotation is left to the
    quotation's mark."""
    marks: list[dict[str, Any]] = []
    for found in QUOTE.finditer(claim.text):
        span = (found.group(1) or found.group(2) or "").strip()
        if len(span.split()) < MIN_QUOTE_WORDS:
            continue
        check = by_key.get(("quotes", quote_key(span)[:80]))
        marks.append({"start": found.start(), "end": found.end(), "kind": "quote",
                      "check": check["id"] if check else None})
    suffix = _suffix(claim)
    for found in _NUMBER.finditer(claim.text):
        written = found.group(0).strip()
        start = found.start()
        if any(m["start"] <= start < m["end"] for m in marks if m["kind"] == "quote"):
            continue
        check = by_key.get(("numbers", f"{written.lower()}{suffix}"))
        marks.append({"start": start, "end": start + len(found.group(0).rstrip()),
                      "kind": "number", "check": check["id"] if check else None})
    return sorted(marks, key=lambda m: m["start"])


def _review_place(check: dict[str, Any]) -> str:
    """The opening of the article's words a review finding quotes, as its key keeps it."""
    parts = str(check["key"]).split(":", 2)
    return parts[2].strip() if len(parts) == 3 else ""


def evidence(title: str, body: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The article as blocks of sentences, each sentence with what it cites, its marks, and
    the questions about it. The title is the first block (``h1``), as the checks read it."""
    by_key = {(c["name"], c["key"]): c for c in checks}
    reviews = [c for c in checks if c["name"] == "review" and c["level"] != "ok"
               and _review_place(c)]
    placed: set[int] = set()
    out: list[dict[str, Any]] = []
    index = 0
    for tag, text in [("h1", title), *markup.blocks(body)]:
        if tag == "h1":
            claims = [Claim(0, "h1", title, ())] if title else []
        else:
            claims = markup.claims(f"<{tag}>{html.escape(text, quote=False)}</{tag}>")
        sentences = []
        for claim in claims:
            index += 1
            suffix = _suffix(claim)
            about = [c["id"] for c in checks if c["name"] in SENTENCE_CHECKS
                     and str(c["key"]).endswith(suffix)]
            flat = " ".join(claim.text.lower().split())
            for review in reviews:
                if review["id"] not in placed and _review_place(review) in flat:
                    about.append(review["id"])
                    placed.add(review["id"])
            sentences.append({"i": index, "text": claim.text,
                              "cites": [list(c) for c in claim.cites],
                              "marks": claim_marks(claim, by_key), "checks": about})
        if sentences:
            out.append({"tag": tag, "sentences": sentences})
    return out


def view(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    store = ctx.store
    piece = store.piece(_id(args))
    if piece is None:
        raise ScreenError("There is no such article. It may have been removed.")
    now = ctx.now()
    site = store.site(piece["site_id"])
    revision = store.revision(piece["id"])
    found_checks = store.checks(revision["id"]) if revision else []
    return {
        "now_ms": now,
        "piece": {**{k: piece[k] for k in PIECE_FIELDS},
                  "words": states.WORDS.get(piece["state"], piece["state"]),
                  "ways_out": states.WAYS_OUT.get(piece["state"], "")},
        "site": {k: site[k] for k in ("id", "name", "publisher", "timezone")} if site else None,
        "close": _close(piece, now),
        "phrasings": piece["phrasings"] or [],
        "summary": piece["summary"] or [],
        "outline": piece["outline"] or {},
        "sources": [{k: s[k] for k in ("n", "url", "final_url", "host", "title", "why")}
                    | {"weak": bool(s["weak"]), "sentences": len(s["sentences"])}
                    for s in store.sources(piece["id"])],
        "left_out": store.dropped_sources(piece["id"]),
        "revision": None if revision is None else {
            "n": revision["n"], "kind": revision["kind"], "title": revision["title"],
            "body": revision["body"], "aside": revision["aside"], "created": revision["created"],
            "words": markup.word_count(revision["body"])},
        "revisions": [{k: r[k] for k in ("n", "kind", "kept", "open_count", "changes",
                                         "created")} for r in store.revisions(piece["id"])],
        "checks": found_checks,
        "said": checked.summary(found_checks) if revision else "",
        "blocks": evidence(revision["title"], revision["body"], found_checks) if revision
        else [],
        "turns": [{k: t[k] for k in ("id", "step", "role", "model", "local", "gloss",
                                     "created")} for t in store.turns(piece["id"])],
        "attempts": [{k: a[k] for k in ("id", "publisher", "started", "finished", "outcome",
                                        "detail")} for a in store.attempts(piece["id"])],
    }


def source_sentences(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The kept sources' numbered sentences after source ``after``, as many whole sources
    as fit in ``screen_answer_kb`` (always at least one), and the number to ask after
    next, or None at the end."""
    piece_id = _id(args)
    if ctx.store.piece(piece_id) is None:
        raise ScreenError("There is no such article. It may have been removed.")
    try:
        after = int(args.get("after") or 0)
    except (TypeError, ValueError):
        after = 0
    room = int(ctx.cfg().screen_answer_kb) * 1024 - ENVELOPE
    out: list[dict[str, Any]] = []
    used = 0
    rest = [s for s in ctx.store.sources(piece_id) if s["n"] > after]
    for source in rest:
        size = len(json.dumps(source["sentences"]).encode("utf-8")) + 48
        if out and used + size > room:
            return {"sources": out, "next": out[-1]["n"]}
        sentences = source["sentences"]
        if size > room:  # one source alone too large: as many sentences as fit
            sentences, total = [], 2
            for sentence in source["sentences"]:
                total += len(json.dumps(sentence).encode("utf-8")) + 2
                if total > room:
                    break
                sentences.append(sentence)
        out.append({"n": source["n"], "sentences": sentences,
                    "cut": len(sentences) < len(source["sentences"])})
        used += size
    return {"sources": out, "next": None}


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    return {"overview": lambda a: overview(ctx, a),
            "article.view": lambda a: view(ctx, a),
            "article.sources": lambda a: source_sentences(ctx, a)}
