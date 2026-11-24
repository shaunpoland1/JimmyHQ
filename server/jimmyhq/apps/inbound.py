"""Messages from the owner's devices: a device or service on the owner's network (a home's
hub, say) sends a small JSON object to one full app::

    POST /api/in/<app>/<name>      Authorization: Bearer <key>     -> 202 {"ok": true}
    POST /api/in/<app>/<name>/k/<key>                              -> the same

The second form is for a sender that can only be given an address (a pendant's webhook, a
service's "notify this URL" box). It is narrow too:

- ``inbound_address_key_opt_in``: only a key the owner made for that (``in_address``) is
  taken from the address; any other key there gets the one refusal. A key made for the
  address works in the header as well.
- ``inbound_address_key_never_kept``: the key in the address is used to look the key up and
  is then dropped. The access log is written without it (``cli._NoQueryStrings``), every
  answer from the door says ``Referrer-Policy: no-referrer``, and the app, the Tape and the
  receipts get the key's label, never the key.
- ``inbound_query_is_the_senders``: what the sender adds after ``?`` is handed to the app as
  ``query`` (first value of each name wins), at most ``apps.inbound_query_max`` names of at
  most ``apps.inbound_query_chars`` each, or the message is refused. Anything that carries
  one of our keys is dropped first, so a key never reaches an app.
- ``inbound_array_when_declared``: a JSON list is taken only for a name whose manifest entry
  says ``"array": true``; otherwise one JSON object, as always.
- ``inbound_max_kb_is_what_the_name_expects``: a name may say ``"max_kb"``, and that is how
  much is read for it, up to the owner's ``apps.inbound_max_kb`` ceiling; a name that does
  not say is held to ``apps.inbound_default_kb``.

For every form the door is narrow:

- ``inbound_one_refusal``: a wrong or missing key, an unknown app, an app that does not
  ask for ``inbound``, an unknown name, a switched-off app, an app the owner has not opened
  and the whole door switched off all get the same 401 with the same body. The key is
  looked up first, by its digest, and only a key's own app is ever looked at, so how long
  the answer takes does not depend on which app the address names.
- ``inbound_key_is_one_apps``: a key opens one app, for every name it declares.
- ``inbound_keys_hashed``: only a key's sha256 is kept; the key is returned once.
- ``inbound_paced``: ``apps.inbound_per_minute`` per key, and ``apps.inbound_bad_per_minute``
  refusals per source address, after which what that address sends without a right key is
  turned away for ``apps.inbound_block_s`` (with no key at all, before anything is looked
  up). A right key is never turned away for its address: behind a proxy on this computer
  every caller has the same one, and a stranger must not lock the owner's devices out.
  Both are counted in this process's memory, bounded.
- ``inbound_queue_bounded``: at most ``apps.inbound_queue_max`` messages wait for one app;
  past that the sender is told to wait, and nothing already waiting is dropped.
- ``inbound_body_kept_in_the_app``: the body goes into the app's inbox and nowhere else.
  The Tape, the receipts and the logs get the app, the name, the key's label, the time,
  the size and the outcome. Whatever the app's handler returns is never sent back.

Keys, the owner's switch, the counts and the receipts are tables in ``apps.db``
(``dbfile``). The routes are in ``inbound_routes``; the owner's page reads ``state``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.apps import bridge, dbfile, inbox, kinds, quotas
from jimmyhq.apps.permissions import FULL, allows
from jimmyhq.apps.store import AppError
from jimmyhq.ids import now_ms, ulid

if TYPE_CHECKING:
    from jimmyhq.core import Core

PREFIX = "/api/in/"
PERMISSION = "inbound"
KEY_PREFIX = "jhqi_"  # noqa: S105 - a prefix, not a secret
MAX_KEY = 200  # a longer string offered as a key is not looked at
LABEL_MAX = 60
ERROR_MAX = 300
WINDOW_S = 60
MAX_SOURCES = 5000  # addresses remembered at once
MAX_ALL_KNOCKS = 20_000  # refusals from all addresses remembered at once, for the ceiling
MAX_KEYS_PACED = 5000  # keys whose minute is remembered at once
REFUSAL_RECEIPT_S = 60  # the same refusal of one key is kept at most once a minute
SIZE_MAX = 2**53  # a size given in a receipt; a declared one larger than any body is cut

QUEUED = "queued"
OUTCOMES = (QUEUED, "too_many", "wrong_type", "too_large", "not_json", "not_object",
            "queue_full", "bad_query")
ADDRESS_MARK = "k"  # /api/in/<app>/<name>/k/<key>: the segment before a key in the address


def digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "surrogatepass")).hexdigest()


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


# --- what is kept ------------------------------------------------------------------------------


class Store:
    """The keys, each app's switch and counts, and the receipts, in ``apps.db``."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        storeversion.prepare(self._db, dbfile.STORE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _tx(self) -> _Tx:
        return _Tx(self._db)

    def keys(self, app_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, label, created, last_used, uses, in_address FROM inbound_keys "
                "WHERE app_id=? ORDER BY created, id", (app_id,)).fetchall()
        return [{**dict(r), "in_address": bool(r["in_address"])} for r in rows]

    def add_key(self, app_id: str, label: str, key_digest: str, most: int,
                now: int, *, in_address: bool = False) -> str | None:
        """Keep a new key's digest. None if the app has ``most`` keys already. The first
        key an app is given opens it. ``in_address``: it may be sent in the address."""
        with self._lock, self._tx():
            have = self._db.execute("SELECT COUNT(*) FROM inbound_keys WHERE app_id=?",
                                    (app_id,)).fetchone()[0]
            if have >= most:
                return None
            key_id = ulid()
            self._db.execute(
                "INSERT INTO inbound_keys (id, app_id, label, digest, created, in_address) "
                "VALUES (?,?,?,?,?,?)", (key_id, app_id, label, key_digest, now,
                                         int(in_address)))
            if have == 0:
                self._db.execute(
                    "INSERT INTO inbound_apps (app_id, open) VALUES (?, 1) ON CONFLICT "
                    "(app_id) DO UPDATE SET open=1", (app_id,))
            return key_id

    def remove_key(self, app_id: str, key_id: str) -> str | None:
        """Delete one key of this app. Returns its label, or None if there was none."""
        with self._lock, self._tx():
            row = self._db.execute("SELECT label FROM inbound_keys WHERE id=? AND app_id=?",
                                   (key_id, app_id)).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM inbound_keys WHERE id=?", (key_id,))
            return str(row["label"])

    def find(self, key_digest: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT k.id, k.app_id, k.label, k.in_address, COALESCE(a.open, 0) AS open FROM "
                "inbound_keys k LEFT JOIN inbound_apps a ON a.app_id = k.app_id WHERE "
                "k.digest=?", (key_digest,)).fetchone()
        return None if row is None else dict(row)

    def app(self, app_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT * FROM inbound_apps WHERE app_id=?",
                                   (app_id,)).fetchone()
        if row is None:
            return {"open": False, "received": 0, "failed": 0, "last_error": None,
                    "last_error_at": None}
        found = dict(row)
        found["open"] = bool(found["open"])
        return found

    def set_open(self, app_id: str, is_open: bool) -> bool:
        """Set the switch. True if it changed."""
        with self._lock, self._tx():
            row = self._db.execute("SELECT open FROM inbound_apps WHERE app_id=?",
                                   (app_id,)).fetchone()
            if row is not None and bool(row["open"]) == is_open:
                return False
            if row is None and not is_open:
                return False
            self._db.execute("INSERT INTO inbound_apps (app_id, open) VALUES (?, ?) ON "
                             "CONFLICT (app_id) DO UPDATE SET open=excluded.open",
                             (app_id, int(is_open)))
            return True

    def accepted(self, app_id: str, key_id: str, now: int) -> None:
        with self._lock, self._tx():
            self._db.execute("UPDATE inbound_keys SET uses=uses+1, last_used=? WHERE id=?",
                             (now, key_id))
            self._db.execute("UPDATE inbound_apps SET received=received+1 WHERE app_id=?",
                             (app_id,))

    def handled(self, app_id: str, ok: bool, error: str | None, now: int) -> None:
        if ok:
            return
        with self._lock:
            self._db.execute(
                "UPDATE inbound_apps SET failed=failed+1, last_error=?, last_error_at=? "
                "WHERE app_id=?", (error, now, app_id))

    def receipt(self, app_id: str, name: str, label: str, at: int, size: int,
                outcome: str, kept: int) -> None:
        with self._lock, self._tx():
            self._db.execute(
                "INSERT INTO inbound_receipts (app_id, name, key_label, at, size, outcome) "
                "VALUES (?,?,?,?,?,?)", (app_id, name, label, at, size, outcome))
            self._db.execute(
                "DELETE FROM inbound_receipts WHERE app_id=? AND n <= (SELECT n FROM "
                "inbound_receipts WHERE app_id=? ORDER BY n DESC LIMIT 1 OFFSET ?)",
                (app_id, app_id, kept))

    def receipts(self, app_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT name, key_label, at, size, outcome FROM inbound_receipts WHERE "
                "app_id=? ORDER BY n DESC LIMIT ?", (app_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def close_app(self, app_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE inbound_apps SET open=0 WHERE app_id=?", (app_id,))

    def forget(self, app_id: str) -> None:
        with self._lock, self._tx():
            for table in ("inbound_keys", "inbound_apps", "inbound_receipts"):
                self._db.execute(f"DELETE FROM {table} WHERE app_id=?", (app_id,))  # noqa: S608


class _Tx:
    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def __enter__(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind: Any, *_: Any) -> None:
        self._db.execute("ROLLBACK" if kind else "COMMIT")


# --- what is counted in memory -----------------------------------------------------------------


class Knocks:
    """Rule ``inbound_paced``, for strangers: refusals per source address over a minute.
    ``most`` of them and the address is turned away for ``block_s``. Nothing is written
    for a refusal of a stranger, so knocking cannot grow any file.

    Over all addresses together there is a ceiling too (``apps.inbound_bad_all_per_minute``):
    past it every message without a right key is turned away until the minute has moved
    on, so a stranger who comes from a fresh address each time is held as well. Addresses
    whose count or block has run out are forgotten first, so fresh ones cannot push a live
    block out of memory. A right key is looked up before any of this and always passes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()
        self._until: OrderedDict[str, float] = OrderedDict()
        self._all: deque[float] = deque(maxlen=MAX_ALL_KNOCKS)

    def blocked(self, source: str, now: float, ceiling: int | None = None) -> float:
        """Seconds the address is still turned away for, or 0."""
        with self._lock:
            wait = 0.0
            until = self._until.get(source, 0.0)
            if until <= now:
                self._until.pop(source, None)
            else:
                wait = until - now
            if ceiling is not None:
                while self._all and self._all[0] <= now - WINDOW_S:
                    self._all.popleft()
                if len(self._all) >= ceiling:
                    wait = max(wait, self._all[0] + WINDOW_S - now)
            return wait

    def _forget_stale(self, now: float) -> None:
        while self._until and next(iter(self._until.values())) <= now:
            self._until.popitem(last=False)
        while self._seen:
            tries = next(iter(self._seen.values()))
            if tries and tries[-1] > now - WINDOW_S:
                break
            self._seen.popitem(last=False)

    def record(self, source: str, most: int, block_s: float, now: float) -> None:
        with self._lock:
            self._all.append(now)
            tries = self._seen.get(source)
            if tries is None:
                self._forget_stale(now)
                tries = self._seen[source] = deque()
            self._seen.move_to_end(source)
            while tries and tries[0] <= now - WINDOW_S:
                tries.popleft()
            tries.append(now)
            if len(tries) >= most:
                self._until[source] = now + block_s
                self._until.move_to_end(source)
                tries.clear()
            while len(self._seen) > MAX_SOURCES:
                self._seen.popitem(last=False)
            while len(self._until) > MAX_SOURCES:
                self._until.popitem(last=False)

    def remembered(self) -> int:
        with self._lock:
            return len(self._seen) + len(self._until)


class Pace:
    """Rule ``inbound_paced``, for keys: messages per key over the last minute."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()
        self._refused_at: OrderedDict[tuple[str, str], float] = OrderedDict()

    def allow(self, key_id: str, most: int, now: float) -> bool:
        with self._lock:
            seen = self._seen.get(key_id)
            if seen is None:
                seen = self._seen[key_id] = deque()
            self._seen.move_to_end(key_id)
            while len(self._seen) > MAX_KEYS_PACED:
                self._seen.popitem(last=False)
            while seen and seen[0] <= now - WINDOW_S:
                seen.popleft()
            if len(seen) >= most:
                return False
            seen.append(now)
            return True

    def worth_a_receipt(self, key_id: str, outcome: str, now: float) -> bool:
        """A refusal of one key for one reason is kept at most once a minute, so a device
        stuck in a loop cannot fill the receipts with one line."""
        with self._lock:
            mark = (key_id, outcome)
            if now - self._refused_at.get(mark, float("-inf")) < REFUSAL_RECEIPT_S:
                return False
            self._refused_at[mark] = now
            self._refused_at.move_to_end(mark)
            while len(self._refused_at) > MAX_KEYS_PACED:
                self._refused_at.popitem(last=False)
            return True


class Service:
    def __init__(self, core: Core):
        self.core = core
        self.store = Store(quotas.state_db(core))
        self.knocks = Knocks()
        self.pace = Pace()
        self.clock = time.monotonic
        self.now_ms = now_ms

    def close(self) -> None:
        self.store.close()


def service(core: Core) -> Service:
    return core.service("apps.inbound", Service)


# --- who is sending ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Admitted:
    app_id: str
    name: str
    key_id: str
    key_label: str
    array: bool = False  # the name takes a JSON list as well as an object
    max_kb: int | None = None  # the largest message the name expects, if it says


def declared(core: Core, app_id: str) -> tuple[Any, ...]:
    """The inbound names the installed version of an app declares, or ()."""
    found = bridge.find_app(core, app_id)
    return found.manifest.inbound if found is not None else ()


def admit(core: Core, app_id: Any, name: Any, key: str | None, *,
          from_address: bool = False) -> Admitted | None:
    """Rules ``inbound_one_refusal``, ``inbound_key_is_one_apps`` and
    ``inbound_address_key_opt_in``: who a message is from and which app it is for, or None.
    The key is looked up before anything about the named app, and only the key's own app is
    looked at. ``from_address``: the key came in the address, which only a key made for
    that may."""
    svc = service(core)
    offered = key if isinstance(key, str) and 0 < len(key) <= MAX_KEY else ""
    row = svc.store.find(digest(offered))  # done for every request, a key or not
    if row is None or not offered:
        return None
    if from_address and not row["in_address"]:
        return None
    if not hmac.compare_digest(str(row["app_id"]).encode(), str(app_id).encode()[:64]):
        return None
    if not core.settings.get("apps.inbound_enabled") or not row["open"]:
        return None
    app = bridge.find_app(core, row["app_id"])
    if app is None or not app.enabled or app.manifest.tier != FULL:
        return None
    if not allows(app.granted, PERMISSION):
        return None
    entry = next((i for i in app.manifest.inbound if i.name == name), None) \
        if isinstance(name, str) else None
    if entry is None:
        return None
    return Admitted(row["app_id"], name, row["id"], row["label"], array=entry.array,
                    max_kb=entry.max_kb)


def largest_kb(core: Core, who: Admitted) -> int:
    """Rule ``inbound_max_kb_is_what_the_name_expects``: the most read for one message, in KB.
    A name that says what it expects is allowed that much, a name that does not is allowed
    the owner's ``apps.inbound_default_kb``, and neither is ever more than the owner's
    ``apps.inbound_max_kb`` ceiling."""
    ceiling = int(core.settings.get("apps.inbound_max_kb"))
    wanted = who.max_kb or int(core.settings.get("apps.inbound_default_kb"))
    return min(ceiling, wanted)


def ours(text: str) -> bool:
    """Whether a part of an address carries one of our keys."""
    return KEY_PREFIX in text


def sender_query(items: list[tuple[str, str]], most: int, chars: int) -> dict[str, str] | None:
    """Rule ``inbound_query_is_the_senders``: the parameters the sender added to the address,
    the first value of each name, or None when there are more than ``most`` names or one is
    longer than ``chars``. Any that carries one of our keys is dropped first."""
    out: dict[str, str] = {}
    for name, value in items:
        if ours(name) or ours(value):
            continue
        if len(name) > chars or len(value) > chars:
            return None
        if name not in out:
            if len(out) >= most:
                return None
            out[name] = value
    return out


def address_paths(app_id: str, names: Any, key: str) -> dict[str, str]:
    """Where a sender that can only be given an address sends each name, the key in it."""
    return {i.name: f"{PREFIX}{app_id}/{i.name}/{ADDRESS_MARK}/{key}" for i in names}


def receipt(core: Core, who: Admitted, outcome: str, size: int) -> None:
    """A line for the owner's page: never the body, only how large it was."""
    svc = service(core)
    if outcome != QUEUED and not svc.pace.worth_a_receipt(who.key_id, outcome, svc.clock()):
        return
    kept = int(core.settings.get("apps.inbound_receipts_max"))
    svc.store.receipt(who.app_id, who.name, who.key_label, svc.now_ms(),
                      max(0, min(int(size), SIZE_MAX)), outcome, kept)


def queue(core: Core, who: Admitted, body: dict[str, Any] | list[Any], size: int,
          query: dict[str, str] | None = None) -> str | None:
    """Rule ``inbound_queue_bounded``: hand the message to the app's inbox. The delivery's
    id, or None when the app's queue is full. ``BridgeError`` (too_large) if the message
    with its envelope is larger than an inbox delivery may be. ``query`` is said only when
    the sender added some, so an app's messages read as before otherwise."""
    svc = service(core)
    received = svc.now_ms()
    envelope: dict[str, Any] = {"name": who.name, "body": body, "received_ms": received,
                                "key_name": who.key_label}
    if query:
        envelope["query"] = query
    text = inbox.check_size(core, envelope, "A message from a device")
    most = int(core.settings.get("apps.inbound_queue_max"))
    delivery = inbox.inbox(core).add_bounded(who.app_id, "inbound", text, most, received)
    if delivery is None:
        receipt(core, who, "queue_full", size)
        return None
    svc.store.accepted(who.app_id, who.key_id, received)
    receipt(core, who, QUEUED, size)
    return delivery


def handled(core: Core, app_id: str, *, ok: bool, error: str | None) -> None:
    """The app answered one message: a failure is counted, with the app's own sentence
    (cut short), for its page. Called by ``inbox.answer``."""
    sentence = None if ok else (error or "The app said it failed.")[:ERROR_MAX]
    service(core).store.handled(app_id, ok, sentence, now_ms())


# --- the owner's side --------------------------------------------------------------------------


def _need_inbound(core: Core, app_id: str) -> Any:
    found = bridge.find_app(core, app_id)
    if found is None:
        raise AppError(404, f"There is no app called \"{str(app_id)[:40]}\" installed. Nothing "
                            "was changed. The Apps screen lists what is installed.")
    if found.manifest.tier != FULL or not found.manifest.inbound:
        raise AppError(422, f"{found.manifest.name} does not take messages from devices, so "
                            "it has no keys. Nothing was changed.")
    if not allows(found.granted, PERMISSION):
        raise AppError(409, f"{found.manifest.name} was not given \"inbound\" when you "
                            "installed it. Nothing was changed. Update it to agree to it.")
    return found


def check_label(value: Any, have: list[dict[str, Any]]) -> str:
    """A key's label: the owner's name for the device it is for."""
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    if not text or len(text) > LABEL_MAX or any(
            unicodedata.category(ch).startswith("C") for ch in text):
        raise AppError(422, f"Give the key a name of 1 to {LABEL_MAX} letters, such as "
                            "\"Home hub\", so you know which device holds it. No key was "
                            "made.")
    if any(k["label"].casefold() == text.casefold() for k in have):
        raise AppError(409, f"This app already has a key called \"{text}\". No key was made. "
                            "Give each device's key its own name.")
    return text


def _declared(entry: Any) -> dict[str, Any]:
    """One name as the page shows it: ``array`` and ``max_kb`` only where the app said them."""
    out: dict[str, Any] = {"name": entry.name, "label": entry.label, "help": entry.help}
    if entry.array:
        out["array"] = True
    if entry.max_kb is not None:
        out["max_kb"] = entry.max_kb
    return out


def state(core: Core, app_id: str) -> dict[str, Any]:
    """What the app's page shows: the names it takes, the switch, the keys (labels and
    times, never a key) and the counts. ``declared`` is empty for an app that takes none."""
    found = bridge.find_app(core, app_id)
    if found is None:
        raise AppError(404, f"There is no app called \"{str(app_id)[:40]}\" installed. "
                            "The Apps screen lists what is installed.")
    names = found.manifest.inbound if found.manifest.tier == FULL else ()
    svc = service(core)
    kept = svc.store.app(app_id)
    return {
        "declared": [_declared(i) for i in names],
        "max_kb": int(core.settings.get("apps.inbound_max_kb")),
        "default_kb": int(core.settings.get("apps.inbound_default_kb")),
        "granted": allows(found.granted, PERMISSION),
        "everywhere": bool(core.settings.get("apps.inbound_enabled")),
        "open": kept["open"],
        "path": f"{PREFIX}{app_id}/",
        "keys": svc.store.keys(app_id) if names else [],
        "keys_max": int(core.settings.get("apps.inbound_keys_max")),
        "received": int(kept["received"]),
        "failed": int(kept["failed"]),
        "last_error": kept["last_error"],
        "last_error_at": kept["last_error_at"],
        "waiting": inbox.inbox(core).count_type(app_id, "inbound") if names else 0,
        "recent": svc.store.receipts(app_id) if names else [],
    }


def make_key(core: Core, app_id: str, label: Any, *, actor: str,
             in_address: Any = False) -> tuple[dict[str, Any], str]:
    """Rule ``inbound_keys_hashed``: a new key for one app, returned this once. Only its
    digest is kept. The first key opens the app to messages. ``in_address``: the owner made
    it for a sender that can only be given an address (``inbound_address_key_opt_in``)."""
    if not isinstance(in_address, bool):
        raise AppError(422, "Say \"in_address\": true or false. No key was made.")
    found = _need_inbound(core, app_id)
    svc = service(core)
    text = check_label(label, svc.store.keys(app_id))
    most = int(core.settings.get("apps.inbound_keys_max"))
    key = new_key()
    was_open = svc.store.app(app_id)["open"]
    key_id = svc.store.add_key(app_id, text, digest(key), most, now_ms(),
                               in_address=in_address)
    if key_id is None:
        raise AppError(409, f"{found.manifest.name} has {most} keys, the most it may have. No "
                            "key was made. Remove one you no longer use, or raise the limit "
                            "under Settings, Apps.")
    core.tape.print("apps.inbound_key_made", {"app_id": app_id, "key_id": key_id,
                                              "label": text, "in_address": in_address},
                    actor=actor, subject=kinds.subject(app_id))
    if not was_open and svc.store.app(app_id)["open"]:
        core.tape.print("apps.inbound_switched", {"app_id": app_id, "open": True},
                        actor=actor, subject=kinds.subject(app_id))
    return state(core, app_id), key


def remove_key(core: Core, app_id: str, key_id: Any, *, actor: str) -> dict[str, Any]:
    """The key stops working with this call."""
    label = service(core).store.remove_key(app_id, key_id) if isinstance(key_id, str) \
        else None
    if label is None:
        raise AppError(404, "That key is not one of this app's. Nothing was removed. Reload "
                            "the page to see its keys.")
    core.tape.print("apps.inbound_key_removed", {"app_id": app_id, "key_id": key_id,
                                                 "label": label},
                    actor=actor, subject=kinds.subject(app_id))
    return state(core, app_id)


def set_open(core: Core, app_id: str, is_open: Any, *, actor: str) -> dict[str, Any]:
    if not isinstance(is_open, bool):
        raise AppError(422, "Say \"open\": true or false. Nothing was changed.")
    _need_inbound(core, app_id)
    if service(core).store.set_open(app_id, is_open):
        core.tape.print("apps.inbound_switched", {"app_id": app_id, "open": is_open},
                        actor=actor, subject=kinds.subject(app_id))
    return state(core, app_id)


def on_removed(core: Core, app_id: str, *, keep_data: bool) -> None:
    """An app was removed. With its data deleted, its keys, counts and receipts go too. With
    its data kept, its keys stay but the door is switched off, so installing the app again
    does not open it without the owner's click."""
    store = service(core).store
    if keep_data:
        store.close_app(app_id)
    else:
        store.forget(app_id)


__all__ = ["ADDRESS_MARK", "PREFIX", "Admitted", "address_paths", "admit", "handled",
           "largest_kb", "make_key", "on_removed", "queue", "remove_key", "sender_query",
           "service", "set_open", "state"]
