"""Speech-to-text: the routes, the rules around them and the two engines.

No test loads a real model or touches a network. The local engine runs a fake
model through the same loader hook the real one uses, and the endpoint engine
talks to an httpx.MockTransport.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import wave
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from jimmyhq import speech
from jimmyhq.speech.engines import (
    KEY_NAME,
    EndpointEngine,
    LocalEngine,
    ModelHolder,
    is_hosted_url,
)
from jimmyhq.speech.transcript import SpeechError
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
WEBM = ("clip.webm", b"not really audio", "audio/webm;codecs=opus")
SPOKEN = "buy oat milk on the way home"


# --- fakes ----------------------------------------------------------------------


class FakeModel:
    """Stands in for faster-whisper's WhisperModel."""

    def __init__(self, text=SPOKEN, duration=4.0, hold: threading.Event | None = None):
        self.text, self.duration, self.hold = text, duration, hold
        self.calls: list[dict] = []
        self.running = threading.Event()

    def transcribe(self, audio, language=None, word_timestamps=False):
        data = audio.read()
        if data == b"garbage":
            raise ValueError("Invalid data found when processing input")
        self.calls.append({"language": language, "words": word_timestamps, "bytes": len(data)})
        info = SimpleNamespace(language=language or "en", duration=self.duration)

        def segments():
            self.running.set()
            if self.hold is not None:
                self.hold.wait(5)
            words = [SimpleNamespace(start=i * 0.5, end=i * 0.5 + 0.4, word=f" {w}")
                     for i, w in enumerate(self.text.split())] if word_timestamps else None
            yield SimpleNamespace(start=0.0, end=self.duration, text=f" {self.text} ", words=words)

        return segments(), info


def verbose_json(**changes):
    body = {
        "task": "transcribe", "language": "english", "duration": 4.0, "text": SPOKEN,
        "segments": [{"id": 0, "start": 0.0, "end": 1.5, "text": " buy oat milk"},
                     {"id": 1, "start": 1.5, "end": 4.0, "text": " on the way home"}],
        "words": [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
                  for i, w in enumerate(SPOKEN.split())],
    }
    body.update(changes)
    return body


def endpoint(handler, *, url="https://api.example.com/v1", key="sk-test", location="auto"):
    return EndpointEngine(url=url, model="whisper-1", key=key, location=location, timeout=5,
                          transport=httpx.MockTransport(handler))


def wav_of(seconds: float) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * int(8000 * seconds))
    return out.getvalue()


# --- fixtures -------------------------------------------------------------------


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).status_code \
            == 200
        yield c


@pytest.fixture
def model(core):
    """The local engine switched on, with a fake model behind it."""
    fake = FakeModel()
    speech.service(core).models.loader = lambda *settings: fake
    core.settings.set("speech.engine", "local", actor="owner")
    return fake


def use_endpoint(core, handler, *, url="https://api.example.com/v1", key="sk-test"):
    core.settings.set("speech.engine", "endpoint", actor="owner")
    core.settings.set("speech.endpoint_url", url, actor="owner")
    if key:
        core.secrets.set(KEY_NAME, key)
    speech.service(core).transport = httpx.MockTransport(handler)


def post(client, file=WEBM, **fields):
    return client.post("/api/speech/transcribe", files={"file": file}, data=fields)


def receipts(core):
    return [e for e in core.tape.after(0) if e.kind.startswith("speech.")]


def token_client(core, scopes):
    _, secret = core.auth.create_token("probe", scopes)
    c = TestClient(create_app(core), base_url="http://testserver")
    c.headers["authorization"] = f"Bearer {secret}"
    return c


# --- status ---------------------------------------------------------------------


def test_a_new_install_has_speech_off_and_status_says_how_to_turn_it_on(client):
    status = client.get("/api/speech/status").json()
    assert status["engine"] == "off" and status["ready"] is False
    assert "Settings, Voice" in status["problem"]
    assert status["hosted"] is False and status["location"] is None
    assert status["limits"] == {"max_seconds": 300, "max_bytes": 25 * 1024 * 1024,
                                "busy_wait_seconds": 20.0}
    assert "audio/webm" in status["accepted_types"] and "audio/mp4" in status["accepted_types"]


