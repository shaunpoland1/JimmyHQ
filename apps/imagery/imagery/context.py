"""``Ctx``: what every part of Imagery is handed: the SDK's ``App``, the store, the clock
and the image services. Nothing holds a global, so a test makes as many as it likes.

The page's calls run on the program's own thread and pictures are made on another, and a
SQLite connection belongs to one thread, so ``store`` opens one connection for each.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from threading import local as _thread_local
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq_app import App, CallError

from imagery import config
from imagery.rules import Refused
from imagery.store import Store

log = logging.getLogger("imagery")

SETTINGS_KEEP_S = 2.0  # one request reads the settings once, not once a field

__all__ = ["Ctx", "Refused", "wall_clock"]


class _PerThread(_thread_local):
    store: Store | None = None


def wall_clock() -> int:
    return int(time.time() * 1000)


class Ctx:
    def __init__(self, app: App, *, clock: Callable[[], int] | None = None,
                 providers: dict[str, Any] | None = None, threaded: bool = True):
        from imagery import providers as known  # noqa: PLC0415

        self.app = app
        self.now: Callable[[], int] = clock or wall_clock
        self.threaded = threaded  # False in tests: they run the queue themselves
        self._local = _PerThread()
        self._stores: list[Store] = []
        self._lock = threading.Lock()
        self.spend_lock = threading.Lock()  # the daily count is read and added to in one go
        self._cfg: config.Config | None = None
        self._cfg_at = 0.0
        self.providers: dict[str, Any] = dict(providers or known.made(app))
        from imagery.making import Maker  # noqa: PLC0415 - it imports this module

        self.maker = Maker(self)

    @property
    def store(self) -> Store:
        mine = self._local.store
        if mine is None:
            mine = Store(self.app.private_db("imagery"), self.now)
            self._local.store = mine
            with self._lock:
                self._stores.append(mine)
        return mine

    def close(self) -> None:
        """Stop the worker and close every database (tests; on Windows an open file cannot
        be removed)."""
        self.maker.shut_down()
        with self._lock:
            for one in self._stores:
                try:
                    one.close()
                except Exception:  # noqa: BLE001, S110 - closing must never fail a test
                    pass
            self._stores.clear()
        self._local = _PerThread()

    def cfg(self) -> config.Config:
        """The owner's settings, read again after a moment or when one changes."""
        if self._cfg is None or time.monotonic() - self._cfg_at > SETTINGS_KEEP_S:
            self._cfg, self._cfg_at = config.load(self.app), time.monotonic()
        return self._cfg

    def forget_settings(self) -> None:
        self._cfg = None

    def provider(self) -> Any:
        """The image service the owner chose (Practice when the choice is unknown)."""
        return self.providers.get(str(self.cfg().provider)) or self.providers["practice"]

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

    def emit(self, event: str = "imagery.changed", data: Any = None) -> None:
        try:
            self.app.emit(event, data)
        except CallError as error:
            log.info("could not tell open pages %s: %s", event, error.message)
