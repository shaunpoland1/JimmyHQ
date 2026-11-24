"""A key that goes only to the address the owner typed it for.

An app that sends a key to an address named in a setting (a server on the owner's own
network, which JimmyHQ's per-host secrets do not cover) must not send it somewhere else when
the setting changes. ``KeyBinding`` remembers which address a key was first sent to and
refuses to send the same key anywhere else:

    bound = KeyBinding(app, "stats")
    if key and not bound.allows(key, "http://192.0.2.10:3001"):
        ...  # tell the owner: type the key again to use it with this address

- The first time a key is seen it is bound to the address it is sent to.
- The same key at another address is refused, and stays bound to the first.
- A different key (the owner cleared it and typed another, or the same one again after
  ``forget``) is bound afresh to the address it is sent to.

What is kept is a fingerprint of the key (a hash, never the key) and the address, in the
app's own data folder. A key on a host the owner added under Your own addresses needs none of
this: ``app.secrets.get(name, host=host)`` is bound to that host by JimmyHQ.
"""

from __future__ import annotations

import hashlib
from typing import Any

__all__ = ["KeyBinding"]

_TABLE = "CREATE TABLE IF NOT EXISTS keybind (name TEXT PRIMARY KEY, fp TEXT NOT NULL, " \
         "origin TEXT NOT NULL)"


def _fingerprint(key: str) -> str:
    return hashlib.sha256(b"jimmyhq-keybind\0" + key.encode()).hexdigest()[:24]


class KeyBinding:
    def __init__(self, app: Any, name: str):
        self._app, self._name = app, name

    def _open(self) -> Any:
        db = self._app.private_db("keybind")  # a connection of its own: safe on any thread
        db.execute(_TABLE)
        return db

    def allows(self, key: str, origin: str) -> bool:
        """Whether ``key`` may be sent to ``origin`` (``scheme://host[:port]``, as the owner
        typed it): bound to it now when the key is new, else only if it is the address the
        key was bound to."""
        want, where = _fingerprint(key), origin.strip().lower()
        db = self._open()
        try:
            row = db.execute("SELECT fp, origin FROM keybind WHERE name=?",
                             (self._name,)).fetchone()
            if row is not None and row[0] == want:
                return str(row[1]) == where
            db.execute("INSERT INTO keybind (name, fp, origin) VALUES (?, ?, ?) "
                       "ON CONFLICT(name) DO UPDATE SET fp=excluded.fp, "
                       "origin=excluded.origin", (self._name, want, where))
            db.commit()
            return True
        finally:
            db.close()

    def forget(self) -> None:
        """The key is gone (the owner cleared it): the next key is bound afresh."""
        db = self._open()
        try:
            db.execute("DELETE FROM keybind WHERE name=?", (self._name,))
            db.commit()
        finally:
            db.close()

    def bound_to(self) -> str | None:
        """The address the key is bound to, or None."""
        db = self._open()
        try:
            row = db.execute("SELECT origin FROM keybind WHERE name=?",
                             (self._name,)).fetchone()
            return str(row[0]) if row else None
        finally:
            db.close()