def test_status_names_the_install_command_when_the_local_add_on_is_missing(client, core,
                                                                          monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    core.settings.set("speech.engine", "local", actor="owner")
    status = client.get("/api/speech/status").json()
    assert status["ready"] is False and "uv sync --extra speech" in status["problem"]
    r = post(client)
    assert r.status_code == 503 and "uv sync --extra speech" in r.json()["detail"]
    assert [e.payload["reason"] for e in receipts(core)] == ["unavailable"]


def test_status_does_not_load_the_model(client, core):
    loaded = []
    speech.service(core).models.loader = lambda *s: loaded.append(s) or FakeModel()
    core.settings.set("speech.engine", "local", actor="owner")
    status = client.get("/api/speech/status").json()
    assert status["ready"] is True and status["warm"] is False and status["location"] == "local"
    assert loaded == []


def test_status_says_a_hosted_server_is_hosted_and_whether_a_key_is_set_but_never_the_key(
        client, core):
    use_endpoint(core, lambda request: httpx.Response(500), key=None)
    status = client.get("/api/speech/status").json()
    assert status["hosted"] is True and status["location"] == "hosted"
    assert status["ready"] is False and "API key" in status["problem"]
    assert client.put("/api/speech/key", json={"key": "sk-very-secret"}).json() == {
        "key_set": True}
    status = client.get("/api/speech/status")
    assert status.json()["ready"] is True and status.json()["key_set"] is True
    assert "sk-very-secret" not in status.text


def test_status_needs_a_reader_and_transcribing_needs_the_owner(client, core, model):
    nobody = TestClient(create_app(core), base_url="http://testserver")
    assert nobody.get("/api/speech/status").status_code == 401
    assert post(nobody).status_code == 401
    reader = token_client(core, ["read"])
    assert reader.get("/api/speech/status").status_code == 200
    assert post(reader).status_code == 403
    assert reader.put("/api/speech/key", json={"key": "sk-test"}).status_code == 403
    assert post(token_client(core, ["read", "write"])).status_code == 403
    assert model.calls == []
    assert post(client).status_code == 200


def test_a_token_cannot_spend_the_owners_hosted_speech_key_by_naming_an_allowed_caller(
        client, core):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json=verbose_json())

    use_endpoint(core, handler, key="sk-owner-secret")
    core.settings.set("speech.hosted_callers", ["dictation"], actor="owner")
    token = token_client(core, ["write"])
    for fields in ({}, {"caller": "dictation"}):
        assert post(token, **fields).status_code == 403
    assert sent == []
    assert receipts(core) == []
    assert post(client).status_code == 200  # the owner's own dictation still works
    assert len(sent) == 1


def signed(who, rest):
    """An address with a name or password in front of the host."""
    return "https://" + who + "\x40" + rest


@pytest.mark.parametrize("address", [
    signed("user:hunter2", "speech.example.com/v1"),
    signed("user", "speech.example.com/v1"),
    signed(":hunter2", "speech.example.com/v1"),
    "https://speech.example.com/v1?api_key=abc",
    "https://speech.example.com/v1#key=abc",
    signed("user:hunter2", "192.0.2.7:8000/v1?api_key=abc"),
])
def test_a_speech_server_address_holding_a_name_or_key_is_refused_and_never_reaches_the_tape(
        client, core, address):
    r = client.put("/api/settings/speech.endpoint_url", json={"value": address})
    assert r.status_code == 422
    assert "key field" in r.text
    assert "hunter2" not in r.text and "abc" not in r.text
    assert core.settings.get("speech.endpoint_url") == ""
    on_tape = json.dumps([e.payload for e in core.tape.after(0)])
    assert "hunter2" not in on_tape and "api_key" not in on_tape and "key=abc" not in on_tape


def test_a_name_and_password_written_with_percent_signs_in_the_host_is_refused_too(client, core):
    address = "https://user%3Ahunter2%40speech.example.com/v1"
    r = client.put("/api/settings/speech.endpoint_url", json={"value": address})
    assert r.status_code == 422
    assert "key field" in r.text and "hunter2" not in r.text
    assert "hunter2" not in json.dumps([e.payload for e in core.tape.after(0)])
    # A percent sign in the path is an ordinary address and is still saved.
    plain = "http://localhost:8000/speech%20box/v1"
    assert client.put("/api/settings/speech.endpoint_url", json={"value": plain}).status_code == 200
    assert core.settings.get("speech.endpoint_url") == plain


def test_a_speech_server_address_with_an_invisible_character_is_refused(client):
    r = client.put("/api/settings/speech.endpoint_url",
                   json={"value": "http://speech.example.com\u200b/v1"})
    assert r.status_code == 422
    assert "invisible" in r.text


