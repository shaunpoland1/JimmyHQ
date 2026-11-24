"""A stored place on the Tape ("I have read up to event N"), checked where it is used.

A store that keeps such a place can end up ahead of the Tape it reads: after a restore
from a backup made before the Tape was copied last, or from a folder copied by hand. The
Tape then hands out numbers the store believes it has already handled, and the new events
are silently skipped. ``within_tape`` is the one check every such reader makes. What to
do about a place that was past the end depends on the reader (a stored view replays, a
reader with side effects goes back to the end) and stays with the reader; this says only
whether it happened, and says so in the log.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class _HasEnd(Protocol):
    def last_seq(self) -> int: ...


def within_tape(tape: _HasEnd, seq: int, who: str) -> int:
    """``seq``, or the Tape's last event when ``seq`` is past it. ``who`` names the reader
    in the log line. The caller stores the result when it differs. Read the position
    before the Tape's end, so the end is never behind it."""
    end = tape.last_seq()
    if seq <= end:
        return seq
    log.warning("%s had read the Tape to event %s but the Tape ends at %s (it was restored "
                "from an older copy); reading on from its end", who, seq, end)
    return end
