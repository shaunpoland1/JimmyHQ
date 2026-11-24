"""Printing web lookup's receipts, and the keyed hash a private one carries."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Any

from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.lookup")

RECEIPT_KEY = "lookup.receipt_key"  # the secret that keys the hashes
MAX_KEPT = 2000  # characters of a query or URL kept on a receipt


def _key(core: Core) -> bytes:
    """This install's key for hashing, made the first time one is needed and
    kept with the other secrets. Never logged or returned."""
    key = core.secrets.get(RECEIPT_KEY)
    if not key:
        core.secrets.set(RECEIPT_KEY, secrets.token_hex(32))
        # Read back: if another process made one at the same moment, the two
        # must agree on whichever was written last.
        key = core.secrets.get(RECEIPT_KEY) or ""
    return key.encode("ascii")


def keyed_hash(core: Core, text: str) -> str:
    """The same text gives the same hash on this install, and nobody without
    the key can test a guess against it."""
    return hmac.new(_key(core), text.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


def said(core: Core, text: str, private: bool) -> tuple[str | None, str | None]:
    """``(text, None)`` for a receipt that may say it, ``(None, hash)`` for a
    private one."""
    if private:
        return None, keyed_hash(core, text)
    return text[:MAX_KEPT], None


def print_receipt(core: Core, kind: str, payload: dict[str, Any], *, actor: str,
                  cause: str | None, root: str | None) -> None:
    """A receipt that cannot be printed must not lose the owner their answer."""
    try:
        core.tape.print(kind, payload, actor=actor, cause=cause, root=root)
    except Exception as exc:  # noqa: BLE001
        log.warning("a web lookup left no receipt: %s", type(exc).__name__)
