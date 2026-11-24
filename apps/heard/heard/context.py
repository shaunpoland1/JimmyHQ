"""``Ctx``: what every part of Heard is handed: the SDK's ``App``, the store, the model
questions on their way and the clock. Nothing holds a global, so a test makes as many as
it likes."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq_app import App, CallError

from heard import config
from heard.jobs import Jobs
from heard.store import Store

log = logging.getLogger("heard")

SETTINGS_KEEP_S = 2.0  # one pass reads the settings once, not once a message
MINUTE_MS = 60_000


def wall_clock() -> int:
    return int(time.time() * 1000)


class Ctx:
    def __init__(self, app: App, *, clock: Callable[[], int] | None = None):
        self.app = app
        self.now: Callable[[], int] = clock or wall_clock
        self._store: Store | None = None
        self._cfg: config.Config | None = None
        self._cfg_at = 0.0
        self.jobs = Jobs()  # questions to a model that are on their way

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.app.private_db("heard"), self.now)
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

    def gap_ms(self) -> int:
        return int(self.cfg().gap_minutes) * MINUTE_MS

    def slack_ms(self) -> int:
        """How far past the time a message arrived a line's time may be kept: a recorder's
        clock may run a little fast, but nothing it says is kept as later than that."""
        return int(self.cfg().clock_slack_minutes) * MINUTE_MS

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
                log.warning("unknown time zone; using this computer's")
        return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")

    def day_of(self, ms: int) -> str:
        """The local calendar date of a stored time, ``YYYY-MM-DD``."""
        return datetime.fromtimestamp(ms / 1000, self.zone()).date().isoformat()
