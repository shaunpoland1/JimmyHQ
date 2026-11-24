"""The inbox: how the core asks a full app's program to do something, without a port.

The app holds one long-poll, ``GET /api/app/v1/inbox?wait=25``, and is handed what is
waiting for it::

    {id, type, data, offers}      type: job | decision | tool | collect | call | settings | work
                                        | inbound

It answers each with ``POST /api/app/v1/inbox/<id>`` and ``{ok: true, result}`` or
``{ok: false, error: "<a sentence>"}``.

Deliveries are kept in ``apps.db``, so nothing is lost while an app is down or JimmyHQ
restarts. Delivery is at least once: a delivery handed over and not answered within
``apps.redeliver_s`` is handed over again, so an app keys its work on the id. An app is
only ever handed, and may only answer, its own deliveries.

Who queues what (the signatures the plug points call)::

    deliver(core, app_id, type, data, *, key=None) -> delivery id
    wait_answer(core, delivery_id, timeout_s) -> Answer | None          (a thread)
    await await_answer(core, delivery_id, timeout_s) -> Answer | None   (the event loop)
    expire(core, delivery_id)                     the asker gave up; it is not handed over
    plugs(core, app_id) -> dict                   what the app said it offers at start

``key`` keeps one delivery of a sort waiting at a time: while a delivery with the same
key is waiting or handed over and not answered, ``deliver`` returns that one's id. The
worker's job scheduler uses the job's name, so a slow job is never queued twice.

The app's long-poll is also its heartbeat (``beat``/``last_beat``).

**A message from a device** (``inbound``, queued by ``inbound.py``) is the one delivery
nobody waits on and whose data is not the core's: once the app answers it, the row goes
at once (``drop_answered_inbound``), so the body stays in this file only while it waits.
The connection deletes with ``secure_delete`` for the same reason; in WAL mode an old copy
can linger in ``apps.db-wal`` until SQLite writes over it. Such messages are handed over
after every other kind of delivery (``Inbox.take``), so a busy device delays only itself.

**Given up, still in the app's hands.** When the asker gives up on a ``tool`` delivery the
app was already handed (``expire``), the app may still be running it. Such a delivery is
``given_up``: never handed over again and never read by the asker, but still counted by
``tools_in_hand``, so the guard in ``record_effect`` does not lift at the timeout. It ends
(becomes ``expired``) when the app answers it late, when the app's program starts again
(its ``PUT /plugs`` at start, ``set_plugs``), or ``GIVEN_UP_HELD_MS`` after it was given
up, so an app that crashed mid-tool is never kept inside for good.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.apps import dbfile, quotas
from jimmyhq.apps.bridge import BridgeError
from jimmyhq.ids import now_ms, ulid

if TYPE_CHECKING:
    from jimmyhq.core import Core

TYPES = ("job", "decision", "tool", "collect", "call", "settings", "work", "inbound")
MAX_TAKE = 20
MAX_ERROR = 2000
MAX_OWNER_MESSAGE = 500  # what the shell shows of it, at most
KEEP_ANSWERED_MS = 60 * 60 * 1000  # long enough for whoever asked to read the answer
# How long a tool delivery whose asker gave up still counts as in the app's hands, at the
# most. Far above any tool timeout (``apps.tool_timeout_s`` is at most 10 minutes).
GIVEN_UP_HELD_MS = 60 * 60 * 1000
_KEY_MAX = 200


@dataclass(frozen=True)
class Answer:
    ok: bool
    result: Any = None
    error: str | None = None
    owner_message: str | None = None


def check_type(kind: Any) -> str:
    if kind not in TYPES:
        raise ValueError(f"A delivery's type is one of: {', '.join(TYPES)}.")
    return kind


def check_size(core: Core, value: Any, what: str) -> str:
    """The value as JSON, if it is within ``apps.call_max_kb``."""
    most = core.settings.get("apps.call_max_kb") * 1024
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise BridgeError("invalid", f"{what} must be plain JSON: text, numbers, true, "
                                     "false, null, lists and objects. Nothing was "
                                     "taken.") from None
    if len(text.encode("utf-8")) > most:
        raise BridgeError("too_large", f"{what} can be at most {most // 1024} KB. Nothing "
                                       "was taken. Send less, or keep it in your app's own "
                                       "database and send its id.")
    return text


class Inbox:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA secure_delete=ON")  # a device's message is overwritten
        storeversion.prepare(self._db, dbfile.STORE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add_bounded(self, app_id: str, kind: str, data: str, most: int,
                    now: int) -> str | None:
        """Queue one delivery unless ``most`` of this type already wait for this app.
        Returns its id, or None when the queue is full: nothing waiting is touched."""
        with self._lock, self._tx():
            waiting = self._db.execute(
                "SELECT COUNT(*) FROM inbox WHERE app_id=? AND type=? AND state IN "
                "('waiting', 'taken')", (app_id, kind)).fetchone()[0]
            if waiting >= most:
                return None
            delivery_id = ulid()
            self._db.execute(
                "INSERT INTO inbox (id, app_id, type, data, key, created, state) VALUES "
                "(?,?,?,?,NULL,?, 'waiting')", (delivery_id, app_id, kind, data, now))
            return delivery_id

    def count_type(self, app_id: str, kind: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM inbox WHERE app_id=? AND type=? "
                                    "AND state IN ('waiting', 'taken')",
                                    (app_id, kind)).fetchone()[0]

    def drop_answered_inbound(self, app_id: str, delivery_id: str
                              ) -> tuple[bool, str | None] | None:
        """If this is an answered ``inbound`` delivery of this app, delete it and return
        how it went (ok, error). None for any other delivery."""
        with self._lock, self._tx():
            row = self._db.execute(
                "SELECT ok, error FROM inbox WHERE id=? AND app_id=? AND type='inbound' AND "
                "state='answered'", (delivery_id, app_id)).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM inbox WHERE id=?", (delivery_id,))
        return bool(row["ok"]), row["error"]

    def _tx(self):
        return _Tx(self._db)

    def add(self, app_id: str, kind: str, data: str, key: str | None, now: int) -> str:
        with self._lock, self._tx():
            self._db.execute("DELETE FROM inbox WHERE state IN ('answered', 'expired') AND "
                             "COALESCE(answered, created) < ?", (now - KEEP_ANSWERED_MS,))
            self._db.execute("DELETE FROM inbox WHERE state='given_up' AND answered < ?",
                             (now - max(KEEP_ANSWERED_MS, GIVEN_UP_HELD_MS),))
            if key is not None:
                row = self._db.execute(
                    "SELECT id FROM inbox WHERE app_id=? AND key=? AND state IN "
                    "('waiting', 'taken')", (app_id, key)).fetchone()
                if row is not None:
                    return row["id"]
            delivery_id = ulid()
            self._db.execute(
                "INSERT INTO inbox (id, app_id, type, data, key, created, state) VALUES "
                "(?,?,?,?,?,?, 'waiting')", (delivery_id, app_id, kind, data, key, now))
            return delivery_id

    def take(self, app_id: str, limit: int, redeliver_ms: int, now: int) -> list[dict]:
        """Oldest first, but messages from devices after everything else: a device that
        keeps the queue full must not hold up the app's jobs and the owner's calls."""
        with self._lock, self._tx():
            rows = self._db.execute(
                "SELECT id, type, data, offers FROM inbox WHERE app_id=? AND (state='waiting' "
                "OR (state='taken' AND offered <= ?)) ORDER BY type='inbound', created, id "
                "LIMIT ?",
                (app_id, now - redeliver_ms, limit)).fetchall()
            for row in rows:
                self._db.execute("UPDATE inbox SET state='taken', offered=?, offers=offers+1 "
                                 "WHERE id=?", (now, row["id"]))
        return [{"id": r["id"], "type": r["type"], "data": json.loads(r["data"]),
                 "offers": r["offers"] + 1} for r in rows]

    def row(self, delivery_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute("SELECT * FROM inbox WHERE id=?",
                                    (delivery_id,)).fetchone()

    def settle(self, app_id: str, delivery_id: str, ok: bool, result: str | None,
               error: str | None, now: int, owner_error: str | None = None) -> str:
        """Record an answer. Returns the delivery's state before it: "taken" if this
        answer was taken, otherwise what it was ("answered", "expired", "waiting", or
        "missing" when there is no such delivery for this app)."""
        with self._lock, self._tx():
            row = self._db.execute("SELECT state FROM inbox WHERE id=? AND app_id=?",
                                   (delivery_id, app_id)).fetchone()
            if row is None:
                return "missing"
            if row["state"] == "given_up":  # answered late: no longer in the app's hands
                self._db.execute("UPDATE inbox SET state='expired' WHERE id=?",
                                 (delivery_id,))
                return "expired"
            if row["state"] != "taken":
                return row["state"]
            self._db.execute("UPDATE inbox SET state='answered', answered=?, ok=?, result=?, "
                             "error=?, owner_error=? WHERE id=?",
                             (now, int(ok), result, error, owner_error, delivery_id))
            return "taken"

    def expire(self, delivery_id: str, now: int) -> None:
        """The asker gave up. A tool the app was already handed is ``given_up``: it may
        still be running it. Anything else is ``expired``."""
        with self._lock:
            self._db.execute("UPDATE inbox SET state=CASE WHEN state='taken' AND type='tool' "
                             "THEN 'given_up' ELSE 'expired' END, answered=? WHERE id=? AND "
                             "state IN ('waiting', 'taken')", (now, delivery_id))

    def count(self, app_id: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM inbox WHERE app_id=? AND state IN "
                                    "('waiting', 'taken')", (app_id,)).fetchone()[0]

    def beat(self, app_id: str, now: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO heartbeats (app_id, at) VALUES (?, ?) ON CONFLICT "
                             "(app_id) DO UPDATE SET at=excluded.at", (app_id, now))

    def last_beat(self, app_id: str) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT at FROM heartbeats WHERE app_id=?",
                                   (app_id,)).fetchone()
        return None if row is None else row["at"]

    def set_plugs(self, app_id: str, plugs: dict[str, Any], now: int) -> None:
        """What the program offers, said at its start. A program that starts holds none
        of the tools an earlier run of it was given up on."""
        with self._lock, self._tx():
            self._db.execute("INSERT INTO plugs (app_id, plugs, at) VALUES (?,?,?) ON "
                             "CONFLICT (app_id) DO UPDATE SET plugs=excluded.plugs, "
                             "at=excluded.at", (app_id, json.dumps(plugs), now))
            self._db.execute("UPDATE inbox SET state='expired' WHERE app_id=? AND "
                             "state='given_up'", (app_id,))

    def plugs(self, app_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT plugs FROM plugs WHERE app_id=?",
                                   (app_id,)).fetchone()
        return {} if row is None else json.loads(row["plugs"])

    # --- roles: what a record tool's guard asks (apps/record_effect.py) ---
    def tools_in_hand(self, app_id: str, now: int | None = None) -> list[dict[str, Any]]:
        """The tool deliveries this app has been handed and not yet answered, including
        ones whose asker gave up within ``GIVEN_UP_HELD_MS``."""
        now = now_ms() if now is None else now
        with self._lock:
            rows = self._db.execute("SELECT data FROM inbox WHERE app_id=? AND type='tool' "
                                    "AND (state='taken' OR (state='given_up' AND answered "
                                    "> ?))", (app_id, now - GIVEN_UP_HELD_MS)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def forget(self, app_id: str) -> None:
        with self._lock, self._tx():
            for table in ("inbox", "heartbeats", "plugs"):
                self._db.execute(f"DELETE FROM {table} WHERE app_id=?", (app_id,))  # noqa: S608


class _Tx:
    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind: Any, *_: Any) -> None:
        self._db.execute("ROLLBACK" if kind else "COMMIT")


def inbox(core: Core) -> Inbox:
    return core.service("apps.inbox", lambda c: Inbox(quotas.state_db(c)))


# --- what the core calls ---------------------------------------------------------------------


def deliver(core: Core, app_id: str, kind: str, data: Any, *, key: str | None = None) -> str:
    """Queue one delivery for an app. Returns its id (an earlier one's, for a ``key`` that
    is still unfinished)."""
    check_type(kind)
    if key is not None and (not isinstance(key, str) or not 0 < len(key) <= _KEY_MAX):
        raise ValueError(f"A delivery's key is text of 1 to {_KEY_MAX} characters.")
    text = check_size(core, data, "What is handed to an app")
    return inbox(core).add(app_id, kind, text, key, now_ms())


def take(core: Core, app_id: str, *, limit: int = MAX_TAKE, now: int | None = None) -> list[dict]:
    """Hand an app what is waiting for it, and what it was handed long enough ago without
    answering. Counts as the app's heartbeat."""
    now = now_ms() if now is None else now
    box = inbox(core)
    box.beat(app_id, now)
    redeliver_ms = core.settings.get("apps.redeliver_s") * 1000
    return box.take(app_id, max(1, min(limit, MAX_TAKE)), redeliver_ms, now)


