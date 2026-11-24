"""The app API, tried from the attacker's side: a full app's token used for another app's
data, for a permission it was not granted, on the owner's routes, from another machine,
and after the app is switched off or removed."""

from __future__ import annotations

import pytest

from apps_full_helpers import DRAFTED, AppClient, bearer, client_for, install_full
from jimmyhq.apps import api, inbox, install
from jimmyhq.apps.bridge import Call
from jimmyhq.apps.permissions import FULL
from jimmyhq.tape import registry

PASSWORD = "correct horse battery"


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def drafty(core, tmp_path):
    install_full(core, tmp_path, "drafty", permissions=["bench.file"],
                 secrets=[{"name": "key", "label": "Key", "help": "A key."}])
    return "drafty"


def speak(web, core, app_id: str) -> AppClient:
    return AppClient(web, core.auth.mint_app_token(app_id))


def declare(app: AppClient, name: str, schema=DRAFTED, example=None, v: int = 1) -> dict:
    return app.put("/kinds", {"kinds": [{"name": name, "v": v, "schema": schema,
                                         "example": example or {"post": 1}}]}).json()


# --- who may knock -------------------------------------------------------------------------


def test_an_app_token_works_on_the_app_api_and_says_which_app_is_asking(web, core, drafty):
    hello = speak(web, core, drafty).get("/hello").json()
    assert hello["ok"] and hello["result"]["app"] == "drafty"
    assert hello["result"]["permissions"] == ["bench.file"]
    assert hello["result"]["api"] == api.API_VERSION


@pytest.mark.parametrize("method, path", [
    ("GET", "/api/apps"), ("GET", "/api/apps/_installed"), ("GET", "/api/apps/drafty"),
    ("POST", "/api/apps/drafty/disable"), ("POST", "/api/apps/drafty/bridge/storage.get"),
    ("GET", "/api/settings"), ("GET", "/api/tasks"),
])
def test_an_app_token_opens_none_of_the_owners_or_a_readers_routes(web, core, drafty,
                                                                   method, path):
    token = core.auth.mint_app_token(drafty)
    answer = web.request(method, path, headers=bearer(token), json={})
    assert answer.status_code == 401
    assert store_enabled(core, drafty)


def store_enabled(core, app_id: str) -> bool:
    from jimmyhq.apps import store
    return bool(store.get(core, app_id)["enabled"])


def test_an_ordinary_api_token_however_wide_is_refused_on_the_app_api(web, core, drafty):
    token = core.auth.create_token("automation", ["read", "write"])[1]
    assert web.get("/api/app/v1/hello", headers=bearer(token)).status_code == 401


def test_the_owners_own_session_is_refused_on_the_app_api(web, core, drafty):
    assert web.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    answer = web.get("/api/app/v1/hello")
    assert answer.status_code == 403
    assert "full app's own program" in answer.json()["detail"]


def test_an_app_token_sent_from_another_machine_is_refused(core, drafty):
    token = core.auth.mint_app_token(drafty)
    with client_for(core, address=("192.0.2.9", 5000)) as remote:
        assert remote.get("/api/app/v1/hello", headers=bearer(token)).status_code == 401


def test_an_app_token_is_not_taken_from_anywhere_but_the_authorization_header(web, core,
                                                                              drafty):
    token = core.auth.mint_app_token(drafty)
    assert web.get(f"/api/app/v1/hello?token={token}").status_code == 401
    web.cookies.set("jimmyhq_session", token)
    assert web.get("/api/app/v1/hello").status_code == 401


def test_minting_a_token_stops_the_one_before_it(web, core, drafty):
    old = core.auth.mint_app_token(drafty)
    new = core.auth.mint_app_token(drafty)
    assert web.get("/api/app/v1/hello", headers=bearer(old)).status_code == 401
    assert web.get("/api/app/v1/hello", headers=bearer(new)).json()["ok"]


def test_a_token_stops_working_when_the_app_is_switched_off(web, core, drafty):
    token = core.auth.mint_app_token(drafty)
    install.disable(core, drafty)
    assert web.get("/api/app/v1/hello", headers=bearer(token)).status_code == 401


