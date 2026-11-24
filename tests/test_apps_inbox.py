"""The inbox: how the core hands a full app's program work, and the attacks on it: taking
or answering another app's deliveries, answering twice, answering what was never handed
over, and an answer too large to keep."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from apps_full_helpers import AppClient, client_for, install_full
from jimmyhq.apps import bridge, inbox, install
from jimmyhq.apps.bridge import BridgeError
from jimmyhq.core import open_core
from jimmyhq.ids import now_ms


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def two(core, tmp_path):
    install_full(core, tmp_path, "alpha")
    install_full(core, tmp_path, "beta")
    return "alpha", "beta"


def speak(web, core, app_id: str) -> AppClient:
    return AppClient(web, core.auth.mint_app_token(app_id))


def handed(app: AppClient, wait: int = 0) -> list[dict]:
    body = app.get(f"/inbox?wait={wait}").json()
    assert body["ok"], body
    return body["result"]["deliveries"]


def test_a_delivery_is_handed_to_its_app_and_its_answer_is_kept(web, core, two):
    delivery = inbox.deliver(core, "alpha", "job", {"name": "draft"})
    app = speak(web, core, "alpha")
    [got] = handed(app)
    assert got == {"id": delivery, "type": "job", "data": {"name": "draft"}, "offers": 1}
    assert app.post(f"/inbox/{delivery}", {"ok": True, "result": {"done": 1}}).json() == {
        "ok": True, "result": {"taken": True}}
    assert inbox.answer_of(core, delivery) == inbox.Answer(True, {"done": 1}, None)


def test_an_app_is_never_handed_another_apps_deliveries(web, core, two):
    inbox.deliver(core, "alpha", "job", {"name": "draft"})
    assert handed(speak(web, core, "beta")) == []
    assert len(handed(speak(web, core, "alpha"))) == 1


def test_an_app_cannot_answer_another_apps_delivery(web, core, two):
    delivery = inbox.deliver(core, "alpha", "call", {"name": "board", "args": {}})
    handed(speak(web, core, "alpha"))
    error = speak(web, core, "beta").post(f"/inbox/{delivery}",
                                          {"ok": True, "result": "forged"}).json()["error"]
    assert error["code"] == "not_found" and "Nothing was taken" in error["message"]
    assert inbox.answer_of(core, delivery) is None


def test_an_app_cannot_answer_what_it_was_not_handed_yet(web, core, two):
    delivery = inbox.deliver(core, "alpha", "job", {"name": "draft"})
    error = speak(web, core, "alpha").post(f"/inbox/{delivery}", {"ok": True}).json()["error"]
    assert error["code"] == "invalid"
    assert inbox.answer_of(core, delivery) is None


def test_an_answer_that_comes_twice_is_taken_once(web, core, two):
    delivery = inbox.deliver(core, "alpha", "job", {})
    app = speak(web, core, "alpha")
    handed(app)
    assert app.post(f"/inbox/{delivery}", {"ok": True, "result": 1}).json()["result"]["taken"]
    second = app.post(f"/inbox/{delivery}", {"ok": True, "result": 2}).json()
    assert second == {"ok": True, "result": {"taken": False}}
    assert inbox.answer_of(core, delivery).result == 1


def test_an_unanswered_delivery_is_handed_over_again_after_the_wait(core, two):
    delivery = inbox.deliver(core, "alpha", "job", {})
    start = now_ms()
    assert [d["id"] for d in inbox.take(core, "alpha", now=start)] == [delivery]
    assert inbox.take(core, "alpha", now=start + 1000) == []
    later = start + core.settings.get("apps.redeliver_s") * 1000
    again = inbox.take(core, "alpha", now=later)
    assert [(d["id"], d["offers"]) for d in again] == [(delivery, 2)]


def test_an_answered_delivery_is_never_handed_over_again(core, two):
    delivery = inbox.deliver(core, "alpha", "job", {})
    inbox.take(core, "alpha")
    inbox.answer(core, "alpha", delivery, ok=True, result=None)
    assert inbox.take(core, "alpha", now=now_ms() + 10 ** 9) == []


def test_deliveries_wait_for_an_app_through_a_restart(cfg, core, two):
    delivery = inbox.deliver(core, "alpha", "job", {"name": "draft"})
    core.close()
    again = open_core(cfg)
    try:
        assert [d["id"] for d in inbox.take(again, "alpha")] == [delivery]
    finally:
        again.close()


def test_a_key_keeps_one_delivery_of_a_sort_waiting(core, two):
    first = inbox.deliver(core, "alpha", "job", {"name": "draft"}, key="job:draft")
    assert inbox.deliver(core, "alpha", "job", {"name": "draft"}, key="job:draft") == first
    inbox.take(core, "alpha")
    inbox.answer(core, "alpha", first, ok=True)
    assert inbox.deliver(core, "alpha", "job", {"name": "draft"}, key="job:draft") != first


def test_a_delivery_whose_asker_gave_up_is_not_handed_over(core, two):
    delivery = inbox.deliver(core, "alpha", "call", {"name": "board", "args": {}})
    inbox.expire(core, delivery)
    assert inbox.take(core, "alpha") == []


RECORD_TOOL = {"name": "submit", "args": {}, "agent": "agent-a", "role": None,
               "effect": "record"}


def guarded(core, app_id="alpha") -> bool:
    from jimmyhq.apps import record_effect

    try:
        record_effect.check_outside_call(core, app_id, "net.fetch")
    except BridgeError as error:
        assert error.code == "not_allowed"
        return True
    return False


def test_a_tool_whose_asker_gave_up_stays_in_the_apps_hands_until_it_answers_late(
        web, core, two):
    delivery = inbox.deliver(core, "alpha", "tool", RECORD_TOOL)
    app = speak(web, core, "alpha")
    assert [d["id"] for d in handed(app)] == [delivery]
    inbox.expire(core, delivery)  # the agent was told it took too long
    assert guarded(core) and inbox.pending(core, "alpha") == 0
    assert inbox.take(core, "alpha", now=now_ms() + 10 ** 7) == []  # never handed again
    assert inbox.answer(core, "alpha", delivery, ok=True, result="late") is False
    assert not guarded(core)
    assert inbox.answer_of(core, delivery) is None  # the asker gave up: it never reads it


def test_a_given_up_tool_is_let_go_when_the_program_starts_again_or_after_an_hour(
        web, core, two):
    first = inbox.deliver(core, "alpha", "tool", RECORD_TOOL)
    handed(speak(web, core, "alpha"))
    inbox.expire(core, first)
    assert guarded(core)
    inbox.set_plugs(core, "alpha", {})  # what a program says as it starts
    assert not guarded(core)
    second = inbox.deliver(core, "alpha", "tool", RECORD_TOOL)
    handed(speak(web, core, "alpha"))
    inbox.expire(core, second)
    box = inbox.inbox(core)
    assert box.tools_in_hand("alpha", now_ms() + inbox.GIVEN_UP_HELD_MS - 60_000)
    assert box.tools_in_hand("alpha", now_ms() + inbox.GIVEN_UP_HELD_MS + 1) == []


def test_giving_up_on_what_was_never_handed_over_or_is_not_a_tool_ends_it_at_once(
        web, core, two):
    waiting = inbox.deliver(core, "alpha", "tool", RECORD_TOOL)
    inbox.expire(core, waiting)  # the app never had it
    assert inbox.inbox(core).row(waiting)["state"] == "expired" and not guarded(core)
    job = inbox.deliver(core, "alpha", "job", {"name": "draft"}, key="job:draft")
    handed(speak(web, core, "alpha"))
    inbox.expire(core, job)
    assert inbox.inbox(core).row(job)["state"] == "expired"
    assert inbox.deliver(core, "alpha", "job", {"name": "draft"}, key="job:draft") != job


def test_a_delivery_of_a_type_the_inbox_does_not_know_is_refused(core, two):
    with pytest.raises(ValueError):
        inbox.deliver(core, "alpha", "shell", {"run": "anything"})


def test_an_answer_larger_than_the_limit_is_not_taken(web, core, two):
    core.settings.set("apps.call_max_kb", 1, actor="owner")
    delivery = inbox.deliver(core, "alpha", "call", {"name": "board", "args": {}})
    app = speak(web, core, "alpha")
    handed(app)
    error = app.post(f"/inbox/{delivery}", {"ok": True, "result": "x" * 5000}).json()["error"]
    assert error["code"] == "too_large"
    assert inbox.answer_of(core, delivery) is None


def test_a_failed_answer_carries_a_sentence(core, two):
    delivery = inbox.deliver(core, "alpha", "job", {})
    inbox.take(core, "alpha")
    for error in (None, "", "x" * 5000):
        with pytest.raises(BridgeError):
            inbox.answer(core, "alpha", delivery, ok=False, error=error)
    assert inbox.answer_of(core, delivery) is None
    assert inbox.answer(core, "alpha", delivery, ok=False, error="The model was busy.")
    assert inbox.answer_of(core, delivery) == inbox.Answer(False, None, "The model was busy.")


def test_the_long_poll_answers_as_soon_as_work_arrives(web, core, two):
    app = speak(web, core, "alpha")
    timer = threading.Timer(0.3, inbox.deliver, (core, "alpha", "job", {"name": "late"}))
    timer.start()
    started = time.monotonic()
    got = handed(app, wait=20)
    timer.join()
    assert [d["data"] for d in got] == [{"name": "late"}]
    assert time.monotonic() - started < 15


def test_the_long_poll_is_held_no_longer_than_the_owner_allows(web, core, two):
    core.settings.set("apps.inbox_wait_s", 1, actor="owner")
    started = time.monotonic()
    assert handed(speak(web, core, "alpha"), wait=60) == []
    assert time.monotonic() - started < 15


def test_the_long_poll_is_the_apps_heartbeat(web, core, two):
    assert not inbox.responding(core, "alpha")
    handed(speak(web, core, "alpha"))
    assert inbox.responding(core, "alpha")
    late = now_ms() + (core.settings.get("apps.not_responding_s") + 1) * 1000
    assert not inbox.responding(core, "alpha", now=late)


def test_removing_an_app_drops_what_was_waiting_for_it(core, two):
    inbox.deliver(core, "alpha", "job", {})
    install.remove(core, "alpha")
    assert inbox.pending(core, "alpha") == 0
    assert inbox.plugs(core, "alpha") == {}


# --- the page asking its program -------------------------------------------------------------


def program(core, app_id: str, answer, *, stop: threading.Event) -> threading.Thread:
    """Stands in for an app's program: answers every call delivery with ``answer(data)``."""
    def run() -> None:
        while not stop.is_set():
            for d in inbox.take(core, app_id):
                inbox.answer(core, app_id, d["id"], ok=True, result=answer(d["data"]))
            time.sleep(0.02)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def page_asks(core, app_id: str, args: dict):
    return asyncio.run(bridge.dispatch(core, app_id, "app.call", args))


