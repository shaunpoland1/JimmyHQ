"""What a transcription is, and the one error every speech path raises."""

from __future__ import annotations

from pydantic import BaseModel


class Word(BaseModel):
    start: float  # seconds from the start of the recording
    end: float
    word: str


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] | None = None  # only when asked for and the engine provides them


class Transcript(BaseModel):
    """The result is segments, not just a string, so later surfaces (recorders,
    captions) can place the words in time."""

    text: str
    language: str  # as the engine reports it; "" when it does not say
    duration: float  # seconds of audio
    segments: list[Segment]

    def public(self) -> dict:
        """The API shape: ``words`` is left out of a segment that has none."""
        return self.model_dump(exclude_none=True)


class SpeechError(Exception):
    """A refusal or failure, written for the person who has to recover from it.

    ``status`` is the HTTP code a route answers with. ``reason`` is set when the
    failure belongs on the Tape as a ``speech.failed`` receipt.
    """

    def __init__(self, status: int, detail: str, reason: str | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.reason = reason


def tidy(seconds: object) -> float:
    """A time in seconds as a plain, non-negative number with millisecond precision."""
    try:
        value = float(seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):  # 1e400 written out as an integer
        return 0.0
    if value != value or value in (float("inf"), float("-inf")):
        return 0.0
    return round(max(0.0, value), 3)


def clean(text: object) -> str:
    """Engine text made safe to send as JSON: half a surrogate pair cannot be encoded."""
    return str(text).encode("utf-8", "replace").decode("utf-8").replace("\x00", "").strip()
