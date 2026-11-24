"""``build()``: the Imagery app with its calls, tool and settings wired in.

``imagery.main`` makes the one JimmyHQ runs. A test makes its own with
``build(transport=FakeCore(...), clock=..., providers=..., threaded=False)``; nothing is
shared between two.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jimmyhq_app import App

from imagery import screen, tools
from imagery.context import Ctx


def build(*, transport: Any = None, clock: Callable[[], int] | None = None,
          providers: dict[str, Any] | None = None, threaded: bool = True) -> App:
    app = App(transport=transport)
    ctx = Ctx(app, clock=clock, providers=providers, threaded=threaded)
    app.imagery = ctx  # type: ignore[attr-defined]  # for tests

    @app.on_settings
    def settings_changed(key: str) -> None:
        ctx.forget_settings()
        ctx.emit()

    tools.register(app, ctx)
    for name, fn in screen.calls(ctx).items():
        app.call(name)(fn)
    return app
