"""``Ctx``: what every part of Reader is handed: the SDK's ``App``, the store and the clock.
Nothing in Reader holds a global, so a test makes as many as it likes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq_app import App, CallError

from reader import config
from reader.store import Store

log = logging.getLogger("reader")

SETTINGS_KEEP_S = 2.0  # one job reads the settings once, not once an article


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
            self._store = Store(self.app.db("reader"), self.now)
        return self._store

    def close(self) -> None:
        """Close the database (tests; on Windows an open file cannot be removed)."""
        if self._store is not None:
            self._store.conn.close()
            self._store = None

    def cfg(self) -> config.Config:
        """The owner's settings, read again after a moment or when one changes."""
        if self._cfg is None or time.monotonic() - self._cfg_at > SETTINGS_KEEP_S:
            self._cfg, self._cfg_at = config.load(self.app), time.monotonic()
        return self._cfg

    def forget_settings(self) -> None:
        self._cfg = None

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

    def day(self, ms: int) -> str:
        """The owner's calendar day ``ms`` falls on, as YYYY-MM-DD."""
        return datetime.fromtimestamp(ms / 1000, self.zone()).date().isoformat()

    def today(self) -> str:
        return self.day(self.now())

    def tape(self, kind: str, payload: dict[str, Any], subject: str | None = None) -> None:
        """One line on the owner's record. The record is a receipt, so a refusal to write
        it is logged and the work goes on: Reader's own tables hold the state."""
        try:
            self.app.tape.print(kind, payload, subject=subject)
        except CallError as error:
            log.warning("could not write %s on the record: %s", kind, error.message)

    def emit(self, event: str = "reader.changed", data: Any = None) -> None:
        try:
            self.app.emit(event, data)
        except CallError as error:
            log.info("could not tell open pages %s: %s", event, error.message)
