"""The speech-to-text engines.

An engine is anything with ``transcribe(audio, content_type, language,
want_words) -> Transcript``. There are three:

- ``LocalEngine`` runs faster-whisper in this process. The audio never leaves
  the machine. The package is an optional extra and is imported only when a
  recording actually arrives.
- ``ParakeetEngine`` runs NVIDIA's Parakeet TDT 0.6B v3 in this process on the
  processor, through onnx-asr and ONNX Runtime. Also an optional extra, also
  imported only when a recording arrives (or the owner asks for the download).
- ``EndpointEngine`` posts the audio to a server that speaks the OpenAI-style
  transcription API. That covers a Whisper server on another of the owner's
  machines as well as a hosted service, which is why it works out whether the
  address is "hosted".

Text-to-speech, when it comes, gets its own interface in its own file beside
this one; nothing here assumes speech only goes one way.
"""

from __future__ import annotations

import importlib.util
import io
import ipaddress
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from jimmyhq.speech.audio import decode, too_long, unreadable
from jimmyhq.speech.transcript import Segment, SpeechError, Transcript, Word, clean, tidy

__all__ = ["EndpointEngine", "Engine", "LocalEngine", "ModelHolder", "ParakeetEngine",
           "is_hosted_url", "is_this_machine", "too_long"]

INSTALL_COMMAND = "uv sync --extra speech"
KEY_NAME = "speech.endpoint.api_key"

# Names that only resolve on the owner's own machine or network.
_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home", ".home.arpa")

# What the model file is called on the way to the server: it reads the format
# from the file name as well as the content type.
EXTENSIONS = {
    "audio/webm": "webm", "audio/mp4": "mp4", "audio/wav": "wav", "audio/mpeg": "mp3",
    "audio/ogg": "ogg", "audio/flac": "flac", "audio/m4a": "m4a",
}


class Engine(Protocol):
    name: str
    hosted: bool

    def problem(self) -> str | None:
        """Why this engine cannot be used right now and what to do, or None.
        Must be cheap: no model load, no network."""

    def transcribe(self, audio: bytes, content_type: str, language: str | None,
                   want_words: bool) -> Transcript: ...


# --- on this computer ---------------------------------------------------------


def _load_faster_whisper(model: str, device: str, compute_type: str) -> Any:
    from faster_whisper import WhisperModel  # the optional "speech" extra

    return WhisperModel(model, device=device, compute_type=compute_type)


