"""``build()``: the Reader app with every job, card, call, tool, source and kind wired in.

``reader.main`` makes the one JimmyHQ runs. A test makes its own with
``build(transport=FakeCore(...), clock=...)``; nothing is shared between two.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jimmyhq_app import App, Decision, Work

from reader import (
    articles,
    cards,
    config,
    finding,
    kinds,
    mining,
    outside,
    posted,
    screen,
    tools,
    work,
)
from reader.context import Ctx


def advance(ctx: Ctx) -> str:
    """The ``advance`` job: carry out new suggestions, mine what waits to be mined,
    delete text past its days, look after the researcher's rounds, and mark articles
    close to something Posts already posted."""
    said = articles.carry_out(ctx)
    said += mining.mine_queued(ctx)
    deleted = articles.expire_text(ctx)
    if deleted:
        said.append(f"Deleted the text of {deleted} articles.")
    said += work.watch(ctx)
    said += posted.follow(ctx)  # Posts' feed, when the owner let Reader read it
    return " ".join(said) or "Nothing to do."


def build(*, transport: Any = None, clock: Callable[[], int] | None = None) -> App:
    app = App(transport=transport)
    ctx = Ctx(app, clock=clock)
    app.reader = ctx  # type: ignore[attr-defined]  # for tests
    kinds.declare(app)

    @app.job("search", every_setting="search_minutes")
    def search() -> str:
        return finding.run(ctx)

    @app.job("advance", every_setting="advance_minutes")
    def advance_job() -> str:
        return advance(ctx)

    @app.on_decision("article")
    def article_decided(decision: Decision) -> str:
        return cards.on_article(ctx, decision)

    @app.on_decision("notice")
    def notice_seen(decision: Decision) -> str | None:
        if (decision.subject or "").startswith("round:"):
            return work.on_card(ctx, decision)
        return None

    @app.on_work
    def work_came_back(given: Work) -> str:
        return work.on_work(ctx, given)

    @app.memory_source("facts")
    def facts(cursor: str | None, limit: int) -> Any:
        return mining.collect(ctx, cursor, limit)

    tools.register(app, ctx)
    outside.register(app, ctx)  # for anyone who holds no role: read only

    @app.ui.counter
    def waiting_on_owner() -> int:
        return screen.waiting(ctx)  # the sidebar's number: the articles in triage

    @app.on_settings
    def settings_changed(key: str) -> None:
        ctx.forget_settings()
        for kind, pair in config.PAIRS.items():
            if key in pair:
                said = config.pair(ctx.cfg(), ctx.store, kind)
                if said.refused:
                    cards.notice(ctx, "Reader did not take that change", said.refused)
        ctx.emit()

    for name, fn in screen.calls(ctx).items():
        app.call(name)(fn)
    return app
