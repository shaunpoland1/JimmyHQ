"""``ui.count``: how many things wait on the owner in a full app, kept by JimmyHQ so the
sidebar can show it without the app being open.

    app.ui.count(3)                                # in the program

The page can already say a number with ``ui.badge``, but only while its frame is open. A
program says it with ``ui.count`` whenever it changes, and JimmyHQ keeps the latest one
per app (``app_counts`` in ``apps.db``). The list of installed apps the shell loads
(``GET /api/apps``) carries it as ``count``. While the app's page is open and has said a
number of its own, the page's number is the one shown.

No permission is needed: the number appears only on the app's own line in the sidebar and
reveals nothing outside the app, as ``ui.badge`` does not. It is a whole number from 0 to
``MOST``. It is working state, not the record: a running program says it again at start.

What the owner sees is only ever the latest number of an app that is installed and
switched on. A switched-off app has no line in the sidebar and its last number is not
trusted (``shown``: ``None``); removing an app deletes its number (``forget``).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.apps import api, dbfile, quotas
from jimmyhq.apps.bridge import BridgeError, Call, Ctx, only
from jimmyhq.apps.permissions import FULL
from jimmyhq.ids import now_ms

if TYPE_CHECKING:
    from jimmyhq.core import Core

MOST = 999


def check_count(value: Any) -> int:
    """The number a program says, or a refusal that says nothing was kept."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MOST:
        raise BridgeError("invalid", f"\"n\" for ui.count must be a whole number from 0 to "
                                     f"{MOST}. Nothing was changed. Count what waits on the "
                                     f"owner and send at most {MOST}.")
    return value


class AppCounts:
    """The latest count of each app, in SQLite so a restart shows the last one at once."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        storeversion.prepare(self._db, dbfile.STORE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def set(self, app_id: str, n: int, now: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO app_counts (app_id, n, at) VALUES (?,?,?) "
                "ON CONFLICT (app_id) DO UPDATE SET n=excluded.n, at=excluded.at",
                (app_id, n, now))

    def get(self, app_id: str) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT n FROM app_counts WHERE app_id=?",
                                   (app_id,)).fetchone()
        return None if row is None else int(row[0])

    def every(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT app_id, n FROM app_counts").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def forget(self, app_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM app_counts WHERE app_id=?", (app_id,))


def counts(core: Core) -> AppCounts:
    return core.service("apps.counts", lambda c: AppCounts(quotas.state_db(c)))


def forget(core: Core, app_id: str) -> None:
    """An app was removed: its number goes with it."""
    counts(core).forget(app_id)


def shown(core: Core, rows: dict[str, dict[str, Any]]) -> dict[str, int | None]:
    """The count to show for each installed app (``rows``: ``store.installed``), or None
    for one with no number, or one that is switched off: what it said before is unknown
    now, and it is said again when it runs."""
    said = counts(core).every()
    return {app_id: said.get(app_id) if row.get("enabled") else None
            for app_id, row in rows.items()}


def ui_count(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    only("ui.count", args, ("n",), ("n",))
    n = check_count(args["n"])
    counts(ctx.core).set(ctx.id, n, now_ms())
    return {"n": n}


CALL = Call("ui.count", ui_count, tiers=(FULL,))


def add_call() -> None:
    if CALL.name not in api.API_CALLS:
        api.add_call(CALL)
