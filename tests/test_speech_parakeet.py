"""Parakeet, the second engine on this computer.

No test loads a real model, downloads anything or needs the add-on installed.
The four packages the ``speech-parakeet`` extra brings (onnx-asr, ONNX Runtime,
the Hugging Face downloader, PyAV) are stood in by small fakes put where an
import finds them, so the engine's own code runs exactly as it would for real:
the same imports, the same calls, the same arguments.
"""

from __future__ import annotations

import builtins
import importlib.machinery
import io
import sys
import threading
import time
import types
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from jimmyhq import speech
from jimmyhq.settings import SettingError
from jimmyhq.speech import audio as speech_audio
from jimmyhq.speech.engines import (
    PARAKEET_REPO,
    PARAKEET_VAD_FILE,
    PARAKEET_VAD_REPO,
    PARAKEET_VARIANTS,
    ModelHolder,
    ParakeetEngine,
)
from jimmyhq.speech.livedoor import LiveDoor, LiveLimits
from jimmyhq.speech.transcript import SpeechError
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
EXTRA = ("onnx_asr", "onnxruntime", "huggingface_hub", "av")


# --- the fake library -------------------------------------------------------------


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, None)
    return module


class FakeAsr:
    """onnx-asr's model: ``with_timestamps()`` and ``with_vad(vad).with_timestamps()``."""

    def __init__(self, library):
        self.library = library

    def with_timestamps(self):
        return FakeRecognizer(self.library, split=False)

    def with_vad(self, vad, **options):
        self.library.vad_used.append(vad)
        return SimpleNamespace(with_timestamps=lambda: FakeRecognizer(self.library, split=True))


class FakeRecognizer:
    def __init__(self, library, *, split):
        self.library, self.split = library, split

    def recognize(self, waveform, **kwargs):
        self.library.heard.append({"split": self.split, "samples": waveform, **kwargs})
        if self.library.fail_recognizing:
            raise RuntimeError("ONNXRuntimeError: describing the audio")
        if self.split:
            return iter(self.library.pieces)
        return self.library.whole


class FakeLibrary:
    """Everything the fakes were asked, and what they answer."""

    def __init__(self):
        self.loads: list[dict] = []
        self.vads: list[dict] = []
        self.vad_used: list[object] = []
        self.heard: list[dict] = []
        self.cached: set[tuple[str, str]] = set()
        self.opened: list[bytes] = []
        self.resamplers: list[dict] = []
        self.hold: threading.Event | None = None
        self.load_error: Exception | None = None
        self.fail_recognizing = False
        self.whole = SimpleNamespace(
            text="buy oat milk", tokens=[" buy", " oat", " mi", "lk"],
            timestamps=[0.0, 0.4, 0.8, 1.04], logprobs=[-0.1] * 4)
        self.pieces = [
            SimpleNamespace(start=1.0, end=6.0, text="buy oat milk", tokens=[" buy", " oat",
                            " milk"], timestamps=[0.0, 0.48, 0.96], logprobs=None),
            SimpleNamespace(start=25.0, end=30.0, text="on the way home.",
                            tokens=[" on", " the", " way", " home", "."],
                            timestamps=[0.0, 0.32, 0.64, 0.96, 1.28], logprobs=None),
        ]
        self.frames: object = None  # what PyAV's decode yields; None means "not audio"

    def install(self, monkeypatch):
        onnx_asr = _module("onnx_asr")

        def load_model(model, path=None, *, quantization=None, sess_options=None,
                       providers=None, **rest):
            self.loads.append({"model": model, "path": path, "quantization": quantization,
                               "threads": sess_options.intra_op_num_threads,
                               "providers": providers})
            if self.hold is not None:
                self.hold.wait(5)
            if self.load_error is not None:
                raise self.load_error
            return FakeAsr(self)

        def load_vad(model="silero", path=None, *, providers=None, **rest):
            self.vads.append({"model": model, "providers": providers})
            return SimpleNamespace(name="vad")

        onnx_asr.load_model, onnx_asr.load_vad = load_model, load_vad

        onnxruntime = _module("onnxruntime")

        class SessionOptions:
            intra_op_num_threads = -1

        onnxruntime.SessionOptions = SessionOptions

        hub = _module("huggingface_hub")
        hub.try_to_load_from_cache = lambda repo, name: (
            f"cache/{repo}/{name}" if (repo, name) in self.cached else None)

        av = _module("av")

        class InvalidDataError(ValueError):
            pass

        av.error = SimpleNamespace(InvalidDataError=InvalidDataError)
        library = self

        class Container:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def decode(self, audio=0):
                if library.frames is None:
                    raise InvalidDataError("Invalid data found when processing input")
                return library.frames(InvalidDataError) if callable(library.frames) \
                    else iter(library.frames)

        def av_open(file, mode="r"):
            self.opened.append(file.read())
            return Container(self.opened[-1])

        class AudioResampler:
            def __init__(self, format=None, layout=None, rate=None):
                library.resamplers.append({"format": format, "layout": layout, "rate": rate})

            def resample(self, frame):
                return [] if frame is None else [frame]

        av.open, av.AudioResampler = av_open, AudioResampler
        for name, module in (("onnx_asr", onnx_asr), ("onnxruntime", onnxruntime),
                             ("huggingface_hub", hub), ("av", av)):
            monkeypatch.setitem(sys.modules, name, module)
        self.InvalidDataError = InvalidDataError
        return self

    def download_everything(self, variant="int8"):
        self.cached |= {(PARAKEET_REPO, name) for name in PARAKEET_VARIANTS[variant].files}
        self.cached.add((PARAKEET_VAD_REPO, PARAKEET_VAD_FILE))


