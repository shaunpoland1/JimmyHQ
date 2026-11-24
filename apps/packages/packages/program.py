"""``build()``: the Packages app with its job, card, calls and settings wired in.

``packages.main`` makes the one JimmyHQ runs. A test makes its own with
``build(transport=FakeCore(...), clock=..., trackers=...)``; nothing is shared between two.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jimmyhq_app import App, Decision

from packages import screen, sync, tell
from packages.context import Ctx


def build(*, transport: Any = None, clock: Callable[[], int] | None = None,
          trackers: dict[str, Any] | None = None,
          sleep: Callable[[float], None] | None = None) -> App:
    app = App(transport=transport)
    ctx = Ctx(app, clock=clock, trackers=trackers, sleep=sleep)
    app.packages = ctx  # type: ignore[attr-defined]  # for tests

    @app.job("sync", every_setting="check_minutes")
    def sync_job() -> str:
        return sync.tick(ctx)

    @app.on_decision("update")
    def update_seen(decision: Decision) -> str:
        return tell.on_decision(ctx, decision)

    @app.ui.counter
    def needs_you() -> int:
        # The sidebar's number, the same rule as ``loud`` in web/lib/rows.js.
        return sum(1 for row in ctx.store.packages()
                   if row.get("status") in ("out_for_delivery", "problem"))

    @app.on_settings
    def settings_changed(key: str) -> None:
        ctx.forget_settings()
        ctx.emit()

    for name, fn in screen.calls(ctx).items():
        app.call(name)(fn)
    return app
