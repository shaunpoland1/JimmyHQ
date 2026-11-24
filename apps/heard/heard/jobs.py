"""A question to a model takes as long as the model takes, and a page call is answered
within about thirty seconds. So the question is asked on a thread of its own and the page
asks again, every moment, whether it has been answered.

The thread does nothing but ask. It never touches the database: what it needs is read
before it starts and what it brings back is kept in memory until the page takes it, and
the program does the rest, on its own thread, when the page asks. A restart forgets a
question that was still on its way.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger("heard")


class Job:
    def __init__(self, context: dict[str, Any]):
        self.context = context
        self.done = threading.Event()
        self.answer: str | None = None  # what the model said
        self.said: str = ""  # or why it could not be asked, as a sentence for the owner


class Unanswered(Exception):
    """The model could not be used. ``str()`` is the sentence for the owner."""


class Jobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def start(self, key: str, context: dict[str, Any], ask: Callable[[], str],
              unanswered: str) -> bool:
        """Begin asking, unless this question is already being asked. ``ask`` returns the
        model's text or raises ``Unanswered``; anything else it raises reads as
        ``unanswered``. False when one was already on its way."""
        with self._lock:
            if key in self._jobs:
                return False
            job = self._jobs[key] = Job(context)

        def work() -> None:
            try:
                job.answer = ask()
            except Unanswered as why:
                job.said = str(why)
            except Exception as error:  # noqa: BLE001 - a thread must not die silently
                log.warning("a question to a model failed (%s)", type(error).__name__)
                job.said = unanswered
            finally:
                job.done.set()

        threading.Thread(target=work, name=f"heard-{key}", daemon=True).start()
        return True

    def peek(self, key: str) -> Job | None:
        with self._lock:
            return self._jobs.get(key)

    def take(self, key: str) -> Job | None:
        """The finished job, now forgotten; None when there is none or it is still
        running."""
        with self._lock:
            job = self._jobs.get(key)
            if job is None or not job.done.is_set():
                return None
            del self._jobs[key]
            return job

    def forget(self, prefix: str = "") -> None:
        """Drop the questions whose key starts with ``prefix`` (all of them, by default):
        what they bring back is thrown away."""
        with self._lock:
            for key in [k for k in self._jobs if k.startswith(prefix)]:
                del self._jobs[key]
