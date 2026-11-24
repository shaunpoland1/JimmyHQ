"""``build()``: the Heard app with its inbound door, its daily job and its calls wired in.

``heard.main`` makes the one JimmyHQ runs. A test makes its own with
``build(transport=FakeCore(...), clock=...)``; nothing is shared between two.

There is no card, no sidebar count, no memory source, no tool for an agent, no feed and no
event of its own on the owner's record: the app is private. It asks to take messages from
devices, to run one job a day (the retention), to ask a model and, only if the owner
turns it on, to add a task.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jimmyhq_app import App

from heard import convos, intake, screen
from heard.context import Ctx

DAY_S = 24 * 60 * 60


def build(*, transport: Any = None, clock: Callable[[], int] | None = None) -> App:
    app = App(transport=transport)
    ctx = Ctx(app, clock=clock)
    app.heard = ctx  # type: ignore[attr-defined]  # for tests
    intake.register(ctx)

    @app.job("retention", every=DAY_S)
    def retention_job() -> str:
        return convos.trim(ctx)

    @app.on_settings
    def settings_changed(key: str) -> None:
        ctx.forget_settings()

    for name, fn in screen.calls(ctx).items():
        app.call(name)(fn)
    return app
