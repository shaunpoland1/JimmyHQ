"""The Python SDK, ``jimmyhq_app``: against its own fake core, as an app author tests, and
against the real app API, as a program runs."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, CallError, Payload, TokenRevoked, __main__  # noqa: E402
from jimmyhq_app._transport import HttpTransport, read_token  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import client_for, install_full  # noqa: E402
from jimmyhq.apps import inbox  # noqa: E402

DRAFTS = ROOT / "apps" / "examples" / "drafts" / "drafts" / "main.py"


def load_example(name: str = "example_drafts_main"):
    spec = importlib.util.spec_from_file_location(name, DRAFTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- with the fake core ---------------------------------------------------------------------


def test_the_drafts_example_drafts_files_a_card_and_keeps_the_decision(tmp_path):
    core = FakeCore("drafts", data_dir=tmp_path, settings={"draft_minutes": 60},
                    permissions=["bench.file", "model.ask", "jobs"], cards=["draft"])
    core.replies.append("A short post about the week.")
    with core.installed():
        load_example()
    assert core.plugs["jobs"] == [{"name": "draft", "every_setting": "draft_minutes"}]
    assert core.plugs["calls"] == ["board"] and core.plugs["decisions"] == ["draft"]
    core.run_job("draft")
    [card] = core.cards.values()
    assert card["kind"] == "app.drafts.draft" and card["body"] == "A short post about the week."
    core.decide(card["card_id"], "approve")
    assert core.call("board") == [[1, "A short post about the week.", "queued"]]


def test_the_fake_core_holds_an_app_to_the_permissions_it_is_given(tmp_path):
    core = FakeCore("drafts", data_dir=tmp_path, permissions=["jobs"],
                    settings={"draft_minutes": 60})
    core.replies.append("unused")
    with core.installed():
        load_example("example_drafts_main_2")
    with pytest.raises(CallError) as refused:
        core.run_job("draft")
    assert "model.ask" in refused.value.message
    assert core.cards == {}


def make_app(core: FakeCore) -> App:
    return App(transport=core)


def test_a_payload_is_checked_by_its_model_before_it_is_sent():
    core = FakeCore("notes")
    app = make_app(core)

    @app.kind("noted", example={"n": 1})
    class Noted(Payload):
        n: int

    with pytest.raises(ValueError):
        app.tape.print("noted", {"n": "many"})
    assert core.events == []
    event_id = app.tape.print("noted", Noted(n=2), subject="note:2")
    [event] = core.events
    assert event["id"] == event_id and event["payload"] == {"n": 2}
    assert ("app.notes.noted", 1) in core.kinds


def test_an_undeclared_kind_is_not_written():
    core = FakeCore("notes")
    with pytest.raises(CallError) as refused:
        make_app(core).tape.print("never", {"x": 1})
    assert refused.value.code == "not_found" and core.events == []


def test_reading_back_upgrades_the_apps_own_payloads_and_its_subjects():
    core = FakeCore("notes")
    app = make_app(core)

    @app.kind("noted", example={"n": 1})
    class NotedV1(Payload):
        n: int

    app.tape.print("noted", {"n": 1}, subject="note:1")

    @app.kind("noted", v=2, example={"count": 1})
    class NotedV2(Payload):
        count: int

    @app.upcaster("noted", 1)
    def up(payload):
        return {"count": payload["n"]}

    core.events[0]["v"] = 1  # as written before v2 existed
    [event] = app.tape.read()
    assert (event.v, event.payload, event.subject) == (2, {"count": 1}, "note:1")


def test_a_delivery_seen_again_is_answered_without_running_twice():
    core = FakeCore("counter", permissions=["jobs"])
    app = make_app(core)
    runs = []

    @app.job("tick", every=60)
    def tick():
        runs.append(1)
        return len(runs)

    delivery = {"id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "type": "job", "data": {"name": "tick"}}
    assert app.handle(delivery) == app.handle(delivery) == {"ok": True, "result": 1}
    assert runs == [1]


def test_a_handler_that_fails_answers_with_its_own_sentence_and_nothing_in_front_of_it():
    core = FakeCore("counter")
    app = make_app(core)

    @app.call("boom")
    def boom(args):
        raise ValueError("the list is empty")

    with pytest.raises(CallError) as failed:
        core.call("boom")
    assert failed.value.message == "the list is empty"
    with pytest.raises(CallError):
        core.call("never_declared")


def test_a_refusal_from_jimmyhq_hands_its_owner_words_on_with_the_failed_answer():
    core = FakeCore("counter")
    app = make_app(core)

    @app.call("ask")
    def ask(args):
        raise CallError("unavailable", "For the author.", owner_message="Counter needs a "
                                                                       "model.")

    @app.call("no_words")
    def no_words(args):
        raise CallError("unavailable", "For the author.")

    @app.call("long")
    def long(args):
        raise CallError("unavailable", "For the author.", owner_message="x" * 900)

    @app.call("own")
    def own(args):
        raise ValueError("the list is empty")

    asked = app.handle({"id": "1", "type": "call", "data": {"name": "ask"}})
    assert asked == {"ok": False, "error": "For the author.",
                     "owner_message": "Counter needs a model."}
    assert "owner_message" not in app.handle({"id": "2", "type": "call",
                                              "data": {"name": "no_words"}})
    assert len(app.handle({"id": "3", "type": "call",
                           "data": {"name": "long"}})["owner_message"]) == 500
    assert "owner_message" not in app.handle({"id": "4", "type": "call",
                                              "data": {"name": "own"}})
    with pytest.raises(CallError) as failed:  # the fake core shows what the page would get
        core.call("ask")
    assert failed.value.owner_message == "Counter needs a model."
    assert failed.value.message == "For the author."


def test_the_fake_core_answers_the_limits_with_the_defaults_and_with_values_a_test_sets():
    from jimmyhq_app import Limits

    core = FakeCore("counter")
    app = make_app(core)
    assert app.limits() == Limits()
    assert (app.limits().call_max_kb, app.limits().save_max_mb) == (1024, 8)
    core.limits["call_max_kb"] = 64
    core.limits["save_html"] = False
    assert (app.limits().call_max_kb, app.limits().save_html) == (64, False)
    assert FakeCore("other", limits={"save_parts": 2}).limits["save_parts"] == 2
    assert app.limits().save_parts == 5


def test_a_limit_an_older_or_newer_jimmyhq_does_not_know_is_left_out_not_a_crash():
    core = FakeCore("counter")
    app = make_app(core)
    core.limits["something_new"] = 3
    del core.limits["picture_px"]
    told = app.limits()
    assert told.picture_px == 2048 and not hasattr(told, "something_new")


def test_a_fault_in_the_programs_own_code_is_said_in_one_plain_sentence_not_as_its_raw_text():
    core = FakeCore("counter")
    app = make_app(core)

    @app.call("slip")
    def slip(args):
        return args["missing"]

    @app.call("quiet")
    def quiet(args):
        raise RuntimeError()

    for name in ("slip", "quiet"):
        with pytest.raises(CallError) as failed:
            core.call(name)
        assert failed.value.message.startswith("The app's program")
        assert "missing" not in failed.value.message
    with pytest.raises(CallError) as failed:
        core.call("slip")
    assert "(KeyError)" in failed.value.message


def test_an_apps_own_database_lives_in_its_data_folder_and_cannot_take_the_cores(tmp_path):
    app = App(transport=FakeCore("keeper"), data_dir=tmp_path)
    app.db().execute("CREATE TABLE t (x)")
    assert (tmp_path / "main.db").exists()
    for name in ("app", "../escape", "Main"):
        with pytest.raises(ValueError):
            app.db(name)


def test_purging_a_private_database_leaves_nothing_of_the_rows_in_the_file(tmp_path):
    db = App(transport=FakeCore("hush"), data_dir=tmp_path).private_db("diary")
    db.execute("CREATE TABLE entry (id INTEGER PRIMARY KEY, body TEXT)")
    db.execute("INSERT INTO entry (body) VALUES (?)", ("a very private sentence",))
    db.commit()
    assert db.purge("entry", "id = ?", (1,)) == 1
    db.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("diary.db*"))
    assert b"a very private sentence" not in raw


# --- the token and the transport ------------------------------------------------------------


def test_a_supervised_program_reads_its_token_from_its_first_line_of_input():
    environ = {"JIMMYHQ_SUPERVISED": "1"}
    assert read_token(io.StringIO('{"token": "jhqa_given"}\n'), environ) == "jhqa_given"


def test_a_token_given_in_the_environment_is_taken_out_of_it():
    environ = {"JIMMYHQ_APP_TOKEN": "jhqa_dev"}
    assert read_token(io.StringIO(""), environ) == "jhqa_dev"
    assert "JIMMYHQ_APP_TOKEN" not in environ


def test_a_program_with_no_token_says_how_to_get_one():
    with pytest.raises(RuntimeError, match="JIMMYHQ_APP_TOKEN"):
        read_token(io.StringIO(""), {})


@pytest.mark.parametrize("api", ["https://jimmyhq.example.com", "http://192.0.2.4:8440", ""])
def test_the_token_is_only_ever_sent_to_this_machine(api):
    transport = HttpTransport(api=api, token="jhqa_x")
    with pytest.raises(RuntimeError):
        transport.request("GET", "/hello")


def test_the_entry_point_refuses_anything_but_module_and_attribute():
    assert __main__.main(["drafts.main"]) == 2
    assert __main__.main(["os:system; echo"]) == 2


# --- against the real app API ---------------------------------------------------------------


@pytest.fixture
def real(core, tmp_path):
    install_full(core, tmp_path, "realapp", permissions=["bench.file", "jobs"],
                 settings=[{"key": "every", "type": "int", "default": 5, "min": 1,
                            "label": "Every", "help": "Minutes."}])
    with client_for(core) as web:
        transport = HttpTransport(client=web, token=core.auth.mint_app_token("realapp"))
        yield App(transport=transport, app_id="realapp", data_dir=tmp_path / "data")


def test_the_sdk_speaks_the_real_app_api(core, real):
    app = real

    @app.kind("drafted", example={"post": 1})
    class Drafted(Payload):
        post: int

    @app.job("draft", every_setting="every")
    def draft():
        card = app.bench.file("draft", "Draft ready", body="Text.", subject="post:1")
        app.tape.print("drafted", {"post": 1}, subject="post:1")
        return card

    app.register()
    assert app.settings.get("every") == 5
    assert inbox.plugs(core, "realapp")["jobs"][0]["name"] == "draft"
    delivery = inbox.deliver(core, "realapp", "job", {"name": "draft"})
    found = app._transport.request("GET", "/inbox", params={"wait": 0})["deliveries"]
    for d in found:
        app._answer(d["id"], app.handle(d))
    card_id = inbox.answer_of(core, delivery).result
    from jimmyhq import bench
    assert bench.get_card(core, card_id)["filed_by"] == "app:realapp"
    [event] = app.tape.read(kinds=["drafted"])
    assert event.kind == "app.realapp.drafted" and event.subject == "post:1"


def test_a_refusal_from_the_real_core_reaches_the_app_as_a_call_error(real):
    with pytest.raises(CallError) as refused:
        real.model.ask("hello")
    assert refused.value.code == "not_allowed"


def test_the_owner_words_of_a_refusal_reach_the_page_that_asked_through_the_program(
        core, tmp_path):
    import asyncio  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    from jimmyhq.apps import bridge  # noqa: PLC0415

    install_full(core, tmp_path, "realapp", permissions=["model.ask"])
    with client_for(core) as web:
        app = App(transport=HttpTransport(client=web, token=core.auth.mint_app_token("realapp")),
                  app_id="realapp", data_dir=tmp_path / "data")

        @app.call("ask")
        def ask(args):
            return app.model.ask("hello")  # no model is set up: JimmyHQ refuses, in two voices

        app.register()
        app._transport.request("GET", "/inbox", params={"wait": 0})  # the program is up
        with ThreadPoolExecutor(1) as pool:
            page = pool.submit(lambda: asyncio.run(
                bridge.dispatch(core, "realapp", "app.call", {"name": "ask"})))
            for _ in range(100):
                found = app._transport.request("GET", "/inbox", params={"wait": 1})
                for d in found["deliveries"]:
                    app._answer(d["id"], app.handle(d))
                if page.done():
                    break
            with pytest.raises(bridge.BridgeError) as refused:
                page.result(10)
    assert refused.value.owner_message == (
        "Realapp needs a model, and none is set up yet. Add one to the default role under "
        "Settings, Models, then try again.")
    assert "owner_message" in refused.value.message  # the program's sentence is the author's


def test_a_revoked_token_reaches_the_app_as_token_revoked(core, real):
    core.auth.revoke_app_tokens("realapp")
    with pytest.raises(TokenRevoked):
        real.settings.get("every")


def test_the_sdk_updates_withdraws_and_asks_the_ladder_through_the_real_app_api(core, real):
    from jimmyhq import bench

    card = real.bench.file("draft", "Draft ready")
    real.bench.update(card, title="Draft ready, shorter")
    assert bench.get_card(core, card)["title"] == "Draft ready, shorter"
    assert real.ladder.check("booking", "book", "publish") == "approve"
    real.bench.withdraw(card, note="Not needed.")
    assert bench.get_card(core, card)["status"] != "open"


def test_the_sdk_emits_to_the_apps_own_pages_and_the_fake_core_keeps_what_was_sent(core,
                                                                                    real):
    from jimmyhq.apps.plugs import emit

    real.emit("board.changed", {"post": 3})
    assert emit.emitted(core, "realapp", 0)["events"][0]["data"] == {"post": 3}
    fake = FakeCore("emitter")
    with fake.installed():
        App().emit("board.changed")
    assert fake.emitted == [{"event": "board.changed", "data": None}]
