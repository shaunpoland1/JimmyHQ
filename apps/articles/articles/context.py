"""``Ctx``: what every part of Articles is handed: the SDK's ``App``, the two stores, the
clock and the publishers. Nothing in Articles holds a global, so a test makes as many as
it likes, and ``close`` shuts both databases.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq_app import App, CallError

from articles import config
from articles.store import Store

log = logging.getLogger("articles")

SETTINGS_KEEP_S = 2.0  # one step reads the settings once, not once a check


def wall_clock() -> int:
    return int(time.time() * 1000)


class Ctx:
    def __init__(self, app: App, *, clock: Callable[[], int] | None = None,
                 publishers: dict[str, Any] | None = None):
        from articles import publishers as known  # noqa: PLC0415

        self.app = app
        self.now: Callable[[], int] = clock or wall_clock
        self._store: Store | None = None
        self._replies: Any = None
        self._cfg: config.Config | None = None
        self._cfg_at = 0.0
        self.publishers: dict[str, Any] = dict(publishers or known.made(self))
        self.emits = 0  # how many nudges this Ctx has sent its open pages

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.app.db("articles"), self.now)
        return self._store

    @property
    def replies(self) -> Any:
        """Readers' replies and the ideas found in them: the private store, apart from
        everything else (``replies.ReplyStore``)."""
        if self._replies is None:
            from articles.replies import ReplyStore  # noqa: PLC0415

            self._replies = ReplyStore(self.app.private_db("replies"), self.now)
        return self._replies

    def close(self) -> None:
        for held in (self._store, self._replies):
            if held is not None:
                held.close()
        self._store = self._replies = None

    def cfg(self) -> config.Config:
        """The owner's settings, read again after a moment or when one changes."""
        if self._cfg is None or time.monotonic() - self._cfg_at > SETTINGS_KEEP_S:
            self._cfg, self._cfg_at = config.load(self.app), time.monotonic()
        return self._cfg

    def forget_settings(self) -> None:
        self._cfg = None

    def zone(self) -> tzinfo:
        name = ""
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

    def granted(self) -> set[str]:
        """What the owner granted this app, as JimmyHQ said at hello."""
        try:
            return set(self.app.hello().get("permissions") or [])
        except CallError:
            return set()

    def owner_hosts(self) -> list[str]:
        """The hosts the owner gave Articles himself (its own site's), asked afresh each
        time: he may add or remove one at any moment."""
        try:
            return list(self.app.hosts.list().hosts)
        except CallError:
            return []

    def tape(self, kind: str, payload: dict[str, Any], subject: str | None = None) -> None:
        """One line on the owner's record. The record is a receipt: a refusal to write it
        is logged and the work goes on, since Articles' own tables hold the state."""
        try:
            self.app.tape.print(kind, payload, subject=subject)
        except CallError as error:
            log.warning("could not write %s on the record: %s", kind, error.message)

    def changes(self) -> int:
        """Rows changed so far in both stores: a call that moves this changed a record."""
        count = self._store.conn.total_changes if self._store is not None else 0
        if self._replies is not None:
            count += self._replies.db.conn.total_changes
        return count

    def emit(self, event: str = "articles.changed", data: Any = None) -> None:
        self.emits += 1
        try:
            self.app.emit(event, data)
        except CallError as error:
            log.info("could not tell open pages %s: %s", event, error.message)
