"""The live door: the serving core for a wearable that posts short chunks.

A pendant's phone app posts a few seconds of audio at a time and waits about
ten seconds for each answer. If an answer is late it sends the same audio
again inside a bigger request, so one slow chunk snowballs into minutes of
audio, several copies of each, queued behind each other. The door exists to
make that impossible:

- **One worker thread of its own.** Chunks are transcribed strictly one at a
  time and in the order they arrived, never on a shared thread pool, so a
  backlog here cannot slow any other request.
- **Every chunk is answered inside the deadline.** If the text is not ready
  in time the answer is empty and the chunk is finished in the background;
  its text still reaches ``on_text``, so nothing is lost.
- **A resend is recognised.** The same bytes arriving again within the
  duplicate window are answered empty at once and not transcribed twice.
- **The backlog is bounded.** When too many chunks are waiting, new ones are
  answered "busy" and dropped rather than queued without limit.

There is no route for this yet: its shape is dictated by the device. The route
that comes later builds a door from ``LiveLimits.from_settings`` and maps
``LiveAnswer.status`` to whatever that device needs to hear.

The queue is in memory on purpose. It holds audio a phone is still waiting on;
after a restart the phone resends it. Text the door produces is handed to
``on_text``, whose job it is to keep it somewhere durable.
"""

from __future__ import annotations

import hashlib
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from jimmyhq.settings import Settings

Status = Literal["answered", "deferred", "duplicate", "busy", "failed"]


@dataclass(frozen=True)
class LiveLimits:
    deadline: float  # seconds a caller waits for the text of one chunk
    backlog: int  # chunks that may be queued or running before the door says busy
    duplicate_window: float  # seconds a resend of the same bytes is recognised

    @classmethod
    def from_settings(cls, settings: Settings) -> LiveLimits:
        return cls(
            deadline=settings.get("speech.live_deadline_seconds"),
            backlog=settings.get("speech.live_backlog"),
            duplicate_window=settings.get("speech.live_duplicate_seconds"),
        )


@dataclass(frozen=True)
class LiveAnswer:
    """What to tell the device. ``text`` is empty unless ``status`` is "answered".

    - answered:  transcribed within the deadline
    - deferred:  accepted; the text will go to ``on_text`` when it is ready
    - duplicate: these bytes were already accepted; nothing more to do
    - busy:      the backlog is full and the chunk was dropped
    - failed:    the engine could not transcribe it; the same bytes may be sent again
    """

    text: str
    status: Status


class _Job:
    def __init__(self, number: int, chunk: bytes, digest: bytes):
        self.number = number
        self.digest = digest
        self.chunk = chunk
        self.done = threading.Event()
        self.text = ""
        self.failed = False
        self.waiting = True  # False once the caller has given up on it


class LiveDoor:
    def __init__(
        self,
        transcribe: Callable[[bytes], str],
        limits: LiveLimits,
        *,
        on_text: Callable[[int, str, bool], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        """``transcribe`` turns one chunk into text and may be slow.

        ``on_text(number, text, late)`` is called on the worker thread for every
        chunk that produced text, in arrival order. ``late`` is True when the
        caller had already been answered empty, which is the only way that text
        can still reach anyone.
        """
        self._transcribe = transcribe
        self._limits = limits
        self._on_text = on_text
        self._clock = clock
        self._state = threading.Lock()
        self._jobs: queue.SimpleQueue[_Job | None] = queue.SimpleQueue()
        self._backlog = 0  # queued or running
        self._seen: dict[bytes, float] = {}  # digest -> when first accepted
        self._count = 0
        self._worker: threading.Thread | None = None
        self._closed = False
        self._tally = {"answered": 0, "deferred": 0, "duplicate": 0, "busy": 0, "failed": 0}

    def submit(self, chunk: bytes) -> LiveAnswer:
        """Blocks for at most the deadline. Call it from a thread, not the event loop."""
        digest = hashlib.sha256(chunk).digest()
        with self._state:
            if self._closed:
                return self._answer("", "busy")
            now = self._clock()
            window = self._limits.duplicate_window
            for old in [d for d, at in self._seen.items() if now - at >= window]:
                del self._seen[old]
            if window > 0 and digest in self._seen:
                return self._answer("", "duplicate")
            if self._backlog >= self._limits.backlog:
                # Not remembered as seen: a resend once there is room is welcome.
                return self._answer("", "busy")
            if window > 0:
                self._seen[digest] = now
            self._backlog += 1
            self._count += 1
            job = _Job(self._count, chunk, digest)
            self._jobs.put(job)  # inside the lock, so queue order is arrival order
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="speech-live",
                                                daemon=True)
                self._worker.start()
        finished = job.done.wait(self._limits.deadline)
        with self._state:
            if not finished and not job.done.is_set():
                job.waiting = False
                return self._answer("", "deferred")
            if job.failed:
                return self._answer("", "failed")
            return self._answer(job.text, "answered")

    def _answer(self, text: str, status: Status) -> LiveAnswer:
        self._tally[status] += 1
        return LiveAnswer(text, status)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            text, failed = "", False
            try:
                text = self._transcribe(job.chunk) or ""
            except Exception:  # noqa: BLE001 - one bad chunk must not stop the door
                failed = True
            job.chunk = b""  # the audio is not kept a moment longer than needed
            with self._state:
                job.text, job.failed = text, failed
                late = not job.waiting
                job.done.set()
                self._backlog -= 1
                if failed:
                    # Not transcribed, so not a duplicate: the device's resend gets a go.
                    self._seen.pop(job.digest, None)
            if text and self._on_text is not None:
                try:
                    self._on_text(job.number, text, late)
                except Exception:  # noqa: BLE001, S110 - the keeper's trouble is not the door's
                    pass

    def stats(self) -> dict[str, int]:
        with self._state:
            return {"backlog": self._backlog, **self._tally}

    def drain(self, timeout: float = 5.0) -> bool:
        """Wait until nothing is queued or running. For shutdown and tests."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._state:
                if self._backlog == 0:
                    return True
            time.sleep(0.005)
        return False

    def close(self) -> None:
        """Stop taking chunks and let the worker end after what is already queued."""
        with self._state:
            if self._closed:
                return
            self._closed = True
            if self._worker is not None:
                self._jobs.put(None)
