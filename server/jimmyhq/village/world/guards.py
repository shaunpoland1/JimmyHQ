"""Mechanical guards. A repeat is the same action against the same observed state."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import Any

# Noise that changes between identical screens and must not defeat the guard.
_NOISE = [
    re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", re.I),  # clock times
    re.compile(r"\b\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?\S*", re.I),  # timestamps
    re.compile(r"\b(took|elapsed|in)\s+\d+(\.\d+)?\s*(ms|s|sec|seconds)\b", re.I),  # durations
    re.compile(r"\bpid\s*\d+\b", re.I),
    re.compile(r"\s+"),
]


def normalize(observation: Any) -> str:
    text = observation if isinstance(observation, str) else json.dumps(observation,
                                                                       sort_keys=True)
    text = text.lower()
    for pat in _NOISE[:-1]:
        text = pat.sub("#", text)
    return _NOISE[-1].sub(" ", text).strip()


def obs_hash(observation: Any) -> str:
    # A fingerprint for comparing screens, not a security measure.
    digest = hashlib.sha1(normalize(observation).encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()[:16]


def action_key(tool: str, args: dict[str, Any] | None) -> str:
    return tool + ":" + json.dumps(args or {}, sort_keys=True)


class RepeatGuard:
    """Remembers recent (action, state-before) and (action, result) pairs."""

    def __init__(self, window: int = 6):
        self.before: deque[tuple[str, str]] = deque(maxlen=window)
        self.after: deque[tuple[str, str]] = deque(maxlen=window)

    @property
    def window(self) -> int:
        return self.before.maxlen or 0

    def resize(self, window: int) -> None:
        self.before = deque(self.before, maxlen=window)
        self.after = deque(self.after, maxlen=window)

    def clear(self) -> None:
        """Forget everything: called on waking. A repeat guard that survives sleep refuses
        the first action of the morning whenever the screen it starts from looks like the
        one a failure started from the night before. A new morning is a new state."""
        self.before.clear()
        self.after.clear()

    def is_repeat(self, key: str, before_hash: str | None) -> bool:
        return before_hash is not None and (key, before_hash) in self.before

    def no_new_result(self, key: str, result_hash: str) -> bool:
        return (key, result_hash) in self.after

    def remember(self, key: str, before_hash: str | None, result_hash: str) -> None:
        if before_hash is not None:
            self.before.append((key, before_hash))
        self.after.append((key, result_hash))