def test_an_address_with_a_password_stored_before_the_rule_reads_as_not_set_and_is_never_used(
        client, core):
    sent = []
    use_endpoint(core, lambda request: sent.append(request) or httpx.Response(500))
    core.settings.set("speech.hosted_callers", ["dictation"], actor="owner")
    core.tape.print("setting.updated", {
        "key": "speech.endpoint_url", "value": signed("user:hunter2", "api.example.com/v1"),
    }, actor="owner", subject="setting:speech.endpoint_url")
    assert core.settings.get("speech.endpoint_url") == ""
    assert "hunter2" not in client.get("/api/settings").text
    status = client.get("/api/speech/status").json()
    assert status["ready"] is False and "Speech server address" in status["problem"]
    assert post(client).status_code == 503
    assert sent == []


@pytest.mark.parametrize("address", ["ftp://speech.example.com/v1", "speech.example.com/v1",
                                     "http://", "http://speech.example.com:port/v1",
                                     "http://speech.example.com/v1 x"])
def test_a_speech_server_address_that_is_not_a_web_address_is_refused(client, address):
    r = client.put("/api/settings/speech.endpoint_url", json={"value": address})
    assert r.status_code == 422
    assert "Speech server address" in r.text


def test_a_plain_speech_server_address_is_saved_and_an_empty_one_clears_it(client, core):
    for address in ("http://localhost:8000/v1", "https://api.example.com/v1",
                    "http://[::1]:8000/v1", ""):
        r = client.put("/api/settings/speech.endpoint_url", json={"value": f" {address} "})
        assert r.status_code == 200, r.text
        assert core.settings.get("speech.endpoint_url") == address


# --- transcribing ---------------------------------------------------------------


def test_a_recording_comes_back_as_text_with_timed_segments(client, model):
    r = post(client)
    assert r.status_code == 200
    assert r.json() == {
        "text": SPOKEN, "language": "en", "duration": 4.0,
        "segments": [{"start": 0.0, "end": 4.0, "text": SPOKEN}],
    }
    assert model.calls == [{"language": None, "words": False, "bytes": len(WEBM[1])}]


def test_word_timings_are_included_only_when_asked_for(client, model):
    segment = post(client, words="true", language="en").json()["segments"][0]
    assert segment["words"][0] == {"start": 0.0, "end": 0.4, "word": "buy"}
    assert len(segment["words"]) == len(SPOKEN.split())
    assert model.calls[-1]["words"] is True and model.calls[-1]["language"] == "en"


def test_the_language_setting_is_used_when_the_request_names_none(client, core, model):
    core.settings.set("speech.language", "fr", actor="owner")
    assert post(client).json()["language"] == "fr"
    assert post(client, language="de").json()["language"] == "de"


def test_the_receipt_records_who_and_how_long_but_never_the_words(client, core, model):
    assert post(client, caller="notes").status_code == 200
    [receipt] = receipts(core)
    assert receipt.kind == "speech.transcribed" and receipt.actor == "owner"
    assert receipt.payload["engine"] == "local" and receipt.payload["hosted"] is False
    assert receipt.payload["caller"] == "notes" and receipt.payload["audio_seconds"] == 4.0
    assert set(receipt.payload) == {"engine", "hosted", "caller", "audio_seconds",
                                    "processing_ms"}
    everything = json.dumps([e.model_dump() for e in core.tape.after(0)])
    for word in ("oat", "milk", SPOKEN):
        assert word not in everything


def test_nothing_of_the_recording_is_left_in_the_data_folder(client, core, model):
    marker = b"MARKER-" + bytes(range(256)) * 8
    assert post(client, file=("clip.webm", marker, "audio/webm")).status_code == 200
    for path in core.config.data.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert b"MARKER-" not in content and SPOKEN.encode() not in content, path


def test_transcribing_is_refused_while_speech_is_off(client, core):
    r = post(client)
    assert r.status_code == 409 and "Settings, Voice" in r.json()["detail"]
    assert receipts(core) == []


@pytest.mark.parametrize("content_type", [
    "audio/webm", "audio/webm;codecs=opus", "audio/mp4", "audio/wav", "audio/x-wav",
    "audio/mpeg", "audio/mp3", "audio/ogg; codecs=opus", "audio/flac", "audio/x-m4a", "AUDIO/MP4",
])
def test_the_formats_browsers_and_recorders_produce_are_accepted(client, model, content_type):
    assert post(client, file=("clip", b"audio", content_type)).status_code == 200


def test_a_file_with_no_stated_type_is_judged_by_its_name(client, model):
    assert post(client, file=("clip.m4a", b"audio", "application/octet-stream")).status_code == 200
    assert post(client, file=("clip.exe", b"audio", "application/octet-stream")).status_code == 415


