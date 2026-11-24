"""Messages from devices in the Python SDK: ``@app.inbound``, the testing fake's
``core.inbound``, and the whole way from a device through the real core to the handler."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, CallError, Inbound  # noqa: E402
from jimmyhq_app._transport import HttpTransport  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import PASSWORD, client_for, install_full  # noqa: E402
from jimmyhq.apps import inbound, inbox  # noqa: E402

EVENTS = [{"name": "event", "label": "Home events", "help": "What your home says happened."}]


def test_the_decorator_hands_the_handler_the_body_the_time_and_the_keys_name(tmp_path):
    core = FakeCore("house", data_dir=tmp_path, permissions=["inbound"])
    got: list[Inbound] = []
    with core.installed():
        app = App()

        @app.inbound("event")
        def event(message: Inbound) -> None:
            got.append(message)

        app.run()
    assert core.plugs["inbound"] == ["event"]
    assert core.inbound("event", {"state": "on"}, key_name="Home hub", received_ms=7) is None
    assert got == [Inbound(body={"state": "on"}, received_ms=7, key_name="Home hub",
                           name="event")]
    assert core.inbound_sent == [{"name": "event", "body": {"state": "on"},
                                  "key_name": "Home hub", "received_ms": 7}]


def test_what_the_handler_returns_is_dropped_and_what_it_raises_is_reported(tmp_path):
    core = FakeCore("house", data_dir=tmp_path, permissions=["inbound"])
    with core.installed():
        app = App()

        @app.inbound("event")
        def event(message: Inbound) -> dict:
            if message.body.get("bad"):
                raise ValueError("no such light")
            return {"secret": "never sent"}

    assert core.inbound("event", {"state": "on"}) is None
    answer = app.handle({"id": "d1", "type": "inbound",
                         "data": {"name": "event", "body": {}, "received_ms": 1,
                                  "key_name": "Hub"}})
    assert answer == {"ok": True, "result": None}
    with pytest.raises(CallError, match="no such light"):
        core.inbound("event", {"bad": True})


def test_the_fake_refuses_as_jimmyhq_does_without_the_permission_or_an_object(tmp_path):
    core = FakeCore("house", data_dir=tmp_path, permissions=[])
    with core.installed():
        app = App()
        app.inbound("event")(lambda message: None)
    with pytest.raises(CallError) as caught:
        core.inbound("event", {})
    assert caught.value.code == "not_allowed"
    allowed = FakeCore("house", data_dir=tmp_path, permissions=["inbound"])
    with allowed.installed():
        App().inbound("event")(lambda message: None)
    with pytest.raises(CallError) as caught:
        allowed.inbound("event", ["not", "an", "object"])  # type: ignore[arg-type]
    assert caught.value.code == "invalid" and allowed.inbound_sent == []


def test_an_app_with_no_inbound_handler_says_nothing_about_inbound():
    assert "inbound" not in App(transport=FakeCore("house")).plugs()
    with pytest.raises(ValueError):
        App(transport=FakeCore("house")).inbound("Not A Name")


def test_a_message_goes_from_a_device_through_jimmyhq_to_the_handler_and_back_to_nothing(
        core, tmp_path):
    install_full(core, tmp_path, "house", permissions=["inbound"], inbound=EVENTS)
    got: list[Inbound] = []
    with client_for(core) as owner, client_for(core, address=("192.0.2.10", 5000)) as device, \
            client_for(core) as web:
        assert owner.post("/api/auth/setup", json={"name": "Sam",
                                                   "password": PASSWORD}).is_success
        key = owner.post("/api/apps/house/inbound/keys", json={"label": "Home hub"}).json()["key"]
        app = App(transport=HttpTransport(client=web, token=core.auth.mint_app_token("house")),
                  app_id="house", data_dir=tmp_path / "data")

        @app.inbound("event")
        def event(message: Inbound) -> str:
            got.append(message)
            if message.body.get("fail"):
                raise RuntimeError("the porch light did not answer")
            return "a reply the device must never see"

        app.register()
        for body in ({"state": "on"}, {"fail": True}):
            sent = device.post("/api/in/house/event", json=body,
                               headers={"authorization": f"Bearer {key}"})
            assert sent.status_code == 202 and sent.content == b'{"ok":true}'
        for delivery in inbox.take(core, "house"):
            app._answer(delivery["id"], app.handle(delivery))
        assert [m.body for m in got] == [{"state": "on"}, {"fail": True}]
        assert {m.key_name for m in got} == {"Home hub"}
        seen = owner.get("/api/apps/house/inbound").json()
        assert (seen["received"], seen["failed"], seen["waiting"]) == (2, 1, 0)
        assert "porch light did not answer" in seen["last_error"]
        assert inbound.service(core).store.app("house")["failed"] == 1


def test_the_fake_hands_over_the_senders_parameters_and_a_list_where_the_name_takes_one(
        tmp_path):
    core = FakeCore("house", data_dir=tmp_path, permissions=["inbound"],
                    inbound_arrays=["memory"])
    got: list[Inbound] = []
    with core.installed():
        app = App()
        app.inbound("event")(got.append)
        app.inbound("memory")(got.append)
        app.run()
    core.inbound("event", {"n": 1}, key_name="Pendant", received_ms=5, query={"uid": "abc"})
    core.inbound("memory", [{"text": "one"}], received_ms=6)
    assert got == [
        Inbound(body={"n": 1}, received_ms=5, key_name="Pendant", name="event",
                query={"uid": "abc"}),
        Inbound(body=[{"text": "one"}], received_ms=6, key_name="Test key", name="memory")]
    assert "query" not in core.inbound_sent[1]
    with pytest.raises(CallError) as caught:
        core.inbound("event", [{"text": "one"}])
    assert caught.value.code == "invalid" and "inbound_arrays" in str(caught.value)
    with pytest.raises(CallError):
        core.inbound("event", {}, query={"n": 1})  # type: ignore[dict-item]


def test_a_message_sent_to_an_address_reaches_the_handler_with_its_parameters_and_list(
        core, tmp_path):
    install_full(core, tmp_path, "house", permissions=["inbound"],
                 inbound=[{"name": "memory", "label": "Memories", "array": True}])
    got: list[Inbound] = []
    with client_for(core) as owner, client_for(core, address=("192.0.2.10", 5000)) as device, \
            client_for(core) as web:
        assert owner.post("/api/auth/setup", json={"name": "Sam",
                                                   "password": PASSWORD}).is_success
        made = owner.post("/api/apps/house/inbound/keys",
                          json={"label": "Pendant", "in_address": True}).json()
        app = App(transport=HttpTransport(client=web, token=core.auth.mint_app_token("house")),
                  app_id="house", data_dir=tmp_path / "data")
        app.inbound("memory")(got.append)
        app.register()
        sent = device.post(made["paths"]["memory"] + "?uid=abc&uid=ignored",
                           json=[{"text": "one"}, {"text": "two"}])
        assert sent.status_code == 202
        for delivery in inbox.take(core, "house"):
            app._answer(delivery["id"], app.handle(delivery))
    [message] = got
    assert message.body == [{"text": "one"}, {"text": "two"}]
    assert message.query == {"uid": "abc"} and message.key_name == "Pendant"
    assert made["key"] not in repr(message)
