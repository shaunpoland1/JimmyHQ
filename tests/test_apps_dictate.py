"""An app's page dictating (``hq.speech.dictate``), from the server's side.

The shell records and sends the recording to ``POST /api/speech/transcribe`` with
``app=<id>``. These tests are the requests a page could hope to get through that way:
an app without the permission, one that is switched off, a private app under an engine
whose words would leave the owner's machines, a recording longer than the app's limit.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from jimmyhq import speech
from jimmyhq.apps import dictate, files
from jimmyhq.apps import permissions as pm
from jimmyhq.speech.engines import EndpointEngine, is_this_machine
from jimmyhq.web.app import create_app
from test_apps_bridge_fakes import WATER, installs
from test_speech import PASSWORD, SPOKEN, WEBM, FakeModel, use_endpoint, verbose_json, wav_of

DRAFTS = json.loads((Path(__file__).resolve().parents[1] / "apps" / "examples" / "drafts"
                     / "jimmyhq.app.json").read_text(encoding="utf-8"))
JOURNAL = {**DRAFTS, "id": "journal", "name": "Journal", "private": True,
           "permissions": ["model.ask", "speech.dictate"], "cards": []}
SPEAKS = {**WATER, "permissions": ["speech.dictate"]}


def dotted(*labels: str) -> str:
    return ".".join(labels)


@pytest.fixture
def apps(monkeypatch, tmp_path):
    made = installs(monkeypatch, tmp_path)
    made.add(SPEAKS)
    made.add(JOURNAL)
    return made


@pytest.fixture
def client(core, apps):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).status_code \
            == 200
        yield c


@pytest.fixture
def model(core):
    fake = FakeModel()
    speech.service(core).models.loader = lambda *settings: fake
    core.settings.set("speech.engine", "local", actor="owner")
    return fake


def post(client, file=WEBM, **fields):
    return client.post("/api/speech/transcribe", files={"file": file}, data=fields)


def receipts(core):
    return [e for e in core.tape.after(0) if e.kind.startswith("speech.")]


def everything(core) -> str:
    return json.dumps([e.model_dump() for e in core.tape.after(0, limit=100000)])


class Server:
    """A speech server that only counts what reaches it."""

    def __init__(self):
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        return httpx.Response(200, json=verbose_json())


# --- the permission and its words -----------------------------------------------------------


def test_the_permission_is_for_both_tiers_and_says_the_owner_sees_it_and_it_gets_only_words():
    row = pm.BY_ID["speech.dictate"]
    assert row.tiers == pm.TIERS and not row.always and not row.strong and row.param is None
    assert row.sentence == ("Ask you to dictate: you see when it is recording, and it gets "
                            "only the words.")
    assert "speech.dictate" in pm.ASKABLE


def test_the_longest_dictation_is_a_setting_among_what_an_app_may_use(core):
    where = {x["key"]: x for x in core.settings.describe()["settings"]}
    assert where["apps.dictate_max_s"]["section"] == "apps_limits"
    assert core.settings.get("apps.dictate_max_s") == 300


def test_the_mount_tells_the_shell_the_apps_limit_and_whether_it_is_private(core, apps):
    for app in ("water", "journal"):
        (apps.apps[app].folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    assert files.mint_mount(core, "water")["private"] is False
    core.settings.set("apps.dictate_max_s", 60, actor="owner")
    made = files.mint_mount(core, "journal", "board")
    assert made["private"] is True and made["dictate_max_s"] == 60


def test_a_page_cannot_ask_the_server_to_dictate_through_the_bridge(core, apps):
    from test_apps_bridge_fakes import refused
    for call in ("speech.dictate", "speech.available"):
        error = refused(core, "water", call, {})
        assert error.code == "invalid" and "never reaches the server" in error.message


# --- who may dictate ------------------------------------------------------------------------


def test_an_app_given_the_permission_dictates_as_itself_and_gets_the_words(client, core, model):
    answer = post(client, app="water", caller="dictation")
    assert answer.status_code == 200 and answer.json()["text"] == SPOKEN
    [receipt] = receipts(core)
    assert receipt.kind == "speech.transcribed" and receipt.payload["caller"] == "app.water"


def test_an_app_without_the_permission_is_refused_and_nothing_is_transcribed(
        client, core, model, apps):
    apps.add(SPEAKS, granted=[])
    answer = post(client, app="water")
    assert answer.status_code == 403 and "speech.dictate" in answer.json()["detail"]
    assert model.calls == [] and receipts(core) == []


@pytest.mark.parametrize("app", ["nobody", "Water", "../water", "x" * 50])
def test_an_app_that_is_not_installed_is_refused(client, core, model, app):
    assert post(client, app=app).status_code == 404
    assert model.calls == [] and receipts(core) == []


def test_an_app_that_is_switched_off_is_refused(client, core, model, apps):
    apps.add(SPEAKS, enabled=False)
    assert post(client, app="water").status_code == 409
    assert model.calls == []


def test_a_token_cannot_dictate_for_an_app_whatever_its_scopes(core, apps, model):
    _, secret = core.auth.create_token("probe", ["read", "write"])
    with TestClient(create_app(core), base_url="http://testserver") as c:
        c.headers["authorization"] = f"Bearer {secret}"
        assert c.post("/api/speech/transcribe", files={"file": WEBM},
                      data={"app": "water"}).status_code in (401, 403)
    assert model.calls == []


def test_the_apps_name_not_the_forms_is_the_caller_for_the_hosted_rule(client, core):
    server = Server()
    use_endpoint(core, server)
    core.settings.set("speech.hosted_callers", ["dictation"], actor="owner")
    # The shell's own button may use the hosted server; an app claiming to be it may not.
    assert post(client, caller="dictation").status_code == 200
    refused = post(client, caller="dictation", app="water")
    assert refused.status_code == 403 and "app.water" in refused.json()["detail"]
    assert server.calls == 1
    core.settings.set("speech.hosted_callers", ["dictation", "app.water"], actor="owner")
    assert post(client, app="water").status_code == 200 and server.calls == 2


# --- a private app's words stay on the owner's machines -------------------------------------


def test_a_private_app_dictates_on_this_computer_and_its_words_are_on_no_record(
        client, core, model):
    answer = post(client, app="journal")
    assert answer.status_code == 200 and answer.json()["text"] == SPOKEN
    [receipt] = receipts(core)
    assert receipt.payload["caller"] == "app.journal" and receipt.payload["hosted"] is False
    assert set(receipt.payload) == {"engine", "hosted", "caller", "audio_seconds",
                                    "processing_ms"}
    record = everything(core)
    for word in ("oat", "milk", SPOKEN):
        assert word not in record


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1",
    # Addresses the looser hosted rule calls local, but which may be another machine on
    # the network: they cannot be known to be the owner's.
    "http://192.0.2.10:8000/v1",
    "http://speech:8000/v1",
])
def test_a_private_app_is_refused_under_a_speech_server_not_known_to_be_the_owners(
        client, core, url):
    server = Server()
    use_endpoint(core, server, url=url)
    core.settings.set("speech.hosted_callers", ["app.journal"], actor="owner")
    answer = post(client, app="journal")
    assert answer.status_code == 403
    assert answer.json()["detail"] == dictate.PRIVATE_NOT_LOCAL
    assert server.calls == 0 and receipts(core) == []


def test_a_private_app_may_use_a_speech_server_on_this_computer_or_one_the_owner_calls_theirs(
        client, core):
    server = Server()
    use_endpoint(core, server, url="http://127.0.0.1:8000/v1", key=None)
    assert post(client, app="journal").status_code == 200
    core.settings.set("speech.endpoint_url", "http://" + dotted("192", "0", "2", "10") + "/v1",
                      actor="owner")
    assert post(client, app="journal").status_code == 403
    core.settings.set("speech.endpoint_location", "local", actor="owner")
    assert post(client, app="journal").status_code == 200
    assert server.calls == 2


def test_speech_switched_off_says_where_to_turn_it_on_for_an_app_too(client, core):
    answer = post(client, app="journal")
    assert answer.status_code == 409 and "Settings, Voice" in answer.json()["detail"]


def test_the_status_says_whether_the_words_stay_on_the_owners_machines(client, core, model):
    status = lambda: client.get("/api/speech/status").json()  # noqa: E731
    assert status()["own_machines"] is True
    use_endpoint(core, Server(), url="https://api.example.com/v1")
    assert status()["own_machines"] is False
    core.settings.set("speech.endpoint_url", "http://192.0.2.10:8000/v1", actor="owner")
    assert status()["hosted"] is False and status()["own_machines"] is False
    core.settings.set("speech.endpoint_url", "http://localhost:8000/v1", actor="owner")
    assert status()["own_machines"] is True
    core.settings.set("speech.endpoint_location", "hosted", actor="owner")
    assert status()["own_machines"] is False
    core.settings.set("speech.engine", "off", actor="owner")
    assert status()["own_machines"] is None


def test_only_localhost_and_loopback_addresses_are_this_machine():
    for url in ("http://localhost:8000/v1", "http://127.0.0.1/v1", "http://[::1]:9000/v1",
                "http://127.8.9.1/v1", "http://[::ffff:127.0.0.1]/v1", "http://a.localhost/v1"):
        assert is_this_machine(url), url
    for url in ("http://192.0.2.10/v1", "http://speech/v1", "https://api.example.com/v1",
                "http://2130706433/v1", "http://0x7f000001/v1", "", "not an address"):
        assert not is_this_machine(url), url
    engine = EndpointEngine(url="http://192.0.2.10/v1", model="m", key=None, location="auto",
                            timeout=5)
    assert engine.hosted is False and engine.own_machines is False


# --- the app's own limit --------------------------------------------------------------------


def test_an_apps_recording_is_held_to_its_own_limit_and_the_shells_is_not(client, core, model):
    core.settings.set("apps.dictate_max_s", 5, actor="owner")
    long = ("long.wav", wav_of(8), "audio/wav")
    assert post(client, file=long, app="water").status_code == 413
    model.duration = 8.0
    assert post(client, app="water").status_code == 413
    assert post(client, file=long).status_code == 200


def test_an_apps_limit_never_lengthens_the_owners_voice_limit(client, core, model):
    core.settings.set("speech.max_seconds", 5, actor="owner")
    core.settings.set("apps.dictate_max_s", 600, actor="owner")
    assert post(client, file=("long.wav", wav_of(8), "audio/wav"), app="water").status_code == 413


def test_the_shells_own_dictation_is_unchanged(client, core, model):
    answer = post(client)
    assert answer.status_code == 200 and answer.json()["text"] == SPOKEN
    [receipt] = receipts(core)
    assert receipt.payload["caller"] == "dictation"
