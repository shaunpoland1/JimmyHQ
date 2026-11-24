"""The web shows which events were erased: the tape list and the live feed both
send the ``erased`` flag, so a screen can say so instead of showing an empty row."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from jimmyhq.web.app import COOKIE, create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}
                      ).status_code == 200
        yield c


def test_the_tape_list_says_which_events_were_erased_and_keeps_their_place(owner, core):
    kept = core.tape.print("setting.updated", {"key": "note", "value": "kept"}, actor="owner")
    gone = core.tape.print("setting.updated", {"key": "note", "value": "secret"}, actor="owner")
    core.tape.erase([gone.id], actor="owner", reason="setting.reset")
    events = {e["id"]: e for e in owner.get("/api/tape?limit=50").json()["events"]}
    assert events[kept.id]["erased"] is False and events[kept.id]["payload"]["value"] == "kept"
    erased = events[gone.id]
    assert erased["erased"] is True and erased["payload"] == {}
    assert erased["kind"] == "setting.updated" and erased["seq"] == gone.seq
    assert "secret" not in json.dumps(erased)
    receipt = next(e for e in events.values() if e["kind"] == "tape.erased")
    assert receipt["erased"] is False
    assert receipt["payload"]["events"] == 1 and receipt["payload"]["kinds"] == {
        "setting.updated": 1}


def test_the_live_feed_sends_the_erased_flag_with_every_event(owner, core):
    cookie = owner.cookies.get(COOKIE)
    chunks: list[str] = []

    def listen():
        with TestClient(create_app(core)) as other:
            other.cookies.set(COOKIE, cookie)
            with other.stream("GET", "/api/feed") as response:
                for chunk in response.iter_text():
                    chunks.append(chunk)
                    if "after.feed" in chunk:
                        return

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    for _ in range(100):
        if chunks:
            break
        threading.Event().wait(0.05)
    core.tape.print("setting.reset", {"key": "after.feed"}, actor="owner")
    thread.join(6)
    sent = "".join(chunks)
    data = next(line for line in sent.splitlines() if "after.feed" in line)
    assert json.loads(data.removeprefix("data: "))["erased"] is False
