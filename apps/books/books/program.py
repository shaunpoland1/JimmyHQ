"""``build()``: the Books app with its job, card, calls and settings wired in.

``books.main`` makes the one JimmyHQ runs. A test makes its own with
``build(transport=FakeCore(...), clock=...)``; nothing is shared between two.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jimmyhq_app import App, Decision

from books import auto, reminders, screen, tell
from books.context import Ctx


def build(*, transport: Any = None, clock: Callable[[], int] | None = None) -> App:
    app = App(transport=transport)
    ctx = Ctx(app, clock=clock)
    app.books = ctx  # type: ignore[attr-defined]  # for tests

    @app.job("due", every_setting="check_minutes")
    def due_job() -> str:
        posted = auto.post_due(ctx)  # first: what pays itself needs no card
        filed = tell.check(ctx)
        ctx.emit()  # a new day moves bills from soon to today on an open page
        return (f"{posted} posted by themselves, {filed} card{'s' if filed != 1 else ''} filed.")

    @app.on_decision("due")
    def card_closed(decision: Decision) -> str:
        return tell.on_decision(ctx, decision)

    @app.ui.counter
    def due_now() -> int:
        # The sidebar's number: reminders due today or overdue, as the page counts them.
        return reminders.due_now(ctx.store, ctx.today())

    @app.on_settings
    def settings_changed(key: str) -> None:
        ctx.forget_settings()
        if key in ("tell_bills", "tell_days"):
            tell.check(ctx)
        ctx.emit()

    for name, fn in screen.calls(ctx).items():
        app.call(name)(fn)
    return app