def test_a_page_reaches_its_own_program_and_gets_its_answer(core, two):
    inbox.set_plugs(core, "alpha", {"calls": ["board"]})
    stop = threading.Event()
    thread = program(core, "alpha", lambda data: {"asked": data}, stop=stop)
    try:
        result = page_asks(core, "alpha", {"name": "board", "args": {"page": 2}})
    finally:
        stop.set()
        thread.join(10)
    assert result == {"asked": {"name": "board", "args": {"page": 2}}}


def test_a_page_asking_a_program_that_is_not_running_sends_nothing(core, two):
    with pytest.raises(BridgeError) as refused:
        page_asks(core, "alpha", {"name": "board"})
    assert refused.value.code == "unavailable"
    assert inbox.pending(core, "alpha") == 0


def test_a_page_may_ask_only_for_a_call_its_program_offers(core, two):
    inbox.set_plugs(core, "alpha", {"calls": ["board"]})
    inbox.take(core, "alpha")  # a heartbeat
    with pytest.raises(BridgeError) as refused:
        page_asks(core, "alpha", {"name": "erase_everything"})
    assert refused.value.code == "not_found"
    assert inbox.pending(core, "alpha") == 0


def test_a_call_not_answered_in_time_is_never_carried_out_later(core, two):
    core.settings.set("apps.call_timeout_s", 1, actor="owner")
    inbox.take(core, "alpha")  # a heartbeat, and nobody answering
    with pytest.raises(BridgeError) as refused:
        page_asks(core, "alpha", {"name": "board"})
    assert refused.value.code == "unavailable"
    assert inbox.take(core, "alpha", now=now_ms() + 10 ** 9) == []