def check_answer(core: Core, ok: Any, result: Any, error: Any) -> tuple[bool, str | None,
                                                                       str | None]:
    """An answer is ``{ok: true, result}`` or ``{ok: false, error: "<sentence>"}``."""
    if not isinstance(ok, bool):
        raise BridgeError("invalid", "An answer says \"ok\": true or false. Nothing was "
                                     "taken.")
    if ok:
        if error is not None:
            raise BridgeError("invalid", "An answer with \"ok\": true carries \"result\", "
                                         "not \"error\". Nothing was taken.")
        return True, check_size(core, result, "An answer"), None
    if not isinstance(error, str) or not error.strip() or len(error) > MAX_ERROR:
        raise BridgeError("invalid", "An answer with \"ok\": false carries \"error\": one "
                                     f"sentence of at most {MAX_ERROR} characters that says "
                                     "what went wrong. Nothing was taken.")
    return False, None, error


_ISSUED: OrderedDict[str, None] = OrderedDict()
_ISSUED_LOCK = threading.Lock()
_ISSUED_KEEP = 200


def issued_to_a_program(words: str | None) -> None:
    """Note a sentence JimmyHQ wrote for the owner and gave to an app's program in a refusal
    (``api.failed`` calls this). Only such a sentence is passed on to a page later."""
    if not isinstance(words, str) or not words or len(words) > MAX_OWNER_MESSAGE:
        return
    with _ISSUED_LOCK:
        _ISSUED[words] = None
        _ISSUED.move_to_end(words)
        while len(_ISSUED) > _ISSUED_KEEP:
            _ISSUED.popitem(last=False)