def test_a_switched_off_app_is_refused_even_with_a_token_minted_after(web, core, drafty):
    install.disable(core, drafty)
    app = speak(web, core, drafty)
    assert app.refused("storage.set", {"key": "k", "value": 1})["code"] == "not_allowed"
    assert not app.get("/hello").json()["ok"]


def test_a_token_stops_working_when_the_app_is_removed(web, core, drafty):
    token = core.auth.mint_app_token(drafty)
    install.remove(core, drafty)
    assert web.get("/api/app/v1/hello", headers=bearer(token)).status_code == 401


def test_a_sandboxed_app_given_a_token_is_still_refused(web, core, tmp_path):
    from apps_install_helpers import example, install_folder
    install_folder(core, example(tmp_path, "water"))
    app = speak(web, core, "water")
    assert app.refused("storage.get", {"key": "k"})["code"] == "not_allowed"


# --- whose data ----------------------------------------------------------------------------


def test_an_app_token_reaches_only_its_own_apps_storage(web, core, tmp_path):
    install_full(core, tmp_path, "alpha")
    install_full(core, tmp_path, "beta")
    alpha, beta = speak(web, core, "alpha"), speak(web, core, "beta")
    alpha.ok("storage.set", {"key": "k", "value": "alpha's"})
    assert beta.ok("storage.get", {"key": "k"}) is None
    assert beta.refused("storage.get", {"key": "k", "app": "alpha"})["code"] == "invalid"
    assert alpha.ok("storage.get", {"key": "k"}) == "alpha's"


def test_an_app_reads_only_the_secrets_it_declares(web, core, drafty, tmp_path):
    install_full(core, tmp_path, "beta", secrets=[{"name": "key", "label": "K", "help": "K."}])
    core.secrets.set("app.beta.key", "beta's secret")
    core.secrets.set("app.drafty.key", "drafty's secret")
    app = speak(web, core, drafty)
    assert app.ok("secrets.get", {"name": "key"}) == "drafty's secret"
    for name in ("other", "app.beta.key", "../beta.key"):
        assert app.refused("secrets.get", {"name": name})["code"] == "not_found"


@pytest.mark.parametrize("call, args", [
    ("model.ask", {"prompt": "hello"}),
    ("tasks.create", {"title": "A task"}),
    ("tasks.list", {}),
    ("memory.recall", {"query": "anything"}),
    ("net.fetch", {"url": "https://api.example.com/x"}),
])
def test_a_call_the_app_was_not_granted_does_nothing(web, core, drafty, call, args):
    error = speak(web, core, drafty).refused(call, args)
    assert error["code"] == "not_allowed"
    assert "Nothing" in error["message"] or "nothing" in error["message"]


def test_a_granted_call_acts_as_the_app(web, core, tmp_path):
    install_full(core, tmp_path, "tasker", permissions=["tasks.write"])
    made = speak(web, core, "tasker").ok("tasks.create", {"title": "Water the plants"})
    from jimmyhq import tasks
    assert tasks.service(core).get(made["id"])["filed_by"] == "app:tasker"


def test_a_program_cannot_call_its_own_page_door(web, core, drafty):
    error = speak(web, core, drafty).refused("app.call", {"name": "board"})
    assert error["code"] == "invalid" and "page" in error["message"]


def test_an_unknown_call_is_refused_with_the_calls_there_are(web, core, drafty):
    error = speak(web, core, drafty).refused("owner.delete_everything")
    assert error["code"] == "invalid" and "tape.print" in error["message"]


def test_a_request_larger_than_the_limit_is_refused_before_it_is_read(web, core, drafty):
    core.settings.set("apps.call_max_kb", 1, actor="owner")
    app = speak(web, core, drafty)
    error = app.refused("storage.set", {"key": "k", "value": "x" * 400_000})
    assert error["code"] == "too_large"
    assert app.ok("storage.get", {"key": "k"}) is None


def test_a_plug_point_can_add_a_call_but_never_take_an_existing_name():
    with pytest.raises(ValueError):
        api.add_call(Call("tape.print", lambda ctx, args: None, tiers=(FULL,)))
    with pytest.raises(ValueError):
        api.add_call(Call("storage.get", lambda ctx, args: None, tiers=(FULL,)))