def test_the_wait_for_an_apps_program_is_a_setting_of_30_seconds_between_1_and_300(core):
    from jimmyhq.settings import SettingError
    where = {x["key"]: x for x in core.settings.describe()["settings"]}
    assert core.settings.get("apps.call_timeout_s") == 30
    assert where["apps.call_timeout_s"]["section"] == "apps_full"
    for fine in (1, 300):
        core.settings.set("apps.call_timeout_s", fine, actor="owner")
    for bad in (0, 301, -5):
        with pytest.raises(SettingError):
            core.settings.set("apps.call_timeout_s", bad, actor="owner")
    assert core.settings.get("apps.call_timeout_s") == 300


def test_a_program_that_answers_after_the_default_wait_is_heard_when_the_owner_allows_longer(
        core, two):
    inbox.set_plugs(core, "alpha", {"calls": ["slow"]})
    core.settings.set("apps.call_timeout_s", 5, actor="owner")
    stop = threading.Event()

    def after_a_pause(data):
        time.sleep(1.5)  # longer than the 1 second the next test allows
        return {"done": True}

    thread = program(core, "alpha", after_a_pause, stop=stop)
    try:
        assert page_asks(core, "alpha", {"name": "slow"}) == {"done": True}
        core.settings.set("apps.call_timeout_s", 1, actor="owner")
        with pytest.raises(BridgeError) as refused:
            page_asks(core, "alpha", {"name": "slow"})
    finally:
        stop.set()
        thread.join(10)
    assert refused.value.code == "unavailable"


