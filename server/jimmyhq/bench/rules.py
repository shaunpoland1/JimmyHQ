"""Shared limits, name shapes and the one error type for the Bench.

Kept apart from the rest so cards and the ladder can both use them without
importing each other.
"""

from __future__ import annotations

import re

LANES = ("needs", "done", "waiting")
LEVELS = ("read", "approve", "alone")  # lowest to highest

DAY_MS = 24 * 60 * 60 * 1000

MAX_BODY = 1_000_000  # characters
INLINE_BODY = 4096  # bytes; a longer body is stored in the Vault
MAX_NOTE = 2000
MAX_ACTIONS = 8
MAX_DETAILS = 12
MAX_DETAIL_TEXT = 2000
MAX_FILES = 20
MAX_UPDATES = 1000  # per card, so one card's story stays a small indexed read

CARD_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
DOTTED = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")  # "posts.draft"
NAME = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")  # a tool or an action
SLUG = re.compile(r"[a-z][a-z0-9_]*")
ENTITY = re.compile(r"[a-z][a-z0-9_.]*:\S+")  # "task:01J..."
VAULT_REF = re.compile(r"sha256:[0-9a-f]{64}")


class BenchError(Exception):
    """Something the caller asked for cannot be done. ``detail`` is one sentence
    that says what to do; ``status`` is the HTTP status a route should answer with."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def storable(text: str) -> bool:
    """False for text that cannot be saved, such as half of a surrogate pair."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return "\x00" not in text


def clean(value: object, label: str, limit: int, *, required: bool = False) -> str | None:
    """A trimmed, storable piece of text, or None when it is empty."""
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise BenchError(422, f"{label} must be text.")
    if not storable(text):
        raise BenchError(422, f"{label} contains characters that cannot be saved.")
    if len(text) > limit:
        raise BenchError(422, f"{label} cannot be longer than {limit} characters.")
    if not text:
        if required:
            raise BenchError(422, f"{label} cannot be empty.")
        return None
    return text


def line(value: object, label: str, limit: int) -> str:
    """Required one-line text: trimmed, storable, with no control characters."""
    text = clean(value, label, limit, required=True) or ""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise BenchError(422, f"{label} must be a single line of ordinary text.")
    return text


def name(value: object, label: str, shape: re.Pattern[str], limit: int = 60) -> str:
    if not isinstance(value, str) or len(value) > limit or not shape.fullmatch(value):
        raise BenchError(
            422, f"{label} must be a short lower-case name such as 'posts' or 'posts.publish'."
        )
    return value
