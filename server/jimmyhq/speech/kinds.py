"""Event kinds for speech: receipts only, never words.

Dictation keeps no audio and no text. What the Tape records is that a
transcription happened, which engine did it, whether the audio left the
machine, how long it was and who asked. A failure records a reason from a
fixed list, so nothing an engine says about the audio can end up on the record.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

Engine = Literal["local", "parakeet", "endpoint"]

FailureReason = Literal[
    "not_allowed",   # a hosted engine, and this caller is not on the allowed list
    "unavailable",   # the engine is not installed or not fully set up
    "busy",          # the model was in use for longer than the caller may wait
    "too_long",      # the recording is longer than the limit
    "unreadable",    # the engine could not read the recording as audio
    "engine_error",  # the engine failed, or answered in a form that makes no sense
]


@kind("speech.transcribed", example={
    "engine": "local", "hosted": False, "caller": "dictation",
    "audio_seconds": 4.2, "processing_ms": 810,
})
class SpeechTranscribed(Payload):
    """A receipt for one transcription. It never carries the words."""

    engine: Engine
    hosted: bool  # True when the audio was sent off this machine
    caller: str = Field(max_length=40)  # the surface that asked, e.g. "dictation"
    audio_seconds: float = Field(ge=0)
    processing_ms: int = Field(ge=0)


@kind("speech.failed", example={
    "engine": "endpoint", "hosted": True, "caller": "dictation", "reason": "not_allowed",
})
class SpeechFailed(Payload):
    """A transcription was asked for and did not happen. No content, only why."""

    engine: Engine
    hosted: bool
    caller: str = Field(max_length=40)
    reason: FailureReason


@kind("speech.key_changed", example={"set": True})
class SpeechKeyChanged(Payload):
    """The key for the speech server was saved or removed. Never the key itself."""

    set: bool
