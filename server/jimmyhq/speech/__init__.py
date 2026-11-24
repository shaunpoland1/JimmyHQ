"""Speech: turning a recording into text.

Other modules call ``await transcribe(core, audio, content_type, caller=...)``.
The routes below are the same call for the browser. See docs/speech.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException, MultiPartParser

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.speech.livedoor import LiveAnswer, LiveDoor, LiveLimits
from jimmyhq.speech.service import (
    ACCEPTED,
    SpeechService,
    check_caller,
    check_language,
    content_type_of,
    service,
    too_large,
)
from jimmyhq.speech.transcript import Segment, SpeechError, Transcript, Word
from jimmyhq.web.deps import owner, reader

__all__ = [
    "ACCEPTED", "LiveAnswer", "LiveDoor", "LiveLimits", "Segment", "SpeechError",
    "SpeechService", "Transcript", "Word", "build", "service", "transcribe",
]

_FORM_SLACK = 64 * 1024  # room for the form's other fields and part headers
_TRUE, _FALSE = {"true", "1", "yes", "on"}, {"false", "0", "no", "off", ""}


async def transcribe(core: Core, audio: bytes, content_type: str, *, caller: str,
                     language: str | None = None, want_words: bool = False,
                     actor: str = "system") -> Transcript:
    """Transcribe a recording with the engine the owner chose.

    ``caller`` names the surface asking ("dictation", "recorder", ...): a hosted
    engine is only used for callers the owner has allowed. Raises ``SpeechError``
    (``status``, ``detail``) for anything that cannot be done. Keeps neither the
    audio nor the text; what the caller does with the words is its own decision.
    """
    return await service(core).transcribe(audio, content_type, caller=caller, language=language,
                                          want_words=want_words, actor=actor)


class KeyBody(BaseModel):
    key: str = Field(min_length=1, max_length=500)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.isascii() or not value.isprintable() or " " in value:
            raise ValueError("Paste the key exactly as the speech service gave it, with no "
                             "spaces or line breaks")
        return value


class _TooLarge(Exception):
    pass


async def _read_form(request: Request, max_bytes: int):
    """Parse the upload while enforcing the size limit on the way in.

    The body is never read past the limit, and the file stays in memory rather
    than spilling to a temporary file on disk: dictation keeps no audio.
    """
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise SpeechError(415, "Send the recording as multipart/form-data with the audio in a "
                               "field called file.")
    limit = max_bytes + _FORM_SLACK
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large(max_bytes)

    async def bounded():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise _TooLarge
            yield chunk

    parser = MultiPartParser(request.headers, bounded(), max_files=1, max_fields=6,
                             max_part_size=1024)
    parser.spool_max_size = limit + 1
    try:
        return await parser.parse()
    except _TooLarge:
        raise too_large(max_bytes) from None
    except (MultiPartException, KeyError, ValueError, UnicodeError):
        raise SpeechError(422, "The upload could not be read. Send one audio file in a field "
                               "called file, with at most the fields language, words, "
                               "caller and app.") from None


def _flag(value: object) -> bool:
    text = value.strip().lower() if isinstance(value, str) else None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise SpeechError(422, "words: Use true or false.")


def build(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/speech", tags=["speech"])
    speech = service(core)

    @router.get("/status")
    def status(_: Principal = Depends(reader)):  # noqa: B008
        return speech.status()

    @router.post("/transcribe")
    async def transcribe_upload(request: Request, who: Principal = Depends(owner)):  # noqa: B008
        # Owner only: this runs on the owner's engine, and a hosted one spends the
        # owner's key. The caller named in the form is the owner's own surface.
        try:
            max_bytes = await run_in_threadpool(speech.max_bytes)
            form = await _read_form(request, max_bytes)
            try:
                upload = form.get("file")
                if upload is None or isinstance(upload, str):
                    raise SpeechError(422, "file: Attach the recording in a form field called "
                                           "file.")
                # The cheap checks come before the file is copied out of the form.
                content_type = content_type_of(upload.content_type, upload.filename)
                caller = check_caller(form.get("caller", "dictation"))
                language = check_language(form.get("language"))
                want_words = _flag(form.get("words", ""))
                app = form.get("app")
                max_seconds = None
                if app is not None:
                    # An app's page asked the shell to dictate (hq.speech.dictate). The
                    # app's own rules decide: its permission, its privacy, its limit; the
                    # caller is the app, whatever the form said.
                    from jimmyhq.apps import dictate  # noqa: PLC0415 - apps is optional here

                    rule = await run_in_threadpool(dictate.check, core, app)
                    caller, max_seconds = rule.caller, rule.max_seconds
                audio = await upload.read()
            finally:
                await form.close()
            if len(audio) > max_bytes:
                raise too_large(max_bytes)
            result = await speech.transcribe(audio, content_type, caller=caller,
                                             language=language, want_words=want_words,
                                             actor=who.actor, max_seconds=max_seconds)
        except SpeechError as exc:
            raise HTTPException(exc.status, exc.detail) from None
        return result.public()

    @router.post("/prepare")
    def prepare(_: Principal = Depends(owner)):  # noqa: B008
        # Owner only: it downloads hundreds of megabytes and fills the memory.
        try:
            return speech.prepare()
        except SpeechError as exc:
            raise HTTPException(exc.status, exc.detail) from None

    @router.put("/key")
    def key_set(body: KeyBody, who: Principal = Depends(owner)):  # noqa: B008
        return {"key_set": speech.set_key(body.key, actor=who.actor)}

    @router.delete("/key")
    def key_clear(who: Principal = Depends(owner)):  # noqa: B008
        return {"key_set": speech.set_key(None, actor=who.actor)}

    return router
