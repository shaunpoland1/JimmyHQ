"""A full app's own event kinds: declared by the app as JSON Schema, kept in ``apps.db``,
and registered in every core process that loads them.

The app's SDK sends each kind once at start (``PUT /api/app/v1/kinds``). Every kind in one
request is checked before any is kept, so a request is taken whole or not at all. A kept
``(name, v)`` never changes, and stays after the app is removed so that what it wrote on
the Tape can still be read.

``load_all(core)`` registers every kept schema in this process. Every process calls it
when it starts and whenever another process kept a new one (``apps.sync``).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.apps import dbfile, quotas
from jimmyhq.apps.manifest import Manifest
from jimmyhq.ids import now_ms
from jimmyhq.tape import registry
from jimmyhq.tape import schema as schema_check

if TYPE_CHECKING:
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.schemas")

MAX_KINDS_AT_ONCE = 100
_SHORT = re.compile(r"^[a-z][a-z0-9_]{0,40}(\.[a-z][a-z0-9_]{0,40}){0,3}\Z")



class KindRefused(ValueError):
    """A kind was not taken. The message says why, and that nothing was kept."""


@dataclass(frozen=True)
class Declared:
    name: str  # the full name
    v: int
    schema: dict[str, Any]
    example: dict[str, Any]


class KindStore:
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

    def get(self, name: str, v: int) -> Declared | None:
        with self._lock:
            row = self._db.execute("SELECT schema, example FROM kind_schemas WHERE name=? "
                                   "AND v=?", (name, v)).fetchone()
        return None if row is None else Declared(name, v, json.loads(row[0]),
                                                 json.loads(row[1]))

    def count(self, app_id: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM kind_schemas WHERE app_id=?",
                                    (app_id,)).fetchone()[0]

    def total(self) -> int:
        """How many kinds are kept, for every app: a cheap mark of whether any was added."""
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM kind_schemas").fetchone()[0]

    def all(self, app_id: str | None = None) -> list[tuple[str, Declared]]:
        sql = "SELECT app_id, name, v, schema, example FROM kind_schemas"
        params: tuple[Any, ...] = ()
        if app_id is not None:
            sql, params = sql + " WHERE app_id=?", (app_id,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY name, v", params).fetchall()
        return [(r[0], Declared(r[1], r[2], json.loads(r[3]), json.loads(r[4]))) for r in rows]

    def add(self, app_id: str, items: list[Declared]) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for d in items:
                    self._db.execute(
                        "INSERT OR IGNORE INTO kind_schemas (name, v, app_id, schema, example, "
                        "created) VALUES (?,?,?,?,?,?)",
                        (d.name, d.v, app_id, json.dumps(d.schema, sort_keys=True),
                         json.dumps(d.example, sort_keys=True), now_ms()))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise


def kind_store(core: Core) -> KindStore:
    return core.service("apps.kinds", lambda c: KindStore(quotas.state_db(c)))


# --- the rules ------------------------------------------------------------------------------


def full_kind_name(manifest: Manifest, short: Any) -> str:
    """The kind an app means by a short name: always ``app.<its id>.<short>``. An app
    cannot name a kind outside its own."""
    if not isinstance(short, str) or not _SHORT.match(short):
        raise KindRefused(
            "A kind's name is a short lower-case word such as \"drafted\" (letters, digits "
            f"and _, with at most three dots). Nothing was kept. JimmyHQ adds "
            f"\"{manifest.prefix}\" in front itself.")
    return manifest.prefix + short


def check_version(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10_000:
        raise KindRefused("A kind's version \"v\" is a whole number from 1. Nothing was kept.")
    return v


def check_kind_schema(name: str, v: int, schema: Any, example: Any) -> None:
    """The schema can be read by JimmyHQ, and the example fits it."""
    try:
        schema_check.check_schema(schema)
    except schema_check.SchemaError as exc:
        raise KindRefused(f"The schema of {name} v{v} cannot be used: {exc} Nothing was "
                          "kept.") from None
    if not isinstance(example, dict):
        raise KindRefused(f"{name} v{v} needs an example payload, an object. Nothing was "
                          "kept. A kind cannot be declared without one.")
    try:
        schema_check.validate(schema, example)
    except schema_check.PayloadError as exc:
        raise KindRefused(f"The example of {name} v{v} does not fit its own schema: {exc} "
                          "Nothing was kept.") from None


def check_unchanged(store: KindStore, d: Declared) -> bool:
    """True if this exact ``(name, v)`` is kept already. A kept version never changes."""
    have = store.get(d.name, d.v)
    if have is None:
        return False
    if have.schema == d.schema and have.example == d.example:
        return True
    raise KindRefused(f"{d.name} v{d.v} was declared before with a different schema or "
                      "example, and a declared version never changes. Nothing was kept. "
                      f"Declare the change as v{d.v + 1} and upgrade old payloads in your app.")


def check_room(core: Core, app_id: str, adding: int) -> None:
    most = core.settings.get("apps.max_kinds")
    have = kind_store(core).count(app_id)
    if have + adding > most:
        raise KindRefused(f"An app may declare at most {most} kinds of event, counting each "
                          f"version, and this one would have {have + adding}. Nothing was "
                          "kept. Ask the owner to raise \"Most kinds of event one app may "
                          "declare\" under Settings, Apps, or declare fewer.")


def read_items(manifest: Manifest, items: Any) -> list[Declared]:
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_KINDS_AT_ONCE:
        raise KindRefused(f"Send \"kinds\" as a list of 1 to {MAX_KINDS_AT_ONCE} objects "
                          "{name, v, schema, example}. Nothing was kept.")
    out: list[Declared] = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"name", "v", "schema", "example"}:
            raise KindRefused("Each kind is an object with \"name\", \"v\", \"schema\" and "
                              "\"example\" only. Nothing was kept.")
        name = full_kind_name(manifest, item.get("name"))
        v = check_version(item.get("v", 1))
        check_kind_schema(name, v, item.get("schema"), item.get("example"))
        out.append(Declared(name, v, item["schema"], item["example"]))
    if len({(d.name, d.v) for d in out}) != len(out):
        raise KindRefused("The same kind and version is listed twice. Nothing was kept.")
    return out


def declare(core: Core, manifest: Manifest, items: Any) -> list[dict[str, Any]]:
    """Check every kind in the request, keep the new ones, and register them in this
    process. All or nothing. Returns ``[{name, v, new}]``."""
    declared = read_items(manifest, items)
    store = kind_store(core)
    known = [check_unchanged(store, d) for d in declared]
    check_room(core, manifest.id, sum(1 for k in known if not k))
    for d in declared:  # what the registry would refuse, before anything is kept
        if registry.owner_of_or_none(d.name) not in (None, manifest.id):
            raise KindRefused(f"{d.name} belongs to something else. Nothing was kept.")
    store.add(manifest.id, [d for d, k in zip(declared, known, strict=True) if not k])
    for d in declared:
        _register(manifest.id, d)
    return [{"name": d.name, "v": d.v, "new": not k}
            for d, k in zip(declared, known, strict=True)]


def _register(app_id: str, d: Declared) -> None:
    try:
        registry.register_schema(d.name, d.v, schema=d.schema, example=d.example,
                                 owner=app_id)
    except ValueError as exc:
        log.warning("kind %s v%s was kept but not registered here: %s", d.name, d.v, exc)


def load_all(core: Core) -> int:
    """Register every kept schema in this process. Returns how many were new here."""
    new = 0
    for app_id, d in kind_store(core).all():
        try:
            new += registry.register_schema(d.name, d.v, schema=d.schema, example=d.example,
                                            owner=app_id)
        except ValueError as exc:
            log.warning("kind %s v%s could not be registered: %s", d.name, d.v, exc)
    return new


def kinds_of(core: Core, app_id: str) -> list[dict[str, Any]]:
    """What one app has declared: ``[{name, v}]``."""
    return [{"name": d.name, "v": d.v} for _, d in kind_store(core).all(app_id)]