def check_owner_message(value: Any) -> str | None:
    """The words JimmyHQ wrote for the owner that a program passes on with a failed answer,
    or nothing. Only a sentence JimmyHQ itself gave a program in a refusal is kept: what a
    program writes of its own is never shown to the owner as JimmyHQ's words. (The answer
    itself is taken either way.)"""
    if not isinstance(value, str):
        return None
    with _ISSUED_LOCK:
        return value if value in _ISSUED else None


def answer(core: Core, app_id: str, delivery_id: Any, *, ok: Any, result: Any = None,
           error: Any = None, owner_message: Any = None) -> bool:
    """Record an app's answer to one of its own deliveries. True if it was taken, False if
    that delivery was answered already or its asker gave up (an answer that came twice is
    not an error: delivery is at least once). Refuses a delivery that is not this app's or
    was never handed over."""
    if not isinstance(delivery_id, str) or len(delivery_id) > 40:
        raise BridgeError("not_found", "There is no delivery with that id for your app. "
                                       "Nothing was taken.")
    done, text, error_text = check_answer(core, ok, result, error)
    box = inbox(core)
    words = None if done else check_owner_message(owner_message)
    before = box.settle(app_id, delivery_id, done, text, error_text, now_ms(), words)
    if before == "taken":
        went = box.drop_answered_inbound(app_id, delivery_id)
        if went is not None:
            from jimmyhq.apps import inbound  # noqa: PLC0415 - it imports this module

            inbound.handled(core, app_id, ok=went[0], error=went[1])
    if before == "missing":
        raise BridgeError("not_found", "There is no delivery with that id for your app. "
                                       "Nothing was taken. Answer only what the inbox "
                                       "handed you.")
    if before == "waiting":
        raise BridgeError("invalid", "That delivery has not been handed to your app yet. "
                                     "Nothing was taken. Ask the inbox for it first.")
    return before == "taken"


