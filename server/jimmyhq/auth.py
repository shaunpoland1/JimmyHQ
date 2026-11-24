"""Access: one owner per install, browser sessions, and scoped API tokens.

Credentials live in their own database and never reach the Tape. Passwords are
stored as scrypt hashes; session and API tokens are stored as sha256 digests,
so the database alone cannot be used to sign in.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from jimmyhq import storeversion
from jimmyhq.ids import now_ms, ulid
from jimmyhq.tape.store import connect

MIN_PASSWORD = 10
MAX_PASSWORD = 1024
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}
_ATTEMPT_WINDOW_MS = 15 * 60 * 1000
_ATTEMPT_LIMIT = 5  # wrong passwords per client address per window
_ATTEMPT_CEILING = 100  # wrong passwords from all addresses per window
_DAY_MS = 24 * 60 * 60 * 1000

SCOPES = ("read", "write")
# What an API token may be made for: reading only, or reading and making changes.
TOKEN_LEVELS = {"reader": ("read",), "writer": ("read", "write")}
MAX_TOKEN_NAME = 64
DOOR_TOKEN_PREFIX = "jhqd_"  # noqa: S105 - a prefix, not a secret. A door client's token; never accepted as an API token
MAX_TOKEN = 200  # a longer string offered as a token is not looked at
_DOOR_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
# A second machine's token. It is accepted on the node link and nowhere else:
# ``check_token`` and ``check_door_token`` never look at it.
NODE_TOKEN_PREFIX = "jhqn_"  # noqa: S105 - a prefix, not a secret
NODE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")
# A running full app's token. It is accepted on the app API (``/api/app/v1/``) only, from
# this machine only, and nowhere else: ``check_token`` never looks at it. The principal it
# gives is ``app:<id>`` with the one scope ``app``, which no reader, writer or owner route
# accepts. What the app may do is read afresh on every call from what the owner granted.
APP_TOKEN_PREFIX = "jhqa_"  # noqa: S105 - a prefix, not a secret
APP_SCOPE = "app"
APP_ID = re.compile(r"[a-z][a-z0-9_]{1,30}")
# Letters and digits that are hard to mistake for one another when read aloud or
# typed on another machine: no 0 and O, no 1, I and L.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 10
_NODE_ATTEMPT_LIMIT = 5  # wrong join codes or node tokens per client address per window

_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  salt BLOB NOT NULL, hash BLOB NOT NULL, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  digest TEXT PRIMARY KEY, created INTEGER NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
  scopes TEXT NOT NULL, created INTEGER NOT NULL, revoked INTEGER
);
CREATE TABLE IF NOT EXISTS door_clients (
  id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, digest TEXT UNIQUE NOT NULL,
  created INTEGER NOT NULL, rotated INTEGER, revoked INTEGER
);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
  created INTEGER NOT NULL, revoked INTEGER
);
CREATE TABLE IF NOT EXISTS app_tokens (
  id TEXT PRIMARY KEY, app_id TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
  created INTEGER NOT NULL, revoked INTEGER
);
CREATE INDEX IF NOT EXISTS app_tokens_app ON app_tokens (app_id, revoked);
CREATE TABLE IF NOT EXISTS node_join_codes (
  digest TEXT PRIMARY KEY, created INTEGER NOT NULL, expires INTEGER NOT NULL, used INTEGER
);
CREATE TABLE IF NOT EXISTS node_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, client TEXT NOT NULL,
  local INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sign_in_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, client TEXT NOT NULL,
  local INTEGER NOT NULL DEFAULT 0
);
"""

STORE = storeversion.declare("auth", _SCHEMA, files=("auth.db",))


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    # "owner", "token:<name>", on the node link only "node:<name>", and on the app API
    # only "app:<id>"
    actor: str
    scopes: frozenset[str]

    @property
    def is_owner(self) -> bool:
        return self.actor == "owner"

    def can(self, scope: str) -> bool:
        return self.is_owner or scope in self.scopes


def _is_loopback(client: str) -> bool:
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"The password must be at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AuthError("The password is too long.")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise AuthError("The password contains characters that cannot be used.") from None