@pytest.mark.parametrize("content_type", ["text/plain", "image/png", "audio/midi", "nonsense"])
def test_other_kinds_of_file_are_refused_with_the_list_of_accepted_ones(client, model,
                                                                       content_type):
    r = post(client, file=("clip.webm", b"audio", content_type))
    assert r.status_code == 415
    assert "audio/webm" in r.json()["detail"] and "audio/mp4" in r.json()["detail"]
    assert model.calls == []


def test_an_upload_over_the_size_limit_is_refused(client, core, model):
    core.settings.set("speech.max_megabytes", 1, actor="owner")
    r = post(client, file=("clip.webm", b"x" * (1024 * 1024 + 1), "audio/webm"))
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    assert post(client, file=("clip.webm", b"x" * (1024 * 1024), "audio/webm")).status_code == 200
    assert len(model.calls) == 1


def test_an_oversized_upload_is_cut_off_while_it_is_being_read(client, core, model):
    core.settings.set("speech.max_megabytes", 1, actor="owner")
    def body():  # no Content-Length, so only reading it can reveal the size
        yield (b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.webm"\r\n'
               b"Content-Type: audio/webm\r\n\r\n")
        for _ in range(64):
            yield b"x" * (256 * 1024)
        yield b"\r\n--b--\r\n"

    r = client.post("/api/speech/transcribe", content=body(),
                    headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413
    assert model.calls == []


def test_a_recording_longer_than_the_limit_is_refused_before_the_model_runs(client, core, model):
    core.settings.set("speech.max_seconds", 5, actor="owner")
    model.duration = 6.0
    r = post(client)
    assert r.status_code == 413 and "5 seconds" in r.json()["detail"]
    assert not model.running.is_set()
    assert [e.payload["reason"] for e in receipts(core)] == ["too_long"]


def test_an_over_long_wav_is_refused_without_reaching_any_engine(client, core, model):
    core.settings.set("speech.max_seconds", 5, actor="owner")
    r = post(client, file=("clip.wav", wav_of(6), "audio/wav"))
    assert r.status_code == 413
    assert model.calls == []


def test_a_recording_the_engine_cannot_read_is_a_readable_refusal_not_a_crash(client, core, model):
    r = post(client, file=("clip.webm", b"garbage", "audio/webm"))
    assert r.status_code == 422 and "could not be read as audio" in r.json()["detail"]
    assert "Invalid data" not in r.text
    assert [e.payload["reason"] for e in receipts(core)] == ["unreadable"]


@pytest.mark.parametrize(("fields", "word"), [
    ({"language": "english"}, "language"), ({"language": "e"}, "language"),
    ({"words": "maybe"}, "words"), ({"caller": "Not A Name"}, "caller"),
    ({"caller": "x" * 41}, "caller"), ({"caller": ""}, "caller"),
])
def test_odd_form_fields_are_refused_with_the_field_named(client, model, fields, word):
    r = post(client, **fields)
    assert r.status_code == 422 and r.json()["detail"].startswith(word)


def test_odd_uploads_are_refused_readably(client, model):
    url = "/api/speech/transcribe"
    assert client.post(url, json={"file": "x"}).status_code == 415
    assert client.post(url).status_code == 415
    assert client.post(url, data={"caller": "dictation"},
                       files={"other": WEBM}).status_code == 422
    assert client.post(url, files={"file": ("clip.webm", b"", "audio/webm")}).status_code == 422
    assert client.post(url, files=[("file", WEBM), ("file", WEBM)]).status_code == 422
    assert client.post(url, content=b"--b\r\nbroken",
                       headers={"content-type": "multipart/form-data; boundary=b"}
                       ).status_code == 422
    assert client.post(url, content=b"x",
                       headers={"content-type": "multipart/form-data"}).status_code == 422
    assert client.post(url, files={"file": WEBM},
                       data={"language": "x" * 5000}).status_code == 422
    for r in (client.post(url), client.post(url, files={"other": WEBM})):
        assert isinstance(r.json()["detail"], str)
    assert model.calls == []


def test_a_second_recording_waits_for_a_busy_model_then_is_told_to_try_again(client, core):
    release = threading.Event()
    fake = FakeModel(hold=release)
    speech.service(core).models.loader = lambda *s: fake
    core.settings.set("speech.engine", "local", actor="owner")
    core.settings.set("speech.busy_wait_seconds", 0.05, actor="owner")
    first: dict = {}
    thread = threading.Thread(target=lambda: first.update(r=post(client)))
    thread.start()
    try:
        assert fake.running.wait(5)
        second = post(client)
        assert second.status_code == 503 and "busy" in second.json()["detail"]
    finally:
        release.set()
        thread.join(5)
    assert first["r"].status_code == 200
    assert sorted(e.kind for e in receipts(core)) == ["speech.failed", "speech.transcribed"]


def test_the_model_is_loaded_once_and_kept_warm(client, core):
    loads = []
    fake = FakeModel()
    speech.service(core).models.loader = lambda *s: loads.append(s) or fake
    core.settings.set("speech.engine", "local", actor="owner")
    assert post(client).status_code == 200 and post(client).status_code == 200
    assert loads == [("small", "auto", "default")]
    assert client.get("/api/speech/status").json()["warm"] is True
    core.settings.set("speech.local_model", "tiny", actor="owner")
    assert post(client).status_code == 200
    assert [s[0] for s in loads] == ["small", "tiny"]


def test_a_model_that_will_not_load_says_which_setting_to_check(client, core):
    def broken(*settings):
        raise RuntimeError("no such model anywhere")

    speech.service(core).models.loader = broken
    core.settings.set("speech.engine", "local", actor="owner")
    r = post(client)
    assert r.status_code == 503 and "'small'" in r.json()["detail"]
    assert "Settings, Voice" in r.json()["detail"]


# --- the hosted-permission rule ---------------------------------------------------


def test_a_hosted_engine_is_refused_for_a_caller_the_owner_has_not_allowed(client, core):
    calls = []
    use_endpoint(core, lambda request: calls.append(1) or httpx.Response(200, json=verbose_json()))
    r = post(client)
    assert r.status_code == 403
    assert "Allowed to use a hosted speech server" in r.json()["detail"]
    assert "Settings, Voice" in r.json()["detail"]
    assert calls == []  # the audio went nowhere
    [failed] = receipts(core)
    assert failed.kind == "speech.failed" and failed.payload == {
        "engine": "endpoint", "hosted": True, "caller": "dictation", "reason": "not_allowed"}


def test_each_caller_is_allowed_to_use_a_hosted_engine_separately(client, core):
    use_endpoint(core, lambda request: httpx.Response(200, json=verbose_json()))
    core.settings.set("speech.hosted_callers", ["dictation"], actor="owner")
    assert post(client).json()["text"] == SPOKEN
    assert post(client, caller="recorder").status_code == 403
    assert receipts(core)[0].payload["hosted"] is True


def test_a_speech_server_on_this_machine_needs_no_permission_and_no_key(client, core):
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json=verbose_json())

    use_endpoint(core, handler, url="http://localhost:8000/v1", key=None)
    assert client.get("/api/speech/status").json()["location"] == "local"
    assert post(client).status_code == 200
    assert seen == [None]


