"""Identifiers and the one clock.

Every stored time in JimmyHQ is unix milliseconds in UTC. Convert to a local
time only at the display edge.
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def ulid(ts_ms: int | None = None) -> str:
    """A 26-character, time-sortable id (48 bits of time, 80 bits of randomness)."""
    value = ((now_ms() if ts_ms is None else ts_ms) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))