def _have(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


class ModelHolder:
    """The one warm model in this process, and the lock that guards it.

    Loading a model takes seconds and a lot of memory, so there is exactly one,
    kept between requests, whichever engine it belongs to: switching from
    Whisper to Parakeet lets the Whisper model go before Parakeet's loads. The
    model is not safe to run twice at once, so everything that touches it holds
    ``lock``. ``loader`` can be replaced, which is how tests stand in a fake
    Whisper without the real package.
    """

    def __init__(self, loader: Callable[[str, str, str], Any] | None = None):
        self.loader = loader
        self.lock = threading.Lock()
        self._model: Any = None
        self._key: tuple[str, ...] | None = None
        # A download or load the owner started ahead of time (Parakeet's button).
        self.preparing = False
        self.prepare_problem: str | None = None
        self.flag_lock = threading.Lock()  # guards ``preparing`` being set

    def installed(self) -> bool:
        return self.loader is not None or _have("faster_whisper")

    def warm(self, kind: str | None = None) -> bool:
        """Whether a model is loaded; with ``kind``, a model of that engine."""
        if self._model is None or self._key is None:
            return False
        return kind is None or self._key[0] == kind

    def get(self, model: str, device: str, compute_type: str) -> Any:
        """The loaded Whisper model for these settings. Call with ``lock`` held."""
        return self.hold(("whisper", model, device, compute_type),
                         lambda: (self.loader or _load_faster_whisper)(model, device,
                                                                       compute_type))

    def hold(self, key: tuple[str, ...], load: Callable[[], Any]) -> Any:
        """The model for ``key``, loaded if it is not the one in memory. Call with ``lock`` held.

        ``key[0]`` names the engine. Whatever was held before is let go first,
        so two large models are never in memory at once.
        """
        if self._model is None or self._key != key:
            self._model = None
            self._key = None
            self._model = load()
            self._key = key
        return self._model


class LocalEngine:
    name = "local"
    hosted = False

    def __init__(self, holder: ModelHolder, *, model: str, device: str, compute_type: str,
                 busy_wait: float, max_seconds: float):
        self._holder = holder
        self._model = model.strip()
        self._device = device
        self._compute_type = compute_type
        self._busy_wait = busy_wait
        self._max_seconds = max_seconds

    def problem(self) -> str | None:
        if not self._holder.installed():
            return ("Speech on this computer needs the speech add-on, which is not installed. "
                    f"Install it with `{INSTALL_COMMAND}` and restart JimmyHQ, or choose a "
                    "speech server under Settings, Voice.")
        if not self._model:
            return "No model is chosen. Enter one under Settings, Voice, 'Whisper model'."
        return None

    def transcribe(self, audio: bytes, content_type: str, language: str | None,
                   want_words: bool) -> Transcript:
        problem = self.problem()
        if problem:
            raise SpeechError(503, problem, "unavailable")
        if not self._holder.lock.acquire(timeout=max(0.0, self._busy_wait)):
            raise SpeechError(503, "The speech model is busy with another recording. "
                                   "Try again in a moment.", "busy")
        try:
            try:
                model = self._holder.get(self._model, self._device, self._compute_type)
            except ImportError:
                raise SpeechError(503, "The speech add-on could not be loaded. Reinstall it with "
                                       f"`{INSTALL_COMMAND}` and restart JimmyHQ.",
                                  "unavailable") from None
            except Exception as exc:  # noqa: BLE001 - whatever the loader throws, say what to do
                raise SpeechError(
                    503, f"The speech model '{self._model}' could not be loaded "
                         f"({type(exc).__name__}). Check the model name and the 'Run the model "
                         "on' choice under Settings, Voice.", "engine_error") from None
            try:
                # Decoded from memory: the recording is never written to disk.
                segments, info = model.transcribe(io.BytesIO(audio), language=language,
                                                  word_timestamps=want_words)
                duration = tidy(getattr(info, "duration", 0))
                if duration > self._max_seconds:
                    # Known after decoding and before the model runs, so an
                    # over-long recording costs almost nothing.
                    raise too_long(self._max_seconds)
                out = [_segment(s.start, s.end, s.text, _local_words(s) if want_words else None)
                       for s in segments]  # the model runs here, as this is read
            except SpeechError:
                raise
            except Exception:  # noqa: BLE001 - the message may describe the audio; keep it out
                raise SpeechError(422, "That recording could not be read as audio. Record it "
                                       "again, or send a different format.",
                                  "unreadable") from None
        finally:
            self._holder.lock.release()
        return _transcript(out, str(getattr(info, "language", "") or ""), duration)


def _local_words(segment: Any) -> list[Word]:
    return [Word(start=tidy(w.start), end=tidy(w.end), word=clean(w.word))
            for w in (getattr(segment, "words", None) or [])]


# --- Parakeet, on this computer -----------------------------------------------

PARAKEET_INSTALL_COMMAND = "uv sync --extra speech-parakeet"
# What the extra installs: onnx-asr, ONNX Runtime, the Hugging Face downloader, PyAV.
PARAKEET_MODULES = ("onnx_asr", "onnxruntime", "huggingface_hub", "av")
PARAKEET_MODEL = "nemo-parakeet-tdt-0.6b-v3"  # onnx-asr's name for the model
PARAKEET_SHOWN_AS = "parakeet-tdt-0.6b-v3"
PARAKEET_HOST = "huggingface.co"
# Where onnx-asr downloads from (its resolver's ``model_repos``) and which files it takes.
PARAKEET_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
PARAKEET_VAD = "silero"
PARAKEET_VAD_REPO = "istupakov/silero-vad-onnx"
PARAKEET_VAD_FILE = "silero_vad.onnx"  # about 2 MB


@dataclass(frozen=True)
class ParakeetVariant:
    quantization: str | None  # onnx-asr's ``quantization`` argument
    files: tuple[str, ...]  # in PARAKEET_REPO
    megabytes: int  # the download, rounded


PARAKEET_VARIANTS = {
    "int8": ParakeetVariant("int8", ("config.json", "vocab.txt", "encoder-model.int8.onnx",
                                     "decoder_joint-model.int8.onnx"), 670),
    "full": ParakeetVariant(None, ("config.json", "vocab.txt", "encoder-model.onnx",
                                   "encoder-model.onnx.data", "decoder_joint-model.onnx"), 2550),
}

# The model card's 25 languages. It works out which one it hears; it cannot be told.
PARAKEET_LANGUAGES = frozenset({
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt",
    "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
})

# onnx-asr: "The maximum audio length for most models is 20-30 seconds." Up to this
# long a recording is heard whole; anything longer is cut at pauses by a voice
# activity detector (Silero), whose pieces are at most this long by default.
PARAKEET_WHOLE_SECONDS = 20.0
_PARAKEET_SHORTEST = 0.1  # seconds; less than this has nothing to hear


@dataclass(frozen=True)
class ParakeetModel:
    whole: Any  # onnx-asr's model ``.with_timestamps()``
    split: Any  # the same model ``.with_vad(vad).with_timestamps()``


def parakeet_installed() -> bool:
    return all(_have(module) for module in PARAKEET_MODULES)


def parakeet_downloaded(variant: str) -> bool | None:
    """Whether this variant's files are already in the Hugging Face cache.

    Reads the cache and nothing else: no model is loaded and nothing is fetched.
    None when it cannot be told (the extra is not installed, or the cache could
    not be read).
    """
    chosen = PARAKEET_VARIANTS.get(variant)
    if chosen is None or not parakeet_installed():
        return None
    try:
        from huggingface_hub import try_to_load_from_cache  # in the optional extra
    except ImportError:
        return None
    try:
        wanted = [(PARAKEET_REPO, name) for name in chosen.files]
        wanted.append((PARAKEET_VAD_REPO, PARAKEET_VAD_FILE))
        return all(isinstance(try_to_load_from_cache(repo, name), str) for repo, name in wanted)
    except Exception:  # noqa: BLE001 - an unreadable cache is "cannot tell", not a failure
        return None


def _load_parakeet(variant: str, threads: int) -> ParakeetModel:
    """Load the model, downloading it into the Hugging Face cache the first time."""
    import onnx_asr  # the optional "speech-parakeet" extra
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = threads  # 0: ONNX Runtime uses one per physical core
    # The processor only: the default list can put Core ML or a graphics card first.
    providers = ["CPUExecutionProvider"]
    model = onnx_asr.load_model(PARAKEET_MODEL,
                                quantization=PARAKEET_VARIANTS[variant].quantization,
                                sess_options=options, providers=providers)
    vad = onnx_asr.load_vad(PARAKEET_VAD, providers=providers)
    return ParakeetModel(whole=model.with_timestamps(),
                         split=model.with_vad(vad).with_timestamps())


class ParakeetEngine:
    name = "parakeet"
    hosted = False

    def __init__(self, holder: ModelHolder, *, variant: str, threads: int, busy_wait: float,
                 max_seconds: float):
        self._holder = holder
        self._variant = variant
        self._threads = threads
        self._busy_wait = busy_wait
        self._max_seconds = max_seconds

    @property
    def _key(self) -> tuple[str, ...]:
        return ("parakeet", self._variant, str(self._threads))

    def problem(self) -> str | None:
        if not parakeet_installed():
            return ("Parakeet needs its speech add-on, which is not installed. Install it with "
                    f"`{PARAKEET_INSTALL_COMMAND}` and restart JimmyHQ, or choose another "
                    "engine under Settings, Voice.")
        if self._variant not in PARAKEET_VARIANTS:
            return "No Parakeet model size is chosen. Choose one under Settings, Voice."
        return None

    @staticmethod
    def language_problem(language: str | None) -> str | None:
        """Why Parakeet cannot be held to this language, or None. Empty means "work it out"."""
        if not language or language in PARAKEET_LANGUAGES:
            return None
        return (f"The spoken language is set to '{language}', which Parakeet does not know: "
                "it hears 25 European languages and works out which by itself. Leave the "
                "spoken language empty under Settings, Voice, or choose Whisper there for "
                "this language.")

    def download(self) -> dict[str, Any]:
        """What the Voice settings show about the model's files. Loads nothing."""
        chosen = PARAKEET_VARIANTS.get(self._variant)
        return {
            "present": parakeet_downloaded(self._variant),
            "megabytes": chosen.megabytes if chosen else None,
            "host": PARAKEET_HOST,
            "preparing": self._holder.preparing,
            "problem": self._holder.prepare_problem,
        }

    def shown_as(self) -> str:
        if self._variant == "full":
            return PARAKEET_SHOWN_AS
        return f"{PARAKEET_SHOWN_AS} {self._variant}"

    def prepare(self) -> bool:
        """Download and load the model on a thread of its own. False if already under way."""
        holder = self._holder
        with holder.flag_lock:
            if holder.preparing:
                return False
            holder.preparing = True
            holder.prepare_problem = None

        def run() -> None:
            try:
                with holder.lock:
                    self._model()
            except SpeechError as exc:
                holder.prepare_problem = exc.detail
            finally:
                holder.preparing = False

        threading.Thread(target=run, name="speech-prepare", daemon=True).start()
        return True

    def _model(self) -> ParakeetModel:
        """The loaded model. Call with the holder's lock held."""
        try:
            return self._holder.hold(self._key, lambda: _load_parakeet(self._variant,
                                                                        self._threads))
        except ImportError:
            raise SpeechError(503, "Parakeet's speech add-on could not be loaded. Reinstall it "
                                   f"with `{PARAKEET_INSTALL_COMMAND}` and restart JimmyHQ.",
                              "unavailable") from None
        except Exception as exc:  # noqa: BLE001 - whatever the loader throws, say what to do
            raise SpeechError(
                503, f"The Parakeet model could not be downloaded or loaded "
                     f"({type(exc).__name__}). The first time, it is downloaded from "
                     f"{PARAKEET_HOST}: check that this computer can reach it and has room "
                     "for it, then try again.", "engine_error") from None

    def transcribe(self, audio: bytes, content_type: str, language: str | None,
                   want_words: bool) -> Transcript:
        problem = self.problem()
        if problem:
            raise SpeechError(503, problem, "unavailable")
        refusal = self.language_problem(language)
        if refusal:
            raise SpeechError(422, refusal)
        # Decoded before the model is waited for: it needs no model and may run
        # beside another recording.
        samples, rate = decode(audio, content_type, self._max_seconds)
        duration = tidy(len(samples) / rate)
        if duration < _PARAKEET_SHORTEST:
            return _transcript([], "", duration)  # nothing to hear; no model needed
        if not self._holder.lock.acquire(timeout=max(0.0, self._busy_wait)):
            if self._holder.preparing:
                raise SpeechError(503, "The Parakeet model is still being downloaded or loaded. "
                                       "Try again when Settings, Voice says it is ready.", "busy")
            raise SpeechError(503, "The speech model is busy with another recording. "
                                   "Try again in a moment.", "busy")
        try:
            model = self._model()
            try:
                segments = _parakeet_segments(model, samples, rate, duration, want_words)
            except SpeechError:
                raise
            except Exception:  # noqa: BLE001 - the message may describe the audio; keep it out
                raise unreadable() from None
        finally:
            self._holder.lock.release()
        # Parakeet does not say which language it heard.
        return _transcript(segments, "", duration)


def _parakeet_segments(model: ParakeetModel, samples: Any, rate: int, duration: float,
                       want_words: bool) -> list[Segment]:
    if duration <= PARAKEET_WHOLE_SECONDS:
        # One TimestampedResult: text, tokens and when each token starts, in seconds.
        result = model.whole.recognize(samples, sample_rate=rate)
        return [_parakeet_segment(0.0, duration, result, want_words)]
    # TimestampedSegmentResults: as above, plus where the piece starts and ends.
    # A token's time counts from the start of its piece.
    return [_parakeet_segment(piece.start, piece.end, piece, want_words)
            for piece in model.split.recognize(samples, sample_rate=rate)]


def _parakeet_segment(start: object, end: object, result: Any, want_words: bool) -> Segment:
    segment = _segment(start, end, result.text, None)
    if want_words:
        segment.words = _parakeet_words(result.tokens, result.timestamps, segment.start,
                                        segment.end)
    return segment


def _parakeet_words(tokens: Any, stamps: Any, offset: float, end: float) -> list[Word]:
    """Words from word pieces. A piece that starts with a space starts a new word.

    onnx-asr gives when each piece starts but not when it ends, so a word is
    taken to end where the next one starts, and the last where its segment ends.
    """
    starts: list[list[Any]] = []  # [start, text]
    for token, at in zip(tokens or [], stamps or [], strict=False):
        piece = str(token)
        if not starts or piece.startswith(" "):
            starts.append([offset + tidy(at), piece])
        else:
            starts[-1][1] += piece
    words: list[Word] = []
    for index, (begin, text) in enumerate(starts):
        finish = starts[index + 1][0] if index + 1 < len(starts) else end
        word = clean(text)
        if word:
            words.append(Word(start=tidy(begin), end=tidy(max(begin, finish)), word=word))
    return words


# --- a speech server ----------------------------------------------------------


def is_hosted_url(url: str) -> bool:
    """True when the address is somebody else's machine.

    Not hosted: this computer, an address in a private or otherwise non-public
    range, a single-label name, or a name that only resolves on a home or
    office network. Anything else is assumed to leave the building.
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host.isdigit() or host.startswith("0x"):
            # A whole address written as one number (134744072, 0x8080808) is
            # still an address, and it cannot be judged here: assume the worst.
            return True
        return not (host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES))
    return (getattr(address, "ipv4_mapped", None) or address).is_global


def is_this_machine(url: str) -> bool:
    """True only when the address is certainly this computer: ``localhost`` or a loopback
    address. A name or an address on the owner's network may be someone else's machine
    on that network, so it is not counted (``is_hosted_url`` is the looser rule)."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (getattr(address, "ipv4_mapped", None) or address).is_loopback


class EndpointEngine:
    name = "endpoint"

    def __init__(self, *, url: str, model: str, key: str | None, location: str,
                 timeout: float, transport: httpx.BaseTransport | None = None):
        self._url = url.strip().rstrip("/")
        self._model = model.strip()
        self._key = key
        self._timeout = timeout
        self._transport = transport
        self.hosted = location == "hosted" or (location != "local" and is_hosted_url(self._url))
        # Known to be on the owner's own machines: the owner said so, or it is this
        # computer. A guess from the address alone is not enough (see is_this_machine).
        self.own_machines = location == "local" or (
            location != "hosted" and is_this_machine(self._url))

    def problem(self) -> str | None:
        if not self._url:
            return ("No speech server is set. Enter its address under Settings, Voice, "
                    "'Speech server address'.")
        parts = urlsplit(self._url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ("The speech server address is not a web address. It should look like "
                    "http://localhost:8000/v1. Correct it under Settings, Voice.")
        if not self._model:
            return "No speech server model is set. Enter one under Settings, Voice."
        if self.hosted and not self._key:
            return ("The speech server needs an API key and none is saved. Save one under "
                    "Settings, Voice.")
        return None

    def transcribe(self, audio: bytes, content_type: str, language: str | None,
                   want_words: bool) -> Transcript:
        problem = self.problem()
        if problem:
            raise SpeechError(503, problem, "unavailable")
        data: dict[str, Any] = {"model": self._model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        if want_words:
            # Asking for words alone drops the segments on some servers.
            data["timestamp_granularities[]"] = ["segment", "word"]
        name = f"audio.{EXTENSIONS.get(content_type, 'bin')}"
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        try:
            # Redirects are not followed: the audio goes to the address the
            # owner entered and nowhere else.
            with httpx.Client(transport=self._transport, timeout=self._timeout,
                              follow_redirects=False) as client:
                response = client.post(f"{self._url}/audio/transcriptions", data=data,
                                       files={"file": (name, audio, content_type)},
                                       headers=headers)
        except httpx.TimeoutException:
            raise SpeechError(504, "The speech server did not answer in time. Check that it is "
                                   "running, or raise the wait under Settings, Voice.",
                              "engine_error") from None
        except httpx.HTTPError:
            raise SpeechError(502, "The speech server could not be reached. Check the address "
                                   "under Settings, Voice, and that the server is running.",
                              "engine_error") from None
        if response.status_code >= 300:
            raise _endpoint_refusal(response.status_code)
        return _parse_verbose_json(response, want_words)


def _endpoint_refusal(status: int) -> SpeechError:
    # The server's own message is not passed on: it can quote what it was sent.
    if status in (401, 403):
        return SpeechError(502, "The speech server refused the API key. Save a working key "
                                "under Settings, Voice.", "engine_error")
    if status == 404:
        return SpeechError(502, "The speech server has nothing at that address. The address "
                                "under Settings, Voice should end where /audio/transcriptions "
                                "begins, usually at /v1.", "engine_error")
    if status == 413:
        return SpeechError(413, "The speech server says the recording is too large. Record a "
                                "shorter piece.", "too_long")
    if status == 429:
        return SpeechError(503, "The speech server is busy or over its limit. Try again in a "
                                "moment.", "busy")
    if status in (400, 415, 422):
        return SpeechError(422, "The speech server could not use that recording or the model "
                                "name. Check 'Speech server model' under Settings, Voice, or "
                                "record again.", "unreadable")
    return SpeechError(502, f"The speech server answered with an error ({status}). Check that "
                            "it is working, then try again.", "engine_error")


def _parse_verbose_json(response: httpx.Response, want_words: bool) -> Transcript:
    malformed = SpeechError(502, "The speech server answered in a form JimmyHQ does not "
                                 "understand. Check that the address under Settings, Voice points "
                                 "to an OpenAI-style transcription API.", "engine_error")
    try:
        body = response.json()
    except ValueError:
        raise malformed from None
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        raise malformed
    try:
        raw_segments = body.get("segments") or []
        raw_words = body.get("words") or []
        duration = tidy(body.get("duration", 0))
        segments = [_segment(s["start"], s["end"], s["text"], [] if want_words else None)
                    for s in raw_segments]
        words = [Word(start=tidy(w["start"]), end=tidy(w["end"]), word=clean(w["word"]))
                 for w in raw_words] if want_words else []
    except (TypeError, KeyError, AttributeError):
        raise malformed from None
    if not segments and body["text"].strip():
        # A server that only gives the text still yields one segment.
        end = max(duration, words[-1].end if words else 0.0)
        segments = [Segment(start=0.0, end=end, text=clean(body["text"]),
                            words=[] if want_words else None)]
    _share_out(words, segments)
    if not duration and segments:
        duration = segments[-1].end
    language = body.get("language")
    return _transcript(segments, language if isinstance(language, str) else "", duration,
                       text=body["text"])


def _share_out(words: list[Word], segments: list[Segment]) -> None:
    """The API returns words in one flat list; put each in the segment it falls in."""
    if not words or not segments:
        return
    index = 0
    for word in words:
        while index < len(segments) - 1 and word.start >= segments[index].end:
            index += 1
        segments[index].words.append(word)  # type: ignore[union-attr]


# --- shared -------------------------------------------------------------------


def _segment(start: object, end: object, text: object, words: list[Word] | None) -> Segment:
    if not isinstance(text, str):
        raise TypeError("segment text")
    begin = tidy(start)
    return Segment(start=begin, end=max(begin, tidy(end)), text=clean(text), words=words)


def _transcript(segments: list[Segment], language: str, duration: float, *,
                text: str | None = None) -> Transcript:
    segments = [s for s in segments if s.text]
    joined = " ".join(s.text for s in segments) if text is None else clean(text)
    return Transcript(text=joined, language=clean(language)[:40], duration=duration,
                      segments=segments)