def test_the_owner_can_overrule_where_the_speech_server_is_judged_to_be(client, core):
    use_endpoint(core, lambda request: httpx.Response(200, json=verbose_json()))
    core.settings.set("speech.endpoint_location", "local", actor="owner")
    assert post(client).status_code == 200
    core.settings.set("speech.endpoint_url", "http://localhost:8000/v1", actor="owner")
    core.settings.set("speech.endpoint_location", "hosted", actor="owner")
    assert post(client).status_code == 403


def private(*parts):
    return "http://" + ".".join(str(p) for p in parts) + ":9000/v1"


@pytest.mark.parametrize("url", [
    "http://localhost:8000/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1",
    private(10, 1, 2, 3), private(192, 168, 1, 20), private(172, 16, 0, 9),
    private(100, 64, 0, 7), private(169, 254, 1, 1), "http://whisperbox:9000/v1",
    "http://speech" + ".local/v1", "http://nas" + ".lan/v1", "http://app.internal/v1",
    "http://LOCALHOST/v1",
])
def test_addresses_on_this_machine_or_network_are_not_hosted(url):
    assert is_hosted_url(url) is False


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1", "https://203.0.113.9.example.net/v1", "http://8.8.8.8/v1",
    "https://localhost.example.com/v1", "not a url", "",
])
def test_any_other_address_is_treated_as_hosted(url):
    assert is_hosted_url(url) is True


# --- the key ----------------------------------------------------------------------


def test_the_key_is_kept_in_the_secret_store_and_off_the_tape(client, core):
    assert client.put("/api/speech/key", json={"key": "  sk-very-secret  "}).json() == {
        "key_set": True}
    assert core.secrets.get(KEY_NAME) == "sk-very-secret"
    assert client.delete("/api/speech/key").json() == {"key_set": False}
    assert core.secrets.get(KEY_NAME) is None
    assert client.delete("/api/speech/key").json() == {"key_set": False}
    assert [e.payload for e in receipts(core)] == [{"set": True}, {"set": False}]
    assert "sk-very-secret" not in json.dumps([e.model_dump() for e in core.tape.after(0)])


