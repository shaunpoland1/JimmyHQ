"""``Ctx``: what every part of Posts is handed: the SDK's ``App``, the store, the clock
and the schedulers. Nothing in Posts holds a global, so a test makes as many as it likes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq_app import App, CallError

from posts import config
from posts.store import Store

log = logging.getLogger("posts")

SETTINGS_KEEP_S = 2.0  # one step reads the settings once, not once a check


def wall_clock() -> int:
    return int(time.time() * 1000)


class Ctx:
    def __init__(self, app: App, *, clock: Callable[[], int] | None = None,
                 schedulers: dict[str, Any] | None = None):
        from posts import schedulers as known  # noqa: PLC0415

        self.app = app
        self.now: Callable[[], int] = clock or wall_clock
        self._store: Store | None = None
        self._cfg: config.Config | None = None
        self._cfg_at = 0.0
        self.schedulers: dict[str, Any] = dict(schedulers or known.made(self))
        self.emits = 0  # how many nudges this Ctx has sent its open pages

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.app.db("posts"), self.now)
        return self._store

    def cfg(self) -> config.Config:
        """The owner's settings, read again after a moment or when one changes."""
        if self._cfg is None or time.monotonic() - self._cfg_at > SETTINGS_KEEP_S:
            self._cfg, self._cfg_at = config.load(self.app), time.monotonic()
        return self._cfg

    def forget_settings(self) -> None:
        self._cfg = None

    def zone(self, page: dict[str, Any] | None = None) -> tzinfo:
        """The page's own time zone, else the owner's, else this computer's."""
        name = (page or {}).get("timezone") or ""
        if not name:
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

    def day(self, ms: int, page: dict[str, Any] | None = None) -> str:
        """The calendar day ``ms`` falls on where the page posts, as YYYY-MM-DD."""
        return datetime.fromtimestamp(ms / 1000, self.zone(page)).date().isoformat()

    def tape(self, kind: str, payload: dict[str, Any], subject: str | None = None) -> None:
        """One line on the owner's record. The record is a receipt, so a refusal to write
        it is logged and the work goes on: Posts' own tables hold the state."""
        try:
            self.app.tape.print(kind, payload, subject=subject)
        except CallError as error:
            log.warning("could not write %s on the record: %s", kind, error.message)

    def emit(self, event: str, data: Any = None) -> None:
        self.emits += 1
        try:
            self.app.emit(event, data)
        except CallError as error:
            log.info("could not tell open pages %s: %s", event, error.message)
