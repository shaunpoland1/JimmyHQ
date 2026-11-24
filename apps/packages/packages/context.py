"""``Ctx``: what every part of Packages is handed: the SDK's ``App``, the store, the clock
and the trackers. Nothing in Packages holds a global, so a test makes as many as it likes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq_app import App, CallError

from packages import config
from packages.store import Store

log = logging.getLogger("packages")

SETTINGS_KEEP_S = 2.0  # one check reads the settings once, not once a package


def wall_clock() -> int:
    return int(time.time() * 1000)


class Ctx:
    def __init__(self, app: App, *, clock: Callable[[], int] | None = None,
                 trackers: dict[str, Any] | None = None,
                 sleep: Callable[[float], None] | None = None):
        from packages import trackers as known  # noqa: PLC0415

        self.app = app
        self.now: Callable[[], int] = clock or wall_clock
        self.sleep: Callable[[float], None] = sleep or time.sleep
        self._store: Store | None = None
        self._cfg: config.Config | None = None
        self._cfg_at = 0.0
        self.trackers: dict[str, Any] = dict(trackers or known.made(app, self.cfg))
        self.busy = threading.Lock()  # one check at a time

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.app.private_db("packages"), self.now)
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

    def tracker(self) -> Any:
        """The tracking service the owner chose (Practice when the choice is unknown)."""
        return self.trackers.get(str(self.cfg().tracker)) or self.trackers["practice"]

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

    def emit(self, event: str = "packages.changed", data: Any = None) -> None:
        try:
            self.app.emit(event, data)
        except CallError as error:
            log.info("could not tell open pages %s: %s", event, error.message)