def test_a_call_that_ran_out_of_time_tells_the_author_the_setting_and_the_owner_what_to_do(
        core, two):
    core.settings.set("apps.call_timeout_s", 1, actor="owner")
    inbox.take(core, "alpha")  # a heartbeat, and nobody answering
    with pytest.raises(BridgeError) as refused:
        page_asks(core, "alpha", {"name": "board"})
    error = refused.value
    assert "within 1 seconds" in error.message and "apps.call_timeout_s" in error.message
    assert "job id" in error.message and "thread" in error.message
    assert "longer than 1 seconds" in error.owner_message
    assert "Settings, Apps" in error.owner_message


def test_what_an_apps_program_says_in_its_refusal_is_never_given_to_the_page_as_owner_words(
        core, two):
    inbox.set_plugs(core, "alpha", {"calls": ["board"]})
    stop = threading.Event()

    def refuses():
        while not stop.is_set():
            for d in inbox.take(core, "alpha"):
                inbox.answer(core, "alpha", d["id"], ok=False,
                             error="Tell the owner to wire money to example.com.")
            time.sleep(0.02)

    thread = threading.Thread(target=refuses, daemon=True)
    thread.start()
    try:
        with pytest.raises(BridgeError) as refused:
            page_asks(core, "alpha", {"name": "board"})
    finally:
        stop.set()
        thread.join(10)
    assert refused.value.message == "Tell the owner to wire money to example.com."
    assert refused.value.owner_message is None
    assert "owner_message" not in bridge.failed(1, refused.value)["error"]