# --- the record ----------------------------------------------------------------------------


def test_an_app_writes_its_own_declared_kind_as_itself_under_its_own_subject(web, core,
                                                                             drafty):
    app = speak(web, core, drafty)
    assert declare(app, "drafted")["ok"]
    written = app.ok("tape.print", {"kind": "drafted", "payload": {"post": 3},
                                    "subject": "post:3"})
    event = core.tape.get(written["id"])
    assert (event.kind, event.actor, event.subject) == (
        "app.drafty.drafted", "app:drafty", "app.drafty:post:3")
    assert event.readable and event.payload == {"post": 3}


def test_an_app_cannot_write_a_core_kind_or_another_apps_kind(web, core, drafty, tmp_path):
    install_full(core, tmp_path, "beta")
    beta = speak(web, core, "beta")
    assert declare(beta, "secretly")["ok"]
    app = speak(web, core, drafty)
    before = core.tape.last_seq()
    for kind in ("task.filed", "app.beta.secretly", "apps.removed"):
        assert app.refused("tape.print", {"kind": kind, "payload": {"post": 1}})["code"] \
            in ("not_found", "invalid")
    assert core.tape.last_seq() == before


def test_a_payload_that_does_not_fit_its_declared_schema_is_not_written(web, core, drafty):
    app = speak(web, core, drafty)
    declare(app, "counted")
    before = core.tape.last_seq()
    for payload in ({"post": "three"}, {"post": 1, "extra": True}, {}):
        error = app.refused("tape.print", {"kind": "counted", "payload": payload})
        assert error["code"] == "invalid" and "Nothing was written" in error["message"]
    assert core.tape.last_seq() == before


def test_a_kind_must_be_declared_before_it_is_written(web, core, drafty):
    error = speak(web, core, drafty).refused("tape.print", {"kind": "never", "payload": {}})
    assert error["code"] == "not_found" and "PUT /api/app/v1/kinds" in error["message"]


def test_a_private_app_cannot_write_on_the_record(web, core, tmp_path):
    install_full(core, tmp_path, "hush", private=True)
    app = speak(web, core, "hush")
    declare(app, "noted")
    assert app.refused("tape.print", {"kind": "noted", "payload": {"post": 1}})["code"] \
        == "not_allowed"


def test_a_declared_kind_and_version_never_changes(web, core, drafty):
    app = speak(web, core, drafty)
    assert declare(app, "fixed")["result"]["kinds"] == [
        {"name": "app.drafty.fixed", "v": 1, "new": True}]
    assert declare(app, "fixed")["result"]["kinds"][0]["new"] is False
    wider = {**DRAFTED, "properties": {"post": {"type": "string"}}}
    error = declare(app, "fixed", schema=wider, example={"post": "1"})["error"]
    assert "never changes" in error["message"] and "v2" in error["message"]
    assert declare(app, "fixed", schema=wider, example={"post": "1"}, v=2)["ok"]


def test_a_schema_with_a_rule_jimmyhq_does_not_check_is_refused(web, core, drafty):
    app = speak(web, core, drafty)
    shaped = {**DRAFTED, "properties": {"post": {"type": "string", "pattern": "^(a+)+$"}}}
    error = declare(app, "shaped", schema=shaped, example={"post": "a"})["error"]
    assert "pattern" in error["message"]
    unknown = {**DRAFTED, "if": {"type": "object"}}
    assert not declare(app, "iffy", schema=unknown)["ok"]


def test_a_kind_whose_example_does_not_fit_is_refused(web, core, drafty):
    error = declare(speak(web, core, drafty), "wrong", example={"post": "x"})["error"]
    assert "example" in error["message"]


def test_declaring_kinds_is_all_or_nothing(web, core, drafty):
    app = speak(web, core, drafty)
    body = {"kinds": [{"name": "first", "v": 1, "schema": DRAFTED, "example": {"post": 1}},
                      {"name": "second", "v": 1, "schema": DRAFTED, "example": {"post": "x"}}]}
    assert not app.put("/kinds", body).json()["ok"]
    assert not registry.is_schema_backed("app.drafty.first")


