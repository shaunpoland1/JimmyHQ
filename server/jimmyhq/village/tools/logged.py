"""What of a tool call goes into the log.

The log keeps every action's arguments and the start of its result, and the night reads
it back into a model. So anything that looks like a secret is taken out before it is
logged: a key or a password that passed through a file or a message must not live on in
the record.

The scrubbing itself lives in ``jimmyhq.scrub``, shared with every other module that
handles outside text. ``text``, ``looks_secret`` and ``REMOVED`` are re-exported here.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from jimmyhq.scrub import REMOVED, looks_secret, text

__all__ = ["REMOVED", "args", "looks_secret", "result", "text"]

KEEP_CHARS = 2000  # of a long text argument; the rest is counted, not kept
_LONG = ("content", "old_text", "new_text")
_SECRET_KEYS = re.compile(r"(?i)(pass(word|code|phrase)?|secret|token|api[_-]?key|"
                          r"authorization|credential)s?$")


def _long(value: str) -> dict[str, Any] | str:
    """A long text argument: its start, its size and a fingerprint. The fingerprint
    keeps two different writes from looking like the same action to the repeat guard."""
    if len(value) <= KEEP_CHARS:
        return text(value)
    return {"chars": len(value),
            "sha": hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12],
            "start": text(value[:KEEP_CHARS])}


def _value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        if _SECRET_KEYS.search(key):
            return REMOVED
        return _long(value) if key in _LONG else text(value)
    if isinstance(value, dict):
        return {str(k): _value(str(k), x) for k, x in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(key, x) for x in value]
    return value


def args(given: dict[str, Any] | None) -> dict[str, Any]:
    """A tool's arguments as the log may keep them."""
    return {str(k): _value(str(k), x) for k, x in (given or {}).items()}


def result(value: object) -> str:
    """A tool's result as the log may keep it. The agent still sees the real one."""
    return text(str(value))
