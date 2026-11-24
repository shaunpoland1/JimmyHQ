"""``Ctx``: what every part of Books is handed: the SDK's ``App``, the store and the clock.
Nothing in Books holds a global, so a test makes as many as it likes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq_app import App, CallError

from books import config
from books.store import Store

log = logging.getLogger("books")

SETTINGS_KEEP_S = 2.0


def wall_clock() -> int:
    return int(time.time() * 1000)


class Ctx:
    def __init__(self, app: App, *, clock: Callable[[], int] | None = None):
        self.app = app
        self.now: Callable[[], int] = clock or wall_clock
        self._store: Store | None = None
        self._cfg: config.Config | None = None
        self._cfg_at = 0.0

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.app.private_db("books"), self.now)
            from books.ops import seed  # noqa: PLC0415 - ops needs Ctx, so it comes later

            seed(self._store, self.now())
        return self._store

    def close(self) -> None:
        """Close the database (tests; on Windows an open file cannot be removed)."""
        if self._store is not None:
            self._store.close()
            self._store = None

    def cfg(self) -> config.Config:
        """The owner's settings, read again after a moment or when one changes."""
        if self._cfg is None or time.monotonic() - self._cfg_at > SETTINGS_KEEP_S:
            self._cfg, self._cfg_at = config.load(self.app), time.monotonic()
        return self._cfg

    def forget_settings(self) -> None:
        self._cfg = None

    def style(self) -> str:
        return "comma" if str(self.cfg().number_style) == "comma" else "point"

    def zone(self) -> tzinfo:
        """The owner's time zone, else this computer's."""
        try:
            name = self.app.timezone
        except CallError:
            name = ""
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                log.warning("unknown time zone %r; using this computer's", name)
        return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")

    def today(self) -> date:
        """Today's date where the owner is."""
        return datetime.fromtimestamp(self.now() / 1000, self.zone()).date()

    def emit(self, event: str = "books.changed") -> None:
        try:
            self.app.emit(event, None)
        except CallError as error:
            log.info("could not tell open pages %s: %s", event, error.message)