class Frame:
    """A decoded and resampled PyAV frame: 16-bit mono samples."""

    def __init__(self, samples):
        self.samples = np.asarray(samples, dtype=np.int16)
        self.pts = 1234

    def to_ndarray(self):
        return self.samples.reshape(1, -1)


def wav_of(seconds: float, rate: int = 16_000, width: int = 2, channels: int = 1,
           frames: bytes | None = None) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        silence = (b"\x80" if width == 1 else b"\x00" * width) * channels
        handle.writeframes(frames if frames is not None else silence * int(rate * seconds))
    return out.getvalue()


# --- fixtures -------------------------------------------------------------------


@pytest.fixture
def library(monkeypatch):
    return FakeLibrary().install(monkeypatch)


@pytest.fixture
def parakeet(core, library):
    core.settings.set("speech.engine", "parakeet", actor="owner")
    return library


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).status_code \
            == 200
        yield c


THREE_SECONDS = ("clip.wav", wav_of(3.0), "audio/wav")


def post(client, file=THREE_SECONDS, **fields):
    return client.post("/api/speech/transcribe", files={"file": file}, data=fields)


def receipts(core):
    return [e for e in core.tape.after(0) if e.kind.startswith("speech.")]


def wait_until(check, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


# --- choosing it --------------------------------------------------------------------


def test_parakeet_is_one_of_the_engines_and_the_setting_chooses_it(client, core, parakeet):
    engine = next(s for s in core.settings.describe()["settings"] if s["key"] == "speech.engine")
    assert "parakeet" in [c["value"] for c in engine["choices"]]
    assert "huggingface.co" in engine["help"] and "not uploaded" in engine["help"]
    status = client.get("/api/speech/status").json()
    assert status["engine"] == "parakeet" and status["ready"] is True
    assert status["hosted"] is False and status["location"] == "local"
    assert status["model"] == "parakeet-tdt-0.6b-v3 int8"
    r = post(client)
    assert r.status_code == 200 and r.json()["text"] == "buy oat milk"
    [receipt] = receipts(core)
    assert receipt.kind == "speech.transcribed"
    assert receipt.payload["engine"] == "parakeet" and receipt.payload["hosted"] is False


def test_without_the_add_on_the_status_and_a_recording_name_the_install_command(
        client, core, monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    core.settings.set("speech.engine", "parakeet", actor="owner")
    status = client.get("/api/speech/status").json()
    assert status["ready"] is False
    assert "uv sync --extra speech-parakeet" in status["problem"]
    assert status["download"]["present"] is None  # cannot be told without the add-on
    r = post(client)
    assert r.status_code == 503 and "uv sync --extra speech-parakeet" in r.json()["detail"]
    assert "not installed" in r.json()["detail"]
    assert [e.payload["reason"] for e in receipts(core)] == ["unavailable"]


def test_half_an_add_on_counts_as_not_installed(core, library, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr("importlib.util.find_spec",
                        lambda name: None if name == "av" else real(name))
    engine = ParakeetEngine(ModelHolder(), variant="int8", threads=0, busy_wait=0,
                            max_seconds=60)
    assert "uv sync --extra speech-parakeet" in engine.problem()


def test_nothing_from_the_add_on_is_imported_at_startup_or_by_the_status(
        core, library, monkeypatch):
    imported: list[str] = []
    real = builtins.__import__

    def spy(name, *args, **kwargs):
        if name.split(".")[0] in EXTRA:
            imported.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", spy)
    core.settings.set("speech.engine", "parakeet", actor="owner")
    with TestClient(create_app(core), base_url="http://testserver") as c:
        c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
        status = c.get("/api/speech/status").json()
    assert status["ready"] is True
    # The status reads the download cache, and does nothing else with the add-on.
    assert set(imported) <= {"huggingface_hub"}
    assert library.loads == [] and library.opened == []


# --- the download ---------------------------------------------------------------


def test_the_status_says_whether_the_model_is_downloaded_without_loading_it(
        client, core, parakeet):
    download = client.get("/api/speech/status").json()["download"]
    assert download == {"present": False, "megabytes": 670, "host": "huggingface.co",
                        "preparing": False, "problem": None}
    parakeet.download_everything("int8")
    assert client.get("/api/speech/status").json()["download"]["present"] is True
    core.settings.set("speech.parakeet_variant", "full", actor="owner")
    status = client.get("/api/speech/status").json()
    assert status["download"]["present"] is False and status["download"]["megabytes"] == 2550
    assert status["model"] == "parakeet-tdt-0.6b-v3"
    assert parakeet.loads == []


def test_the_other_engines_say_nothing_about_a_download(client, core, library):
    assert client.get("/api/speech/status").json()["download"] is None
    core.settings.set("speech.engine", "endpoint", actor="owner")
    assert client.get("/api/speech/status").json()["download"] is None


def test_the_model_is_not_downloaded_until_parakeet_is_chosen_and_a_recording_arrives(
        client, core, library):
    for engine in ("off", "endpoint"):
        core.settings.set("speech.engine", engine, actor="owner")
        client.get("/api/speech/status")
        post(client)
    assert library.loads == [] and library.vads == []
    core.settings.set("speech.engine", "parakeet", actor="owner")
    client.get("/api/speech/status")
    assert library.loads == []
    assert post(client).status_code == 200
    assert library.loads == [{"model": "nemo-parakeet-tdt-0.6b-v3", "path": None,
                              "quantization": "int8", "threads": 0,
                              "providers": ["CPUExecutionProvider"]}]
    assert library.vads == [{"model": "silero", "providers": ["CPUExecutionProvider"]}]


def test_the_model_is_loaded_once_and_kept_warm(client, core, parakeet):
    assert client.get("/api/speech/status").json()["warm"] is False
    for _ in range(3):
        assert post(client).status_code == 200
    assert len(parakeet.loads) == 1
    assert client.get("/api/speech/status").json()["warm"] is True


def test_the_full_size_and_the_thread_count_reach_onnx_asr(client, core, parakeet):
    core.settings.set("speech.parakeet_variant", "full", actor="owner")
    core.settings.set("speech.parakeet_threads", 4, actor="owner")
    assert post(client).status_code == 200
    assert parakeet.loads[-1]["quantization"] is None and parakeet.loads[-1]["threads"] == 4
    core.settings.set("speech.parakeet_threads", 2, actor="owner")
    post(client)
    assert len(parakeet.loads) == 2 and parakeet.loads[-1]["threads"] == 2


def test_download_now_loads_the_model_in_the_background_and_only_for_parakeet(
        client, core, library):
    r = client.post("/api/speech/prepare")
    assert r.status_code == 409 and "Parakeet" in r.json()["detail"]
    core.settings.set("speech.engine", "parakeet", actor="owner")
    library.hold = threading.Event()
    status = client.post("/api/speech/prepare").json()
    assert status["download"]["preparing"] is True
    assert wait_until(lambda: library.loads)
    # Asked twice while it runs, it is still one download.
    assert client.post("/api/speech/prepare").status_code == 200
    library.hold.set()
    assert wait_until(lambda: not client.get("/api/speech/status").json()["download"]
                      ["preparing"])
    status = client.get("/api/speech/status").json()
    assert status["warm"] is True and status["download"]["problem"] is None
    assert len(library.loads) == 1
    assert post(client).status_code == 200 and len(library.loads) == 1


def test_download_now_is_for_the_owner_alone(core, library):
    core.settings.set("speech.engine", "parakeet", actor="owner")
    with TestClient(create_app(core), base_url="http://testserver") as anonymous:
        assert anonymous.post("/api/speech/prepare").status_code in (401, 403)
    assert library.loads == []


def test_download_now_without_the_add_on_names_the_install_command(client, core, monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    core.settings.set("speech.engine", "parakeet", actor="owner")
    r = client.post("/api/speech/prepare")
    assert r.status_code == 503 and "uv sync --extra speech-parakeet" in r.json()["detail"]


def test_a_recording_during_the_download_is_told_the_model_is_still_coming(
        client, core, parakeet):
    core.settings.set("speech.busy_wait_seconds", 0, actor="owner")
    parakeet.hold = threading.Event()
    client.post("/api/speech/prepare")
    assert wait_until(lambda: parakeet.loads)
    r = post(client)
    parakeet.hold.set()
    assert r.status_code == 503 and "still being downloaded" in r.json()["detail"]
    assert [e.payload["reason"] for e in receipts(core)] == ["busy"]


def test_a_failed_download_is_explained_in_the_status_and_to_a_recording(
        client, core, parakeet):
    parakeet.load_error = OSError("Temporary failure in name resolution")
    client.post("/api/speech/prepare")
    assert wait_until(lambda: client.get("/api/speech/status").json()["download"]["problem"])
    problem = client.get("/api/speech/status").json()["download"]["problem"]
    assert "huggingface.co" in problem and "name resolution" not in problem
    r = post(client)
    assert r.status_code == 503 and "huggingface.co" in r.json()["detail"]
    assert [e.payload["reason"] for e in receipts(core)] == ["engine_error"]


# --- what comes back ----------------------------------------------------------------


def test_a_short_recording_is_heard_whole_and_its_word_pieces_become_words(
        client, core, parakeet):
    r = post(client, words="true")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "text": "buy oat milk", "language": "", "duration": 3.0,
        "segments": [{"start": 0.0, "end": 3.0, "text": "buy oat milk", "words": [
            {"start": 0.0, "end": 0.4, "word": "buy"},
            {"start": 0.4, "end": 0.8, "word": "oat"},
            {"start": 0.8, "end": 3.0, "word": "milk"},
        ]}],
    }
    [heard] = parakeet.heard
    assert heard["split"] is False and heard["sample_rate"] == 16_000
    assert heard["samples"].dtype == np.float32 and heard["samples"].shape == (48_000,)
    # Parakeet works out the language itself; it is never told one.
    assert set(heard) == {"split", "samples", "sample_rate"}


def test_without_words_asked_for_a_segment_carries_none(client, core, parakeet):
    body = post(client).json()
    assert "words" not in body["segments"][0]


def test_a_long_recording_is_cut_at_pauses_and_word_times_count_from_its_start(
        client, core, parakeet):
    r = post(client, file=("long.wav", wav_of(40.0, rate=8_000), "audio/wav"), words="true")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "buy oat milk on the way home." and body["duration"] == 40.0
    first, second = body["segments"]
    assert (first["start"], first["end"]) == (1.0, 6.0)
    assert [w["start"] for w in first["words"]] == [1.0, 1.48, 1.96]
    assert first["words"][-1]["end"] == 6.0
    assert [w["word"] for w in second["words"]] == ["on", "the", "way", "home."]
    assert second["words"][0] == {"start": 25.0, "end": 25.32, "word": "on"}
    [heard] = parakeet.heard
    assert heard["split"] is True and heard["sample_rate"] == 8_000
    assert len(parakeet.vad_used) == 1


def test_nothing_to_hear_answers_empty_text_without_loading_the_model(client, core, parakeet):
    body = post(client, file=("blip.wav", wav_of(0.05), "audio/wav")).json()
    assert body == {"text": "", "language": "", "duration": 0.05, "segments": []}
    assert parakeet.loads == []


def test_a_language_parakeet_does_not_know_is_refused_and_one_it_knows_is_not(
        client, core, parakeet):
    core.settings.set("speech.language", "ja", actor="owner")
    status = client.get("/api/speech/status").json()
    assert status["ready"] is False and "Whisper" in status["problem"]
    assert post(client, language="fr").status_code == 200  # the request's own language wins
    r = post(client, language="ja")
    assert r.status_code == 422 and "Parakeet does not know" in r.json()["detail"]
    core.settings.set("speech.language", "de", actor="owner")
    assert client.get("/api/speech/status").json()["ready"] is True


def test_a_failure_inside_the_model_says_the_recording_could_not_be_read(
        client, core, parakeet):
    parakeet.fail_recognizing = True
    r = post(client)
    assert r.status_code == 422 and "could not be read as audio" in r.json()["detail"]
    assert "ONNX" not in r.json()["detail"]
    parakeet.fail_recognizing = False
    assert post(client).status_code == 200  # the lock was let go


def test_a_second_recording_waits_for_the_busy_model_then_is_told_to_try_again(
        core, parakeet):
    holder = ModelHolder()
    engine = ParakeetEngine(holder, variant="int8", threads=0, busy_wait=0, max_seconds=60)
    with holder.lock:
        with pytest.raises(SpeechError) as caught:
            engine.transcribe(wav_of(2.0), "audio/wav", None, False)
    assert caught.value.status == 503 and "busy with another recording" in caught.value.detail


def test_switching_from_whisper_to_parakeet_lets_the_whisper_model_go(core, parakeet):
    holder = ModelHolder(lambda *settings: object())
    with holder.lock:
        holder.get("small", "auto", "default")
    assert holder.warm("whisper") is True
    engine = ParakeetEngine(holder, variant="int8", threads=0, busy_wait=1, max_seconds=60)
    engine.transcribe(wav_of(2.0), "audio/wav", None, False)
    assert holder.warm("whisper") is False and holder.warm("parakeet") is True


def test_the_live_door_can_run_on_parakeet_with_the_model_kept_warm(core, parakeet):
    service = speech.service(core)
    door = LiveDoor(lambda chunk: service.transcribe_now(chunk, "audio/wav",
                                                         caller="wearable").text,
                    LiveLimits(deadline=5, backlog=5, duplicate_window=0))
    try:
        answers = [door.submit(wav_of(5.0, frames=bytes([n]) * 2 * 80_000)) for n in range(3)]
    finally:
        door.close()
    assert [(a.status, a.text) for a in answers] == [("answered", "buy oat milk")] * 3
    assert len(parakeet.loads) == 1


# --- formats ------------------------------------------------------------------------


@pytest.mark.parametrize(("width", "frames", "expected"), [
    (1, bytes([0, 128, 255]), [-1.0, 0.0, 127 / 128]),
    (2, np.array([-32768, 0, 16384], "<i2").tobytes(), [-1.0, 0.0, 0.5]),
    (3, b"\x00\x00\x80" + b"\x00\x00\x00" + b"\x00\x00\x40", [-1.0, 0.0, 0.5]),
    (4, np.array([-2 ** 31, 0, 2 ** 30], "<i4").tobytes(), [-1.0, 0.0, 0.5]),
])
def test_a_plain_wav_is_read_with_the_standard_library(width, frames, expected):
    samples, rate = speech_audio.decode(wav_of(0, width=width, frames=frames), "audio/wav", 60)
    assert rate == 16_000 and samples.dtype == np.float32
    assert samples.tolist() == pytest.approx(expected)


def test_a_stereo_wav_is_mixed_down_to_one_channel():
    frames = np.array([1000, 3000, -2000, -4000], "<i2").tobytes()  # two stereo frames
    samples, _ = speech_audio.decode(wav_of(0, channels=2, frames=frames), "audio/wav", 60)
    assert samples.tolist() == pytest.approx([2000 / 32768, -3000 / 32768])


def test_a_wav_longer_than_the_limit_is_refused_from_its_header(library):
    with pytest.raises(SpeechError) as caught:
        speech_audio.decode(wav_of(61.0, rate=8_000), "audio/wav", 60)
    assert caught.value.status == 413 and caught.value.reason == "too_long"
    assert library.opened == []


def test_a_wav_at_a_rate_onnx_asr_cannot_take_goes_through_pyav_at_16_khz(library):
    library.frames = [Frame([16384] * 160)]
    samples, rate = speech_audio.decode(wav_of(1.0, rate=12_345), "audio/wav", 60)
    assert rate == 16_000 and len(library.opened) == 1
    assert samples.tolist() == pytest.approx([0.5] * 160)


@pytest.mark.parametrize("content_type", ["audio/webm", "audio/ogg", "audio/mp4"])
def test_what_the_dictate_button_records_is_decoded_by_pyav_to_16_khz_mono(
        client, core, parakeet, content_type):
    parakeet.frames = [Frame([0] * 16_000), Frame([8192] * 16_000)]
    r = post(client, file=("dictation.rec", b"browser recording", content_type))
    assert r.status_code == 200 and r.json()["duration"] == 2.0
    assert parakeet.opened == [b"browser recording"]
    assert parakeet.resamplers == [{"format": "s16", "layout": "mono", "rate": 16_000}]
    [heard] = parakeet.heard
    assert heard["sample_rate"] == 16_000 and heard["samples"].dtype == np.float32
    assert heard["samples"][-1] == pytest.approx(0.25)


def test_a_recording_pyav_cannot_read_is_refused_in_plain_words(client, core, parakeet):
    parakeet.frames = None
    r = post(client, file=("clip.webm", b"garbage", "audio/webm"))
    assert r.status_code == 422
    assert r.json()["detail"] == speech_audio.UNREADABLE
    assert [e.payload["reason"] for e in receipts(core)] == ["unreadable"]
    assert parakeet.loads == []  # refused before the model was needed


def test_decoding_stops_as_soon_as_the_recording_passes_the_limit(library):
    taken = []

    def endless(_error):
        while True:
            taken.append(1)
            yield Frame([0] * 16_000)

    library.frames = endless
    with pytest.raises(SpeechError) as caught:
        speech_audio.decode(b"a small file that unpacks to hours", "audio/webm", 5)
    assert caught.value.status == 413
    assert len(taken) == 6


def test_a_damaged_packet_in_the_middle_is_skipped_not_fatal(library):
    class Stream:
        def __init__(self, error):
            self.parts = iter([lambda: Frame([100] * 10), lambda: (_ for _ in ()).throw(
                error("bad packet")), lambda: Frame([200] * 10)])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.parts)()

    library.frames = Stream
    samples, _ = speech_audio.decode(b"webm", "audio/webm", 60)
    assert len(samples) == 20


# --- settings -----------------------------------------------------------------------


@pytest.mark.parametrize(("key", "value"), [
    ("speech.parakeet_variant", "huge"),
    ("speech.parakeet_variant", ""),
    ("speech.parakeet_threads", -1),
    ("speech.parakeet_threads", 1000),
    ("speech.parakeet_threads", "four"),
    ("speech.engine", "parakeet-v9"),
])
def test_a_parakeet_setting_outside_its_rules_is_refused(core, key, value):
    with pytest.raises(SettingError):
        core.settings.set(key, value, actor="owner")


def test_the_parakeet_settings_say_what_is_downloaded_and_keep_the_size_choice_in_sight(core):
    by_key = {s["key"]: s for s in core.settings.describe()["settings"]}
    variant = by_key["speech.parakeet_variant"]
    assert variant["default"] == "int8" and "advanced" not in variant
    assert "huggingface.co" in variant["help"]
    assert [c["value"] for c in variant["choices"]] == ["int8", "full"]
    assert by_key["speech.parakeet_threads"]["default"] == 0