class Auth:
    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._conn = connect(path)
        storeversion.prepare(self._conn, STORE)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # not every file system has owner-only permissions

    # --- owner ----------------------------------------------------------------

    def owner_exists(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM owner").fetchone() is not None

    def create_owner(self, password: str) -> None:
        check_password_rules(password)
        salt = os.urandom(16)
        hashed = _hash(password, salt)
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO owner (id, salt, hash, created) VALUES (1, ?, ?, ?)",
                    (salt, hashed, now_ms()),
                )
            except Exception as exc:
                raise AuthError("An owner already exists for this install.") from exc

    def change_password(self, current: str, new: str, client: str,
                        session: str | None, days: int = 0) -> tuple[str, int, str | None]:
        """Replace the owner's password, given the current one. Returns (outcome, how
        many other sessions were ended, the token of the session that replaces
        ``session``); the outcome is "ok", "wrong" or "locked".

        The current password is checked by ``sign_in``, so a wrong one counts against
        this client exactly as a failed sign-in does, and a locked client is refused
        before anything is compared. Nothing changes unless the outcome is "ok". Then,
        in the same step as the new hash is stored, every session stops working,
        ``session`` included: a token that was in use under the old password is not
        carried over to the new one. If ``session`` was live, a fresh one lasting
        ``days`` takes its place and its token is returned; otherwise the third value
        is None. A new password that breaks the rules raises ``AuthError`` before the
        current one is looked at.
        """
        check_password_rules(new)
        outcome = self.sign_in(current, client)
        if outcome != "ok":
            return outcome, 0, None
        if hmac.compare_digest(new.encode(), current.encode()):
            raise AuthError("The new password is the same as the current one. Nothing was "
                            "changed. Choose a different one.")
        ended, fresh = self._store_password(new, session, days)
        return "ok", ended, fresh

    def set_password(self, new: str) -> int:
        """Replace the owner's password without the current one, and end every session.
        Returns how many were ended. Only for someone at the computer itself (the
        ``jimmyhq owner set-password`` command): no route calls this. Wrong-password
        counts are cleared too, so a locked-out owner can sign in straight away. API
        tokens, door clients and nodes are left as they are."""
        check_password_rules(new)
        if not self.owner_exists():
            raise AuthError("There is no owner yet, so there is no password to set. Nothing "
                            "was changed. Open JimmyHQ in a browser on this computer to "
                            "create the owner.")
        ended, _ = self._store_password(new, None, 0)
        with self._lock:
            self._conn.execute("DELETE FROM sign_in_attempts")
        return ended

    def _store_password(self, new: str, replace: str | None,
                        days: int) -> tuple[int, str | None]:
        """Store the new hash and end every session, as one step. Returns (how many
        sessions other than ``replace`` were ended, the token of the session that
        took the place of ``replace``, or None if that was not a live session)."""
        salt = os.urandom(16)
        hashed = _hash(new, salt)
        fresh = None
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("UPDATE owner SET salt = ?, hash = ? WHERE id = 1",
                                   (salt, hashed))
                ended = self._end_sessions_except(replace)
                if replace:
                    gone = self._conn.execute(
                        "DELETE FROM sessions WHERE digest = ?", (_digest(replace),))
                    if gone.rowcount:
                        fresh = secrets.token_urlsafe(32)
                        now = now_ms()
                        self._conn.execute(
                            "INSERT INTO sessions (digest, created, expires) VALUES (?,?,?)",
                            (_digest(fresh), now, now + days * _DAY_MS),
                        )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return ended, fresh

    def sign_in(self, password: str, client: str) -> str:
        """Check the owner's password. Returns "ok", "wrong" or "locked".

        Wrong passwords are counted per client address, so one machine guessing
        cannot lock the owner out from another. The attempt is recorded before
        the slow hash, so requests sent together cannot slip past the limit.
        ``client`` is what ``web.locality.client_key`` says: a request relayed by a
        tunnel on this computer arrives as ``forwarded:...``, which is not a loopback
        address, so it is counted as remote and never in the owner's own bucket.
        """
        now = now_ms()
        floor = now - _ATTEMPT_WINDOW_MS
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM sign_in_attempts WHERE ts <= ?", (floor,))
                mine = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM sign_in_attempts WHERE client = ?", (client,)
                ).fetchone()["n"]
                # The overall ceiling guards against guessing from many
                # addresses. It never applies to the machine itself, so the
                # owner sitting at it can always sign in.
                local = _is_loopback(client)
                remote = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM sign_in_attempts WHERE local = 0"
                ).fetchone()["n"]
                if mine >= _ATTEMPT_LIMIT or (not local and remote >= _ATTEMPT_CEILING):
                    self._conn.execute("COMMIT")
                    return "locked"
                attempt = self._conn.execute(
                    "INSERT INTO sign_in_attempts (ts, client, local) VALUES (?, ?, ?)",
                    (now, client, int(local)),
                ).lastrowid
                row = self._conn.execute("SELECT salt, hash FROM owner").fetchone()
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        ok = False
        if row is not None and len(password) <= MAX_PASSWORD:
            try:
                ok = hmac.compare_digest(_hash(password, row["salt"]), row["hash"])
            except UnicodeEncodeError:
                ok = False  # text that cannot be encoded is simply a wrong password
            except BaseException:
                # The check itself failed (out of memory, say). That is not a
                # wrong password and must not count towards a lockout.
                with self._lock:
                    self._conn.execute("DELETE FROM sign_in_attempts WHERE id = ?", (attempt,))
                raise
        if ok:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM sign_in_attempts WHERE client = ? OR id = ?", (client, attempt)
                )
        return "ok" if ok else "wrong"

    # --- browser sessions -----------------------------------------------------

    def create_session(self, days: int) -> str:
        token = secrets.token_urlsafe(32)
        now = now_ms()
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            self._conn.execute(
                "INSERT INTO sessions (digest, created, expires) VALUES (?,?,?)",
                (_digest(token), now, now + days * _DAY_MS),
            )
        return token

    def session_valid(self, token: str | None, days: int | None = None) -> bool:
        """Whether the session is live. With ``days`` (the "Stay signed in for" setting
        as it is now), a session given out under a longer setting is first cut down to
        end no later than ``days`` from now, so lowering the setting shortens sessions
        already given out, not only new ones."""
        if not token:
            return False
        now = now_ms()
        digest = _digest(token)
        with self._lock:
            row = self._conn.execute(
                "SELECT expires FROM sessions WHERE digest = ?", (digest,)
            ).fetchone()
            if row is None or row["expires"] <= now:
                return False
            latest = now + max(int(days), 0) * _DAY_MS if days is not None else None
            if latest is not None and row["expires"] > latest:
                # Written only in the rare case the setting went down, not per request.
                self._conn.execute(
                    "UPDATE sessions SET expires = ? WHERE digest = ? AND expires > ?",
                    (latest, digest, latest))
                return latest > now
        return True

    def renew_session(self, token: str | None, days: int) -> bool:
        """Extend a session that is in use. True if its end moved.

        Without this a screen that is never signed in again, such as a status
        board on a second monitor, would be signed out on a fixed date. Only
        done once less than half the session's life remains, so it is one
        write every few days, not one per request.
        """
        if not token:
            return False
        now = now_ms()
        life = days * _DAY_MS
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET expires = ? WHERE digest = ? AND expires > ? AND expires < ?",
                (now + life, _digest(token), now, now + life // 2),
            )
        return cur.rowcount > 0

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._conn.execute("DELETE FROM sessions WHERE digest = ?", (_digest(token),))

    def end_all_sessions(self) -> int:
        """End every session. Returns how many were still live."""
        return self.end_other_sessions(None)

    def end_other_sessions(self, keep: str | None) -> int:
        """End every session but the one this token belongs to. Returns how many live
        ones were ended. A session is a row here and is looked up on every request, by
        every process that shares this database, so an ended one stops working at once."""
        with self._lock:
            return self._end_sessions_except(keep)

    def _end_sessions_except(self, keep: str | None) -> int:
        # Called with the lock held. Sessions already past their end are not counted.
        self._conn.execute("DELETE FROM sessions WHERE expires <= ?", (now_ms(),))
        cur = self._conn.execute(
            "DELETE FROM sessions WHERE digest != ?", (_digest(keep) if keep else "",))
        return cur.rowcount

    # --- API tokens -----------------------------------------------------------

    def create_token(self, name: str, scopes: list[str]) -> tuple[str, str]:
        """Returns (token id, the token itself). The token is shown once and not stored."""
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > MAX_TOKEN_NAME or ":" in name or not name.isprintable():
            raise AuthError(f"A token needs a label of 1 to {MAX_TOKEN_NAME} characters, "
                            "without a colon. Nothing was made.")
        if not scopes or any(s not in SCOPES for s in scopes):
            raise AuthError(f"Scopes must be chosen from: {', '.join(SCOPES)}. "
                            "Nothing was made.")
        token = "jhq_" + secrets.token_urlsafe(32)
        token_id = ulid()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tokens (id, name, digest, scopes, created) VALUES (?,?,?,?,?)",
                (token_id, name, _digest(token), json.dumps(sorted(set(scopes))), now_ms()),
            )
        return token_id, token

    def check_token(self, token: str | None) -> Principal | None:
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT name, scopes FROM tokens WHERE digest = ? AND revoked IS NULL",
                (_digest(token),),
            ).fetchone()
        if row is None:
            return None
        return Principal(f"token:{row['name']}", frozenset(json.loads(row["scopes"])))

    def revoke_token(self, token_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tokens SET revoked = ? WHERE id = ? AND revoked IS NULL",
                (now_ms(), token_id),
            )
        return cur.rowcount > 0

    def list_tokens(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, scopes, created, revoked FROM tokens ORDER BY created"
            ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "scopes": json.loads(r["scopes"]),
             "created": r["created"], "revoked": r["revoked"]}
            for r in rows
        ]

    # --- door clients -----------------------------------------------------------
    #
    # An outside system that connects through the MCP door (``jimmyhq.door``). It
    # is a different thing from an API token: it has no scopes, ``check_token``
    # never accepts its token, and the door never accepts an API token.

    def create_door_client(self, name: str) -> tuple[str, str]:
        """Returns (client id, the token). The token is shown once and not stored.
        A name is never given out twice, even after its client was revoked: what a
        client did stays signed with a name that means one client."""
        if not isinstance(name, str) or not _DOOR_NAME.fullmatch(name):
            raise AuthError("A client's name is 1 to 32 characters: lower-case letters, "
                            "digits, - and _, starting with a letter or a digit.")
        token = DOOR_TOKEN_PREFIX + secrets.token_urlsafe(32)
        client_id = ulid()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO door_clients (id, name, digest, created) VALUES (?,?,?,?)",
                    (client_id, name, _digest(token), now_ms()),
                )
            except sqlite3.IntegrityError:
                raise AuthError(f"The name {name} has been used for a client before. Names "
                                "are not reused, so choose another.") from None
        return client_id, token

    def check_door_token(self, token: str | None) -> tuple[str, str] | None:
        """The (client id, name) a door token belongs to, or None. Every live
        client's digest is compared, each in constant time and with no early exit,
        so the time taken says nothing about which one came close."""
        if not token or len(token) > MAX_TOKEN or not token.startswith(DOOR_TOKEN_PREFIX):
            return None
        offered = _digest(token)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, digest FROM door_clients WHERE revoked IS NULL"
            ).fetchall()
        found = None
        for row in rows:
            if hmac.compare_digest(row["digest"], offered):
                found = (row["id"], row["name"])
        return found

    def rotate_door_client(self, client_id: str) -> str | None:
        """A new token for a live client, or None if there is no such client. The
        old token stops working with this call."""
        token = DOOR_TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            cur = self._conn.execute(
                "UPDATE door_clients SET digest = ?, rotated = ? WHERE id = ? AND revoked IS NULL",
                (_digest(token), now_ms(), client_id),
            )
        return token if cur.rowcount > 0 else None

    def revoke_door_client(self, client_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE door_clients SET revoked = ? WHERE id = ? AND revoked IS NULL",
                (now_ms(), client_id),
            )
        return cur.rowcount > 0

    def list_door_clients(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, created, rotated, revoked FROM door_clients ORDER BY created"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- nodes ------------------------------------------------------------------
    #
    # A second machine the owner has joined to this one (``jimmyhq.nodes``). Its
    # token is a fourth kind of credential: it has its own table, no scopes that
    # the API knows, and is only ever looked at on the node link. A join code is
    # what the owner types on the other machine once, to be given that token.

    def create_node_join_code(self, minutes: int) -> tuple[str, int]:
        """Returns (the code, when it stops working). The code is shown once and
        only its digest is kept. Making a new code ends any earlier unused one,
        so there is never more than one that works."""
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        now = now_ms()
        expires = now + minutes * 60_000
        with self._lock:
            self._conn.execute("DELETE FROM node_join_codes WHERE created <= ?", (now - _DAY_MS,))
            self._conn.execute(
                "UPDATE node_join_codes SET expires = ? WHERE used IS NULL AND expires > ?",
                (now, now))
            self._conn.execute(
                "INSERT INTO node_join_codes (digest, created, expires) VALUES (?,?,?)",
                (_digest(code), now, expires))
        return f"{code[:5]}-{code[5:]}", expires

    def nodes_in_use(self) -> bool:
        """Whether this install has anything to do with nodes: a machine that has
        joined, or a join code that still works. Until then the node link does
        not answer at all."""
        with self._lock:
            if self._conn.execute("SELECT 1 FROM nodes WHERE revoked IS NULL").fetchone():
                return True
            return self._conn.execute(
                "SELECT 1 FROM node_join_codes WHERE used IS NULL AND expires > ?",
                (now_ms(),)).fetchone() is not None

    def node_throttled(self, client: str) -> bool:
        """Whether this address has offered too many wrong join codes or node
        tokens lately. The overall ceiling never applies to this machine itself."""
        floor = now_ms() - _ATTEMPT_WINDOW_MS
        with self._lock:
            self._conn.execute("DELETE FROM node_attempts WHERE ts <= ?", (floor,))
            mine = self._conn.execute(
                "SELECT COUNT(*) AS n FROM node_attempts WHERE client = ?", (client,)
            ).fetchone()["n"]
            remote = self._conn.execute(
                "SELECT COUNT(*) AS n FROM node_attempts WHERE local = 0").fetchone()["n"]
        return mine >= _NODE_ATTEMPT_LIMIT or (
            not _is_loopback(client) and remote >= _ATTEMPT_CEILING)

    def note_node_failure(self, client: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO node_attempts (ts, client, local) VALUES (?,?,?)",
                (now_ms(), client, int(_is_loopback(client))))

    def redeem_node_join_code(self, code: str, name: str, client: str) -> tuple[str, str, str]:
        """Exchange a join code for a node token. Returns (outcome, node id, token).

        The outcome is "ok", "wrong", "expired", "used", "locked" or "name_taken";
        the id and token are empty unless it is "ok". A code works once. Every
        stored code is compared in constant time with no early exit. A wrong,
        expired or used code counts against the address that offered it.
        """
        if self.node_throttled(client):
            return "locked", "", ""
        if not isinstance(name, str) or not NODE_NAME.fullmatch(name):
            raise AuthError("A machine's name is 1 to 16 characters: lower-case letters, "
                            "digits and -, starting with a letter or a digit.")
        offered = _digest("".join(ch for ch in str(code).upper() if ch.isalnum())[:MAX_TOKEN])
        now = now_ms()
        token = NODE_TOKEN_PREFIX + secrets.token_urlsafe(32)
        node_id = ulid()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                found = None
                for row in self._conn.execute(
                        "SELECT digest, expires, used FROM node_join_codes").fetchall():
                    if hmac.compare_digest(row["digest"], offered):
                        found = row
                if found is None:
                    outcome = "wrong"
                elif found["used"] is not None:
                    outcome = "used"
                elif found["expires"] <= now:
                    outcome = "expired"
                elif self._conn.execute(
                        "SELECT 1 FROM nodes WHERE name = ? AND revoked IS NULL",
                        (name,)).fetchone():
                    outcome = "name_taken"  # the code is not spent: try again with a name
                else:
                    outcome = "ok"
                    self._conn.execute(
                        "UPDATE node_join_codes SET used = ? WHERE digest = ?",
                        (now, found["digest"]))
                    self._conn.execute(
                        "INSERT INTO nodes (id, name, digest, created) VALUES (?,?,?,?)",
                        (node_id, name, _digest(token), now))
                if outcome in ("wrong", "expired", "used"):
                    self._conn.execute(
                        "INSERT INTO node_attempts (ts, client, local) VALUES (?,?,?)",
                        (now, client, int(_is_loopback(client))))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return (outcome, node_id, token) if outcome == "ok" else (outcome, "", "")

    def check_node_token(self, token: str | None) -> tuple[str, str] | None:
        """The (node id, name) a node token belongs to, or None. Every live node's
        digest is compared, each in constant time and with no early exit."""
        if not token or len(token) > MAX_TOKEN or not token.startswith(NODE_TOKEN_PREFIX):
            return None
        offered = _digest(token)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, digest FROM nodes WHERE revoked IS NULL").fetchall()
        found = None
        for row in rows:
            if hmac.compare_digest(row["digest"], offered):
                found = (row["id"], row["name"])
        return found

    def node_live(self, node_id: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM nodes WHERE id = ? AND revoked IS NULL", (node_id,)
            ).fetchone() is not None

    def revoke_node(self, node_id: str) -> bool:
        """The node's token stops working with this call."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE nodes SET revoked = ? WHERE id = ? AND revoked IS NULL",
                (now_ms(), node_id))
        return cur.rowcount > 0

    def list_nodes(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, created, revoked FROM nodes ORDER BY created").fetchall()
        return [dict(r) for r in rows]

    # --- app tokens --------------------------------------------------------------
    #
    # Minted by the supervisor each time it starts a full app's process, and handed to
    # that process alone (``apps.runtime``). Minting revokes every earlier token of the
    # same app, so at most one works at a time.

    def mint_app_token(self, app_id: str) -> str:
        """A new token for this app; every earlier one stops working with this call. The
        token is returned once and only its digest is kept."""
        if not isinstance(app_id, str) or not APP_ID.fullmatch(app_id):
            raise AuthError("An app's id is 2 to 31 lower-case letters, digits and _, "
                            "starting with a letter.")
        token = APP_TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = now_ms()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE app_tokens SET revoked = ? WHERE app_id = ? AND revoked IS NULL",
                    (now, app_id))
                self._conn.execute(
                    "DELETE FROM app_tokens WHERE revoked IS NOT NULL AND revoked < ?",
                    (now - 30 * _DAY_MS,))
                self._conn.execute(
                    "INSERT INTO app_tokens (id, app_id, digest, created) VALUES (?,?,?,?)",
                    (ulid(), app_id, _digest(token), now))
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return token

    def check_app_token(self, token: str | None) -> Principal | None:
        """The app a live app token belongs to, as the principal ``app:<id>``, or None.
        Anything without the app prefix is not looked at."""
        if (not isinstance(token, str) or len(token) > MAX_TOKEN
                or not token.startswith(APP_TOKEN_PREFIX)):
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT app_id FROM app_tokens WHERE digest = ? AND revoked IS NULL",
                (_digest(token),)).fetchone()
        if row is None:
            return None
        return Principal(f"app:{row['app_id']}", frozenset({APP_SCOPE}))

    def revoke_app_tokens(self, app_id: str) -> int:
        """Stop every token of this app. Returns how many were live."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE app_tokens SET revoked = ? WHERE app_id = ? AND revoked IS NULL",
                (now_ms(), app_id))
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