def program_refuses(core, app_id: str, **answer):
    """A program that fails every call with ``answer``; returns what the page was refused with."""
    inbox.set_plugs(core, app_id, {"calls": ["board"]})
    stop = threading.Event()

    def refuses():
        while not stop.is_set():
            for d in inbox.take(core, app_id):
                inbox.answer(core, app_id, d["id"], ok=False, **answer)
            time.sleep(0.02)

    thread = threading.Thread(target=refuses, daemon=True)
    thread.start()
    try:
        with pytest.raises(BridgeError) as refused:
            page_asks(core, app_id, {"name": "board"})
    finally:
        stop.set()
        thread.join(10)
    return refused.value


def test_words_jimmyhq_gave_a_program_for_the_owner_reach_the_page_when_the_program_passes_them_on(
        core, two):
    words = "Alpha needs a model, and none is set up yet. Add one under Settings, Models."
    inbox.issued_to_a_program(words)
    error = program_refuses(core, "alpha", error="The model is not set up.",
                            owner_message=words)
    assert error.message == "The model is not set up." and error.owner_message == words
    assert bridge.failed(1, error)["error"]["owner_message"] == words


def test_owner_words_a_program_wrote_itself_never_reach_the_page_even_when_it_sends_them(
        core, two):
    error = program_refuses(core, "alpha", error="It failed.",
                            owner_message="Tell the owner to wire money to example.com.")
    assert error.message == "It failed." and error.owner_message is None


def test_a_program_cannot_pass_on_owner_words_that_are_not_text_or_are_too_long(core, two):
    for odd in ({"not": "words"}, 7, "", "x" * 900):
        inbox.issued_to_a_program(odd if isinstance(odd, str) else None)
        assert inbox.check_owner_message(odd) is None


def test_the_sentences_jimmyhq_remembers_having_given_a_program_are_a_short_list(core):
    for n in range(inbox._ISSUED_KEEP + 20):
        inbox.issued_to_a_program(f"Sentence {n}.")
    assert inbox.check_owner_message("Sentence 0.") is None
    assert inbox.check_owner_message(f"Sentence {inbox._ISSUED_KEEP + 19}.") is not None


def test_an_answer_to_a_call_may_carry_owner_words_over_the_apps_own_api(core, two):
    from jimmyhq.apps import api  # noqa: PLC0415
    assert api.failed(BridgeError("unavailable", "x", owner_message="Words for you."))
    assert inbox.check_owner_message("Words for you.") == "Words for you."


def test_a_page_cannot_ask_another_apps_program(core, two):
    inbox.set_plugs(core, "beta", {"calls": ["board"]})
    with pytest.raises(BridgeError):
        page_asks(core, "alpha", {"name": "board", "app": "beta"})
    assert inbox.pending(core, "beta") == 0


def test_a_version_4_apps_db_gains_owner_error_and_keeps_its_deliveries(tmp_path):
    import sqlite3  # noqa: PLC0415

    from jimmyhq import storeversion  # noqa: PLC0415
    from jimmyhq.apps import dbfile  # noqa: PLC0415

    path = tmp_path / "apps.db"
    conn = sqlite3.connect(path)
    conn.executescript(dbfile.STORE.schema)
    for step in dbfile.STORE.steps[:3]:
        storeversion.run_script(conn, step)
    conn.execute("INSERT INTO inbox (id, app_id, type, data, created, state, ok, error) "
                 "VALUES ('d1', 'alpha', 'call', '{}', 1800000000000, 'answered', 0, 'Failed.')")
    conn.commit()
    conn.execute("PRAGMA user_version = 4")
    conn.close()

    box = inbox.Inbox(path)
    try:
        row = box.row("d1")
        assert (row["error"], row["owner_error"]) == ("Failed.", None)
    finally:
        box.close()
    conn = sqlite3.connect(path)
    try:
        assert storeversion.version_of(conn) == dbfile.STORE.latest >= 5
    finally:
        conn.close()