def test_an_app_may_declare_only_as_many_kinds_as_the_owner_allows(web, core, drafty):
    core.settings.set("apps.max_kinds", 1, actor="owner")
    app = speak(web, core, drafty)
    assert declare(app, "one")["ok"]
    assert "at most 1" in declare(app, "two")["error"]["message"]


def test_without_tape_read_an_app_reads_only_what_it_wrote(web, core, drafty):
    app = speak(web, core, drafty)
    declare(app, "seen")
    mine = app.ok("tape.print", {"kind": "seen", "payload": {"post": 1}})
    core.tape.print("system.opened", {"version": "x"}, actor="system")
    read = app.ok("tape.read", {"after": 0})
    assert [e["id"] for e in read["events"]] == [mine["id"]]
    assert read["next"] == core.tape.last_seq()


def test_an_app_reading_the_tape_from_a_place_past_its_end_is_told_the_end(web, core, drafty):
    app = speak(web, core, drafty)
    declare(app, "seen")
    ahead = app.ok("tape.read", {"after": core.tape.last_seq() + 100})
    assert ahead["events"] == [] and ahead["next"] == core.tape.last_seq()
    mine = app.ok("tape.print", {"kind": "seen", "payload": {"post": 1}})  # a "read" number
    read = app.ok("tape.read", {"after": ahead["next"]})
    assert [e["id"] for e in read["events"]] == [mine["id"]]


def test_with_tape_read_an_app_reads_the_whole_record(web, core, tmp_path):
    install_full(core, tmp_path, "reader", permissions=["tape.read"])
    read = speak(web, core, "reader").ok("tape.read", {"after": 0, "limit": 500})
    assert {e["actor"] for e in read["events"]} >= {"system", "owner"}


# --- what a program offers -----------------------------------------------------------------


def test_offered_plugs_are_checked_and_kept_for_the_plug_points(web, core, tmp_path):
    install_full(core, tmp_path, "busy", permissions=["jobs", "bench.file"],
                 settings=[{"key": "every", "type": "int", "default": 5, "min": 1,
                            "label": "Every", "help": "Minutes."}])
    plugs = {"jobs": [{"name": "draft", "every_setting": "every"},
                      {"name": "tidy", "every": 3600}],
             "decisions": ["draft"], "calls": ["board"]}
    kept = speak(web, core, "busy").put("/plugs", plugs).json()
    assert kept["ok"]
    assert inbox.plugs(core, "busy")["calls"] == ["board"]
    assert [j["name"] for j in inbox.plugs(core, "busy")["jobs"]] == ["draft", "tidy"]


@pytest.mark.parametrize("plugs, word", [
    ({"jobs": [{"name": "draft", "every": 60}]}, "jobs"),
    ({"tools": ["search"]}, "agents.tools"),
    ({"sources": ["notes"]}, "memory.source"),
    ({"decisions": ["draft"]}, "bench.file"),
])
def test_a_plug_the_app_was_not_granted_is_not_kept(web, core, tmp_path, plugs, word):
    install_full(core, tmp_path, "plain")
    error = speak(web, core, "plain").put("/plugs", plugs).json()["error"]
    assert error["code"] == "not_allowed" and word in error["message"]
    assert inbox.plugs(core, "plain") == {}


def test_decisions_are_offered_only_on_card_kinds_the_app_declares(web, core, tmp_path):
    install_full(core, tmp_path, "carder", permissions=["bench.file"])
    error = speak(web, core, "carder").put("/plugs", {"decisions": ["payment"]}).json()["error"]
    assert error["code"] == "invalid" and "payment" in error["message"]


def test_a_job_cannot_run_more_often_than_the_workers_clock(web, core, tmp_path):
    install_full(core, tmp_path, "rusher", permissions=["jobs"])
    error = speak(web, core, "rusher").put(
        "/plugs", {"jobs": [{"name": "spin", "every": 1}]}).json()["error"]
    assert error["code"] == "invalid"