def answer_of(core: Core, delivery_id: str) -> Answer | None:
    row = inbox(core).row(delivery_id)
    if row is None or row["state"] != "answered":
        return None
    return Answer(bool(row["ok"]), json.loads(row["result"]) if row["result"] else None,
                  row["error"], row["owner_error"])


def wait_answer(core: Core, delivery_id: str, timeout_s: float) -> Answer | None:
    """Block this thread until the delivery is answered, or None after ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    pause = 0.05
    while True:
        found = answer_of(core, delivery_id)
        if found is not None or time.monotonic() >= deadline:
            return found
        time.sleep(min(pause, max(0.0, deadline - time.monotonic())))
        pause = min(pause * 2, 0.5)


async def await_answer(core: Core, delivery_id: str, timeout_s: float) -> Answer | None:
    """As ``wait_answer``, without holding a thread."""
    deadline = time.monotonic() + timeout_s
    pause = 0.05
    while True:
        found = answer_of(core, delivery_id)
        if found is not None or time.monotonic() >= deadline:
            return found
        await asyncio.sleep(min(pause, max(0.0, deadline - time.monotonic())))
        pause = min(pause * 2, 0.5)


def expire(core: Core, delivery_id: str) -> None:
    """Whoever asked gave up: the delivery is not handed over again."""
    inbox(core).expire(delivery_id, now_ms())


def pending(core: Core, app_id: str) -> int:
    return inbox(core).count(app_id)


def last_beat(core: Core, app_id: str) -> int | None:
    """When the app last asked for work, in unix ms, or None if it never has."""
    return inbox(core).last_beat(app_id)


def responding(core: Core, app_id: str, *, now: int | None = None) -> bool:
    """Whether the app has asked for work within ``apps.not_responding_s``."""
    beat = last_beat(core, app_id)
    now = now_ms() if now is None else now
    return beat is not None and now - beat <= core.settings.get("apps.not_responding_s") * 1000


def set_plugs(core: Core, app_id: str, plugs: dict[str, Any]) -> None:
    inbox(core).set_plugs(app_id, plugs, now_ms())


def plugs(core: Core, app_id: str) -> dict[str, Any]:
    """What the app said it offers at its last start: ``{jobs, decisions, calls, tools,
    sources}``. Empty until it has started once."""
    return inbox(core).plugs(app_id)


def forget(core: Core, app_id: str) -> None:
    """Drop everything about an app that is being removed."""
    inbox(core).forget(app_id)
