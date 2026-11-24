"""``build()``: the Articles app with every job, card, call and kind wired in.

``articles.main`` makes the one JimmyHQ runs. A test makes its own with
``build(transport=FakeCore(...), clock=...)``; nothing is shared between two, and
``app.articles.close()`` shuts its databases.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jimmyhq_app import App, Decision, Work

from articles import (
    actions,
    cards,
    checked,
    credits,
    fill,
    header,
    kinds,
    outside,
    plain,
    publish,
    replies,
    screen,
    sites,
    states,
    tools,
    waiting,
    work,
)
from articles.context import Ctx
from articles.ways import WAYS

ADVANCE_EVERY_S = 60


def build(*, transport: Any = None, clock: Callable[[], int] | None = None,
          publishers: dict[str, Any] | None = None) -> App:
    app = App(transport=transport)
    ctx = Ctx(app, clock=clock, publishers=publishers)
    app.articles = ctx  # type: ignore[attr-defined]  # for tests and later parts
    kinds.declare(app)

    @app.job("advance", every=ADVANCE_EVERY_S)
    def advance() -> str:
        return plain.advance(ctx)

    @app.on_decision("article")
    def article_decided(decision: Decision) -> str:
        return cards.on_article(ctx, decision)

    @app.on_decision("coverage")
    def coverage_decided(decision: Decision) -> str:
        return cards.on_coverage(ctx, decision)

    @app.on_decision("notice")
    def notice_seen(decision: Decision) -> str | None:
        if (decision.subject or "").startswith("work:"):
            return work.on_card(ctx, decision)  # Wait, Finish without agents, Drop
        return None

    @app.job("watch", every_setting="watch_minutes")
    def watch() -> str:
        return work.watch(ctx)

    @app.on_work
    def work_came_back(given: Work) -> str:
        return work.on_work(ctx, given)

    tools.register(app, ctx)
    outside.register(app, ctx)  # for anyone who holds no role: read only

    @app.ui.counter
    def waiting_on_owner() -> int:
        return waiting.count(ctx)  # the sidebar's number, said after everything that changes it

    @app.on_settings
    def settings_changed(key: str) -> None:
        ctx.forget_settings()

    for name, fn in calls(ctx).items():
        app.call(name)(fn if name in READS else announcing(ctx, fn))
    return app


# The page's calls that only read. Every other call is wrapped by ``announcing``.
READS = frozenset({"overview", "sites.list", "pieces.list", "piece.get", "article.view",
                   "article.sources", "article.export", "replies.status", "ideas.list",
                   "sites.hosts", "sites.fill", "header.get", "header.style.get"})


def announcing(ctx: Ctx, fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    """``fn``, telling the open pages ``articles.changed`` whenever it changed a record and
    did not say so itself, so every open page draws itself again."""

    def run(args: dict[str, Any]) -> Any:
        changes, emits = ctx.changes(), ctx.emits
        try:
            return fn(args)
        finally:
            if ctx.changes() != changes and ctx.emits == emits:
                ctx.emit()

    run.__wrapped__ = fn  # type: ignore[attr-defined]
    return run


def _int(args: dict[str, Any], key: str = "id") -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Say which one with \"{key}\". Nothing was changed.") from None


def calls(ctx: Ctx) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """What the app's page may ask. Each returns plain JSON. A reader's reply is never in
    an answer: the replies calls answer counts, and ideas their own words."""

    def piece_list(args: dict[str, Any]) -> list[dict[str, Any]]:
        rows = ctx.store.all("SELECT id, site_id, way, topic, angle, state, error, updated "
                             "FROM piece ORDER BY id DESC LIMIT 200")
        return [{**r, "words": states.WORDS.get(r["state"], r["state"]),
                 "ways_out": states.WAYS_OUT.get(r["state"], "")} for r in rows]

    def piece_view(args: dict[str, Any]) -> dict[str, Any]:
        piece = ctx.store.piece(_int(args))
        if piece is None:
            raise ValueError("There is no such article.")
        revision = ctx.store.revision(piece["id"])
        return {"piece": {**piece, "words": states.WORDS.get(piece["state"], piece["state"]),
                          "ways_out": states.WAYS_OUT.get(piece["state"], "")},
                "turns": ctx.store.turns(piece["id"]),
                "sources": [{k: s[k] for k in ("n", "url", "host", "title", "why", "weak")}
                            | {"sentences": len(s["sentences"])}
                            for s in ctx.store.sources(piece["id"])],
                "left_out": ctx.store.dropped_sources(piece["id"]),
                "revision": revision, "revisions": [
                    {k: r[k] for k in ("n", "kind", "kept", "open_count", "changes", "created")}
                    for r in ctx.store.revisions(piece["id"])],
                "checks": ctx.store.checks(revision["id"]) if revision else [],
                "attempts": ctx.store.attempts(piece["id"]),
                "worked": credits.line(ctx.store, piece["id"])}

    def from_page(fn: Callable[..., dict[str, Any]], *more: str
                  ) -> Callable[[dict[str, Any]], Any]:
        def run(args: dict[str, Any]) -> Any:
            piece = ctx.store.piece(_int(args)) or {}
            done = fn(ctx, _int(args), *(str(args.get(k) or "") for k in more))
            cards.withdraw(ctx, piece.get("card_id"))  # decided on the page, not the card
            return done
        return run

    out: dict[str, Callable[[dict[str, Any]], Any]] = {
        "sites.list": lambda a: {"sites": ctx.store.sites(),
                                 "hint": "" if ctx.store.sites() else sites.NO_SITES},
        "sites.save": lambda a: sites.save_site(ctx.store, a),
        "sites.fill": lambda a: fill.fill(ctx, a),
        "pieces.list": piece_list,
        "piece.get": piece_view,
        "piece.retry": lambda a: plain.retry(ctx, _int(a)),
        "piece.drop": lambda a: actions.drop(ctx, _int(a)),
        "piece.write_anyway": lambda a: actions.write_anyway(ctx, _int(a)),
        "article.edit": lambda a: actions.edit(ctx, _int(a), title=a.get("title"),
                                               body=a.get("body")),
        "check.answer": lambda a: checked.answer(ctx, _int(a), str(a.get("answer") or "")),
        "article.approve": from_page(actions.approve),
        "article.send_back": from_page(actions.send_back, "note"),
        "article.decline": from_page(actions.decline, "reason"),
        "article.publish": lambda a: publish.publish(ctx, _int(a)),
        "article.clear": lambda a: publish.clear_unsure(ctx, _int(a)),
        "article.published": lambda a: publish.mark_published(ctx, _int(a)),
        "article.export": lambda a: publish.export(ctx, _int(a), str(a.get("form") or "")),
        "work.waiting": lambda a: {"work": work.needs_owner(ctx)},
        "work.choose": lambda a: work.from_page(ctx, _int(a), str(a.get("action") or "")),
        "article.edit_sent": lambda a: publish.edit_sent(ctx, _int(a), title=a.get("title"),
                                                         body=a.get("body")),
        "sites.hosts": lambda a: {"hosts": ctx.owner_hosts()},
        "header.make": lambda a: header.make(ctx, _int(a)),
        "header.get": lambda a: {"header": header.view(ctx, _int(a))},
        "header.style.get": lambda a: {"site_id": _int(a, "site_id"),
                                       "style": header.style_of(ctx, _int(a, "site_id"))},
        "header.style.set": lambda a: header.set_style(ctx, _int(a, "site_id"),
                                                       a.get("style")),
        "replies.paste": lambda a: replies.paste(ctx, a),
        "replies.status": lambda a: replies.status(ctx),
        "replies.forget": lambda a: replies.forget(ctx),
        "ideas.find": lambda a: replies.find(ctx),
        "ideas.list": lambda a: replies.ideas(ctx),
        "idea.start": lambda a: replies.start(ctx, _int(a), a.get("site_id")),
        "idea.dismiss": lambda a: replies.dismiss(ctx, _int(a)),
    }
    for way, start in WAYS.items():
        out[f"new.{way}"] = (lambda fn: lambda a: fn(ctx, a))(start)
    out.update(screen.calls(ctx))
    return out