@pytest.mark.parametrize("body", [{}, {"key": ""}, {"key": "   "}, {"key": "two words"},
                                  {"key": "x" * 501}, {"key": 5}, {"key": "line\nbreak"},
                                  {"key": "café"}])
def test_an_unusable_key_is_refused_without_being_echoed(client, core, body):
    r = client.put("/api/speech/key", json=body)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    sent = str(body.get("key", "")).strip()
    assert not sent or sent not in r.text
    assert core.secrets.get(KEY_NAME) is None


# --- the endpoint engine ----------------------------------------------------------


def test_the_endpoint_engine_posts_an_openai_style_request():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        seen["type"] = request.headers["content-type"]
        return httpx.Response(200, json=verbose_json())

    result = endpoint(handler, url="https://api.example.com/v1/").transcribe(
        b"AUDIOBYTES", "audio/webm", "en", True)
    assert seen["url"] == "https://api.example.com/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer sk-test" and seen["type"].startswith("multipart/form-data")
    body = seen["body"].decode("latin-1")
    for part in ('name="model"\r\n\r\nwhisper-1', 'name="response_format"\r\n\r\nverbose_json',
                 'name="language"\r\n\r\nen', 'name="timestamp_granularities[]"\r\n\r\nsegment',
                 'name="timestamp_granularities[]"\r\n\r\nword', 'filename="audio.webm"',
                 "Content-Type: audio/webm", "AUDIOBYTES"):
        assert part in body, part
    assert result.text == SPOKEN and result.language == "english" and result.duration == 4.0
    assert [s.text for s in result.segments] == ["buy oat milk", "on the way home"]
    assert [w.word for w in result.segments[0].words] == ["buy", "oat", "milk"]
    assert [w.word for w in result.segments[1].words] == ["on", "the", "way", "home"]


def test_the_endpoint_engine_leaves_out_what_was_not_asked_for():
    seen = {}

    def handler(request):
        seen["body"] = request.read().decode("latin-1")
        return httpx.Response(200, json=verbose_json())

    result = endpoint(handler).transcribe(b"audio", "audio/mp4", None, False)
    assert 'name="language"' not in seen["body"] and "granularities" not in seen["body"]
    assert 'filename="audio.mp4"' in seen["body"]
    assert all(s.words is None for s in result.segments)
    assert "words" not in result.public()["segments"][0]


def test_a_server_that_only_returns_text_still_yields_one_segment():
    result = endpoint(lambda r: httpx.Response(200, json={"text": " hello there "})).transcribe(
        b"audio", "audio/wav", None, False)
    assert result.text == "hello there" and result.language == "" and result.duration == 0.0
    assert [(s.start, s.end, s.text) for s in result.segments] == [(0.0, 0.0, "hello there")]


def test_silence_is_an_empty_transcript_not_an_error():
    result = endpoint(lambda r: httpx.Response(200, json=verbose_json(
        text="", segments=[], words=[], duration=2.0))).transcribe(b"a", "audio/wav", None, True)
    assert result.text == "" and result.segments == [] and result.duration == 2.0


@pytest.mark.parametrize(("status", "expected", "says"), [
    (401, 502, "API key"), (403, 502, "API key"), (404, 502, "/v1"), (413, 413, "too large"),
    (429, 503, "busy"), (400, 422, "model"), (500, 502, "500"), (302, 502, "302"),
])
def test_an_error_from_the_speech_server_becomes_a_sentence_that_says_what_to_do(
        status, expected, says):
    engine = endpoint(lambda r: httpx.Response(
        status, json={"error": {"message": "you said: buy oat milk"}}))
    with pytest.raises(SpeechError) as caught:
        engine.transcribe(b"audio", "audio/webm", None, False)
    assert caught.value.status == expected and says in caught.value.detail
    assert "oat milk" not in caught.value.detail  # the server's own words are not passed on


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>hello</html>"),
    httpx.Response(200, json=["not", "an", "object"]),
    httpx.Response(200, json={"segments": []}),
    httpx.Response(200, json={"text": 5}),
    httpx.Response(200, json=verbose_json(segments="nope")),
    httpx.Response(200, json=verbose_json(segments=[{"start": 0}])),
    httpx.Response(200, json=verbose_json(segments=[{"start": 0, "end": 1, "text": None}])),
    httpx.Response(200, json=verbose_json(words=[{"word": "x"}])),
    httpx.Response(200, json=verbose_json(words=[1, 2])),
    httpx.Response(200, content=b"\xff\xfe"),
])
def test_a_malformed_answer_from_the_speech_server_is_a_readable_failure(response):
    with pytest.raises(SpeechError) as caught:
        endpoint(lambda r: response).transcribe(b"audio", "audio/webm", None, True)
    assert caught.value.status == 502 and "OpenAI-style" in caught.value.detail


