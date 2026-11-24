"""The speech service: one per process, shared by the routes and other modules.

It reads the settings for each call (so a change takes effect at once, in both
processes), applies the rules that do not depend on the engine (accepted
formats, limits, who may use a hosted engine) and prints the receipt.
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

import httpx

from jimmyhq.core import Core
from jimmyhq.speech.engines import (
    KEY_NAME,
    EndpointEngine,
    Engine,
    LocalEngine,
    ModelHolder,
    ParakeetEngine,
    too_long,
)
from jimmyhq.speech.transcript import SpeechError, Transcript

# Every spelling a browser or tool uses, mapped to the one name engines see.
CONTENT_TYPES = {
    "audio/webm": "audio/webm", "video/webm": "audio/webm",
    "audio/mp4": "audio/mp4", "video/mp4": "audio/mp4",
    "audio/wav": "audio/wav", "audio/x-wav": "audio/wav", "audio/wave": "audio/wav",
    "audio/vnd.wave": "audio/wav",
    "audio/mpeg": "audio/mpeg", "audio/mp3": "audio/mpeg",
    "audio/ogg": "audio/ogg", "application/ogg": "audio/ogg", "audio/opus": "audio/ogg",
    "audio/flac": "audio/flac", "audio/x-flac": "audio/flac",
    "audio/m4a": "audio/m4a", "audio/x-m4a": "audio/m4a", "audio/aac": "audio/m4a",
}
_BY_EXTENSION = {
    "webm": "audio/webm", "mp4": "audio/mp4", "wav": "audio/wav", "mp3": "audio/mpeg",
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "flac": "audio/flac",
    "m4a": "audio/m4a",
}
ACCEPTED = ["audio/webm", "audio/mp4", "audio/wav", "audio/mpeg", "audio/ogg", "audio/flac",
            "audio/m4a"]

_CALLER = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")
_WORKERS = 4  # batch calls in flight at once; the rest are told to try again
# The engine each "speech.engine" choice keeps in the model holder, for "warm".
_MODEL_KIND = {"local": "whisper", "parakeet": "parakeet"}


def content_type_of(declared: str | None, filename: str | None = None) -> str:
    """The canonical audio type, from the declared type or failing that the file name."""
    base = (declared or "").split(";", 1)[0].strip().lower()
    if base in CONTENT_TYPES:
        return CONTENT_TYPES[base]
    if base in ("", "application/octet-stream") and filename and "." in filename:
        by_name = _BY_EXTENSION.get(filename.rsplit(".", 1)[1].strip().lower())
        if by_name:
            return by_name
    raise SpeechError(415, "That kind of file cannot be transcribed. Send one of: "
                           f"{', '.join(ACCEPTED)}.")


def check_caller(caller: object) -> str:
    if not isinstance(caller, str) or not _CALLER.match(caller):
        raise SpeechError(422, "caller: Use a short lower-case name for the part of JimmyHQ "
                               "that is asking, such as dictation.")
    return caller


def check_language(language: object) -> str | None:
    if language is None or language == "":
        return None
    if not isinstance(language, str) or not _LANGUAGE.match(language.strip().lower()):
        raise SpeechError(422, "language: Use a two-letter code such as en or fr, or leave "
                               "it out to detect the language.")
    return language.strip().lower()


def _language_problem(language: str) -> str | None:
    """The 'Spoken language' setting is free text; an engine would choke on a bad one."""
    if language and not _LANGUAGE.match(language):
        return ("The spoken language under Settings, Voice is not a language code. Use a "
                "two-letter code such as en or fr, or leave it empty to detect the language.")
    return None


def _wav_seconds(audio: bytes) -> float | None:
    """The length of a WAV recording, which can be read without decoding it."""
    try:
        with wave.open(io.BytesIO(audio)) as handle:
            return handle.getnframes() / float(handle.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError, ValueError):
        return None


@dataclass(frozen=True)
class _Config:
    engine: str
    language: str
    max_seconds: int
    max_bytes: int
    busy_wait: float
    hosted_callers: tuple[str, ...]


class SpeechService:
    def __init__(self, core: Core):
        self._core = core
        self.models = ModelHolder()  # tests replace ``models.loader``
        self.transport: httpx.BaseTransport | None = None  # tests replace this
        self._pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="speech")
        self._slots = threading.BoundedSemaphore(_WORKERS)

    # --- settings ---------------------------------------------------------------

    def _config(self) -> _Config:
        get = self._core.settings.get
        megabytes = get("speech.max_megabytes")
        return _Config(
            engine=get("speech.engine"),
            language=get("speech.language").strip().lower(),
            max_seconds=get("speech.max_seconds"),
            max_bytes=megabytes * 1024 * 1024,
            busy_wait=get("speech.busy_wait_seconds"),
            hosted_callers=tuple(c.strip().lower() for c in get("speech.hosted_callers")),
        )

    def max_bytes(self) -> int:
        return self._core.settings.get("speech.max_megabytes") * 1024 * 1024

    def _engine(self, cfg: _Config) -> Engine | None:
        get = self._core.settings.get
        if cfg.engine == "local":
            return LocalEngine(
                self.models, model=get("speech.local_model"), device=get("speech.local_device"),
                compute_type=get("speech.local_compute_type"), busy_wait=cfg.busy_wait,
                max_seconds=cfg.max_seconds,
            )
        if cfg.engine == "parakeet":
            return ParakeetEngine(
                self.models, variant=get("speech.parakeet_variant"),
                threads=get("speech.parakeet_threads"), busy_wait=cfg.busy_wait,
                max_seconds=cfg.max_seconds,
            )
        if cfg.engine == "endpoint":
            return EndpointEngine(
                url=get("speech.endpoint_url"), model=get("speech.endpoint_model"),
                key=self._core.secrets.get(KEY_NAME), location=get("speech.endpoint_location"),
                timeout=get("speech.endpoint_timeout_seconds"), transport=self.transport,
            )
        return None

    # --- status -----------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """What is selected and whether it can be used. Loads nothing, calls nothing."""
        cfg = self._config()
        engine = self._engine(cfg)
        get = self._core.settings.get
        download = None
        if engine is None:
            problem = ("Speech to text is turned off. To turn dictation on, choose an engine "
                       "under Settings, Voice, 'Speech to text'.")
            model = None
        elif isinstance(engine, ParakeetEngine):
            problem = (engine.problem() or _language_problem(cfg.language)
                       or engine.language_problem(cfg.language))
            model = engine.shown_as()
            download = engine.download()
        else:
            problem = engine.problem()
            model = get("speech.local_model" if cfg.engine == "local" else "speech.endpoint_model")
            problem = problem or _language_problem(cfg.language)
        hosted = bool(engine and engine.hosted)
        kind = _MODEL_KIND.get(cfg.engine)
        own = own_machines(engine)
        return {
            "engine": cfg.engine,
            "ready": problem is None,
            "problem": problem,
            "hosted": hosted,
            "location": None if engine is None else ("hosted" if hosted else "local"),
            # Stricter than "local": the words certainly stay on the owner's own machines
            # (Whisper, Parakeet, or a speech server the owner said is theirs or that is
            # this computer). A private app may dictate only then. Null when off.
            "own_machines": own,
            "model": model,
            "warm": kind is not None and self.models.warm(kind),
            # Parakeet only: whether its files are downloaded, how big, from where, and
            # whether the owner's "download now" is under way. Null for the others.
            "download": download,
            "key_set": self._core.secrets.get(KEY_NAME) is not None,
            "hosted_callers": list(cfg.hosted_callers),
            "language": cfg.language,
            "limits": {
                "max_seconds": cfg.max_seconds,
                "max_bytes": cfg.max_bytes,
                "busy_wait_seconds": cfg.busy_wait,
            },
            "accepted_types": list(ACCEPTED),
        }

    # --- getting a model ready -------------------------------------------------

    def prepare(self) -> dict[str, Any]:
        """Download and load the chosen model now, in the background, instead of on the
        first recording. Only Parakeet has this; it answers the new status."""
        engine = self._engine(self._config())
        if not isinstance(engine, ParakeetEngine):
            raise SpeechError(409, "Only Parakeet can be downloaded ahead of time. Choose "
                                   "Parakeet under Settings, Voice, 'Speech to text' first.")
        problem = engine.problem()
        if problem:
            raise SpeechError(503, problem)
        engine.prepare()
        return self.status()

    # --- transcription ----------------------------------------------------------

    def own_machines(self) -> bool | None:
        """Whether the engine in use keeps the words on the owner's own machines, or None
        when speech is off. Calls nothing."""
        return own_machines(self._engine(self._config()))

    async def transcribe(self, audio: bytes, content_type: str, *, caller: str,
                         language: str | None = None, want_words: bool = False,
                         actor: str = "system", max_seconds: int | None = None) -> Transcript:
        """Off the event loop, on this module's own few threads."""
        if not self._slots.acquire(blocking=False):
            raise SpeechError(503, "Too many recordings are being transcribed at once. Try "
                                   "again in a moment.")
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._pool, lambda: self.transcribe_now(
                    audio, content_type, caller=caller, language=language,
                    want_words=want_words, actor=actor, max_seconds=max_seconds))
        finally:
            self._slots.release()

    def transcribe_now(self, audio: bytes, content_type: str, *, caller: str,
                       language: str | None = None, want_words: bool = False,
                       actor: str = "system", max_seconds: int | None = None) -> Transcript:
        """The blocking form, for code already on a worker thread. ``max_seconds`` may
        ask for a shorter longest recording than the owner's Voice limit, never a longer
        one (an app's dictation is held to ``apps.dictate_max_s``)."""
        caller = check_caller(caller)
        content_type = content_type_of(content_type)
        language = check_language(language)
        if not isinstance(audio, bytes | bytearray) or not audio:
            raise SpeechError(422, "The recording is empty. Record something and send it again.")
        cfg = self._config()
        if max_seconds is not None:
            cfg = replace(cfg, max_seconds=max(1, min(cfg.max_seconds, int(max_seconds))))
        if len(audio) > cfg.max_bytes:
            raise too_large(cfg.max_bytes)
        engine = self._engine(cfg)
        if engine is None:
            raise SpeechError(409, "Speech to text is turned off. Choose an engine under "
                                   "Settings, Voice.")
        try:
            problem = engine.problem() or (None if language else _language_problem(cfg.language))
            if problem:
                raise SpeechError(503, problem, "unavailable")
            if engine.hosted and caller not in cfg.hosted_callers:
                raise SpeechError(
                    403, "The speech server in use is a hosted one, which means the audio "
                         f"leaves this computer, and '{caller}' is not allowed to do that. To "
                         f"allow it, add {caller} to 'Allowed to use a hosted speech server' "
                         "under Settings, Voice, or switch speech to this computer.",
                    "not_allowed")
            if content_type == "audio/wav" and (_wav_seconds(audio) or 0) > cfg.max_seconds:
                raise too_long(cfg.max_seconds)
            started = time.monotonic()
            transcript = engine.transcribe(bytes(audio), content_type,
                                           language or cfg.language or None, want_words)
            if transcript.duration > cfg.max_seconds:
                raise too_long(cfg.max_seconds)
        except SpeechError as exc:
            if exc.reason:
                self._core.tape.print("speech.failed", {
                    "engine": engine.name, "hosted": engine.hosted, "caller": caller,
                    "reason": exc.reason,
                }, actor=actor)
            raise
        # The receipt: that it happened, never what was said.
        self._core.tape.print("speech.transcribed", {
            "engine": engine.name, "hosted": engine.hosted, "caller": caller,
            "audio_seconds": transcript.duration,
            "processing_ms": int((time.monotonic() - started) * 1000),
        }, actor=actor)
        return transcript

    # --- the key ----------------------------------------------------------------

    def set_key(self, key: str | None, *, actor: str) -> bool:
        """Save or remove the speech server's key. Returns whether one is now set."""
        secrets = self._core.secrets
        if key is None:
            changed = secrets.delete(KEY_NAME)
        else:
            changed = secrets.get(KEY_NAME) != key
            secrets.set(KEY_NAME, key)
        if changed:
            self._core.tape.print("speech.key_changed", {"set": key is not None}, actor=actor)
        return key is not None


def own_machines(engine: Engine | None) -> bool | None:
    """Whether an engine keeps the words on the owner's own machines; None for none.
    Whisper and Parakeet run here. A speech server counts only when the owner said it is
    theirs or it is this computer: if it cannot be known, it is not."""
    if engine is None:
        return None
    if isinstance(engine, EndpointEngine):
        return bool(engine.own_machines)
    return not engine.hosted


def too_large(max_bytes: int) -> SpeechError:
    return SpeechError(413, "That recording is larger than the limit of "
                            f"{max_bytes // (1024 * 1024)} MB. Record a shorter piece, or raise "
                            "the limit under Settings, Voice.")


def service(core: Core) -> SpeechService:
    return core.service("speech", SpeechService)