def test_odd_numbers_and_characters_from_the_speech_server_are_made_safe():
    body = verbose_json(duration="NaN", text="ok \ud800 then", segments=[
        {"start": -3, "end": "x", "text": "ok \ud800 then"}], words=[])
    result = endpoint(lambda r: httpx.Response(
        200, content=json.dumps(body).encode())).transcribe(b"a", "audio/webm", None, False)
    json.dumps(result.public()).encode("utf-8")
    assert result.segments[0].start == 0.0 and result.segments[0].end == 0.0


def test_a_speech_server_that_cannot_be_reached_or_is_slow_is_a_readable_failure():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    def slow(request):
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(SpeechError) as caught:
        endpoint(refuse).transcribe(b"audio", "audio/webm", None, False)
    assert caught.value.status == 502 and "could not be reached" in caught.value.detail
    with pytest.raises(SpeechError) as caught:
        endpoint(slow).transcribe(b"audio", "audio/webm", None, False)
    assert caught.value.status == 504 and "in time" in caught.value.detail


@pytest.mark.parametrize(("url", "says"), [
    ("", "Speech server address"), ("ftp://files.example.com", "not a web address"),
    ("api.example.com/v1", "not a web address"), ("http://", "not a web address"),
])
def test_an_endpoint_without_a_usable_address_explains_itself_and_calls_nothing(url, says):
    calls = []
    engine = endpoint(lambda r: calls.append(1) or httpx.Response(200), url=url,
                      location="local")
    assert says in engine.problem()
    with pytest.raises(SpeechError) as caught:
        engine.transcribe(b"audio", "audio/webm", None, False)
    assert caught.value.status == 503 and calls == []


def test_an_endpoint_failure_reaches_the_browser_as_a_sentence_and_leaves_a_receipt(client, core):
    use_endpoint(core, lambda request: httpx.Response(500, text="boom"))
    core.settings.set("speech.hosted_callers", ["dictation"], actor="owner")
    r = post(client)
    assert r.status_code == 502 and "500" in r.json()["detail"]
    assert [e.payload["reason"] for e in receipts(core)] == ["engine_error"]


def test_the_key_never_appears_in_an_error_or_on_the_tape(client, core):
    use_endpoint(core, lambda request: httpx.Response(401, json={"error": "bad key sk-test"}))
    core.settings.set("speech.hosted_callers", ["dictation"], actor="owner")
    r = post(client)
    assert r.status_code == 502 and "sk-test" not in r.text
    assert "sk-test" not in json.dumps([e.model_dump() for e in core.tape.after(0)])


# --- the local engine -------------------------------------------------------------


def test_the_speech_add_on_is_only_imported_when_a_recording_arrives(monkeypatch):
    import builtins
    imported = []
    real = builtins.__import__

    def spy(name, *args, **kwargs):
        if name.startswith("faster_whisper"):
            imported.append(name)
            raise ImportError("No module named 'faster_whisper'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", spy)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    holder = ModelHolder()
    engine = LocalEngine(holder, model="small", device="auto", compute_type="default",
                         busy_wait=0, max_seconds=60)
    assert engine.problem() is None and imported == []
    with pytest.raises(SpeechError) as caught:
        engine.transcribe(b"audio", "audio/webm", None, False)
    assert imported == ["faster_whisper"]
    assert caught.value.status == 503 and "uv sync --extra speech" in caught.value.detail
    assert not holder.lock.locked()


def test_the_lock_is_released_after_a_failure_so_the_next_recording_can_run():
    fake = FakeModel()
    holder = ModelHolder(lambda *s: fake)
    engine = LocalEngine(holder, model="small", device="auto", compute_type="default",
                         busy_wait=0, max_seconds=60)
    with pytest.raises(SpeechError):
        engine.transcribe(b"garbage", "audio/webm", None, False)
    assert engine.transcribe(b"audio", "audio/webm", None, False).text == SPOKEN


# --- the Python API ---------------------------------------------------------------


def test_other_modules_transcribe_through_one_function(core, model):
    result = asyncio.run(speech.transcribe(core, b"audio", "audio/wav", caller="recorder",
                                           actor="system"))
    assert result.text == SPOKEN and result.segments[0].end == 4.0
    [receipt] = receipts(core)
    assert receipt.actor == "system" and receipt.payload["caller"] == "recorder"


def test_the_python_api_refuses_the_same_things_the_route_does(core, model):
    def refused(audio=b"audio", content_type="audio/wav", **kwargs):
        with pytest.raises(SpeechError) as caught:
            asyncio.run(speech.transcribe(core, audio, content_type,
                                          **{"caller": "recorder", **kwargs}))
        return caught.value.status

    assert refused(audio=b"") == 422
    assert refused(content_type="text/plain") == 415
    assert refused(caller="Bad Name") == 422
    assert refused(language="klingon") == 422
    core.settings.set("speech.max_megabytes", 1, actor="owner")
    assert refused(audio=b"x" * (1024 * 1024 + 1)) == 413
    assert model.calls == []


# --- found on review ----------------------------------------------------------------


def test_an_absurdly_large_number_from_the_speech_server_is_not_a_crash():
    raw = b'{"text":"hi","duration":1' + b"0" * 400 + b',"segments":[{"start":0,"end":1' \
          + b"0" * 400 + b',"text":"hi"}]}'
    out = endpoint(lambda request: httpx.Response(
        200, content=raw, headers={"content-type": "application/json"},
    )).transcribe(b"audio", "audio/webm", None, False)
    assert out.text == "hi" and out.duration == 0.0


@pytest.mark.parametrize("host", ["134744072", "0x8080808", "0X8080808", "017700000001"])
def test_an_address_written_as_a_single_number_is_treated_as_hosted(host):
    # These resolve as IPv4 addresses, public ones included, so they are not local names.
    assert is_hosted_url(f"http://{host}/v1") is True


def test_a_spoken_language_setting_that_is_not_a_code_is_explained_not_blamed_on_the_audio(
        client, core, model):
    core.settings.set("speech.language", "English", actor="owner")
    status = client.get("/api/speech/status").json()
    assert status["ready"] is False and "spoken language" in status["problem"]
    r = post(client)
    assert r.status_code == 503 and "spoken language" in r.json()["detail"]
    assert model.calls == []
    # A request that names its own language does not depend on the setting.
    assert post(client, language="fr").status_code == 200
    assert model.calls[0]["language"] == "fr"


def test_a_fifth_recording_at_once_is_told_to_try_again_without_waiting(core):
    release = threading.Event()
    fake = FakeModel(hold=release)
    speech.service(core).models.loader = lambda *s: fake
    core.settings.set("speech.engine", "local", actor="owner")
    core.settings.set("speech.busy_wait_seconds", 5, actor="owner")

    async def run():
        four = [asyncio.create_task(speech.transcribe(core, b"audio", "audio/webm",
                                                      caller="dictation")) for _ in range(4)]
        await asyncio.to_thread(fake.running.wait, 5)
        with pytest.raises(SpeechError) as refused:
            await speech.transcribe(core, b"audio", "audio/webm", caller="dictation")
        release.set()
        return refused.value, await asyncio.gather(*four)

    refused, done = asyncio.run(run())
    assert refused.status == 503 and "Too many" in refused.detail
    assert [t.text for t in done] == [SPOKEN] * 4
    # Room again once they have finished.
    assert asyncio.run(speech.transcribe(core, b"audio", "audio/webm",
                                         caller="dictation")).text == SPOKEN


def test_the_server_stops_reading_an_endless_upload_once_the_limit_is_passed(client, core, model):
    # Straight to the app, one piece at a time as a real socket delivers it, with no
    # Content-Length: the only thing that can stop the reading is the limit.
    core.settings.set("speech.max_megabytes", 1, actor="owner")
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    pieces = {"asked": 0}
    sent: list[dict] = []

    async def receive():
        pieces["asked"] += 1
        if pieces["asked"] == 1:
            body = (b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.webm"\r\n'
                    b"Content-Type: audio/webm\r\n\r\n")
        elif pieces["asked"] > 200:
            return {"type": "http.disconnect"}
        else:
            body = b"x" * (64 * 1024)
        return {"type": "http.request", "body": body, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/speech/transcribe", "raw_path": b"/api/speech/transcribe",
        "query_string": b"", "root_path": "", "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode()),
                    (b"content-type", b"multipart/form-data; boundary=b")],
    }
    asyncio.run(client.app(scope, receive, send))
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [413]
    # 1 MB plus the form's slack is 17 pieces of 64 KB; a few more may be in flight.
    assert 17 <= pieces["asked"] <= 24
    assert model.calls == []
