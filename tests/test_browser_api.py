"""The owner's Browser tab over HTTP: the owner watches and chooses the level and the
allow-list; nobody else sees it, and nothing here drives the browser."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from jimmyhq import village
from jimmyhq.browser import config, engine
from jimmyhq.browser.fence import Fence
from jimmyhq.browser.page import Look, Tab
from jimmyhq.browser.session import Session
from jimmyhq.village import record, roster
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
A = "agent-a"
URL = f"/api/village/agents/{A}/browser"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver",
                    raise_server_exceptions=False) as c:
        assert c.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}
                      ).status_code == 200
        record.born(core, A, 7, "researcher")
        roster.agent_dir(core, A).mkdir(parents=True, exist_ok=True)
        yield c


def showing(core, picture: bytes | None = PNG) -> Session:
    """What the village process leaves behind after an action."""
    session = Session(core, A, roster.agent_dir(core, A), driver=None, fence=Fence(core),
                      proxy=None)
    session.record(Look(url="https://example.com/beans", title="Beans"),
                   [Tab("https://example.com/beans", "Beans", True),
                    Tab("https://example.com/other", "Other")], picture)
    return session


def test_only_the_signed_in_owner_can_watch_or_change_an_agents_browser(owner, core):
    showing(core)
    for scopes in (["read"], ["read", "write"]):
        token = core.auth.create_token("helper" + str(len(scopes)), scopes)[1]
        with TestClient(owner.app, base_url="http://testserver",
                        headers={"authorization": f"Bearer {token}"},
                        raise_server_exceptions=False) as helper:
            assert helper.get(URL).status_code == 403
            assert helper.get(f"{URL}/picture").status_code == 403
            assert helper.put(URL, json={"submit_level": "alone"}).status_code == 403
    with TestClient(owner.app, base_url="http://testserver",
                    raise_server_exceptions=False) as stranger:
        assert stranger.get(URL).status_code == 401
    assert owner.get("/api/village/agents/..%2Fx/browser").status_code == 404
    assert owner.get("/api/village/agents/nobody/browser").status_code == 404


def test_the_owner_sees_the_tabs_the_address_the_title_and_the_latest_picture(owner, core):
    quiet = owner.get(URL).json()
    assert quiet["running"] is False and quiet["tabs"] == [] and quiet["picture_ms"] is None
    assert owner.get(f"{URL}/picture").status_code == 404
    showing(core)
    seen = owner.get(URL).json()
    assert seen["running"] is True
    assert seen["tabs"] == [
        {"url": "https://example.com/beans", "title": "Beans", "active": True},
        {"url": "https://example.com/other", "title": "Other", "active": False}]
    assert abs(seen["picture_ms"] - time.time() * 1000) < 60_000
    picture = owner.get(f"{URL}/picture")
    assert picture.status_code == 200 and picture.content == PNG
    assert picture.headers["content-type"] == "image/png"
    assert picture.headers["cache-control"] == "no-store"
    showing(core, picture=None)  # pictures switched off: the old one is not left behind
    assert owner.get(f"{URL}/picture").status_code == 404
    showing(core).record_stopped()
    stopped = owner.get(URL).json()
    assert stopped["running"] is False and stopped["tabs"] == []
    assert owner.get(f"{URL}/picture").status_code == 404


def test_the_tab_says_whether_the_agent_holds_the_capability_and_whether_a_browser_is_here(
        owner, core, monkeypatch):
    monkeypatch.setattr(engine, "find", lambda core: None)
    state = owner.get(URL).json()
    assert state["held"] is False and state["ready"] is False and "Install" in state["problem"]
    monkeypatch.setattr(engine, "find", lambda core: "a-browser")
    assert owner.put(f"/api/village/agents/{A}/config",
                     json={"role": "researcher", "capabilities": ["browser"]}).status_code == 200
    state = owner.get(URL).json()
    assert state["held"] is True and state["ready"] is True and state["problem"] is None
    listed = {c["id"]: c for c in owner.get("/api/village/capabilities").json()}
    assert listed["browser"]["label"] == "A browser of its own"
    assert listed["browser"]["settings"] == "browser" and len(listed["browser"]["tools"]) == 9


def test_todays_use_is_counted_from_the_village_log_against_the_limits(owner, core):
    log, now = village.log(core), time.time()
    for kind, times in (("browser_opened", 3), ("browser_acted", 5), ("browser_asked", 1)):
        for _ in range(times):
            log.append(kind, A, now, ok=True)
    log.append("browser_opened", A, now - 3 * 86400, ok=True)  # another day
    log.append("browser_opened", "agent-b", now, ok=True)  # another agent
    limits = owner.get(URL).json()["limits"]
    assert limits == {"pages": {"used": 3, "cap": 100}, "actions": {"used": 5, "cap": 400},
                      "requests": {"used": 1, "cap": 10}}


def test_the_owner_chooses_the_level_with_no_second_question_and_the_allow_list(owner, core):
    assert owner.get(URL).json()["submit_level"] == "approve"
    changed = owner.put(URL, json={"submit_level": "alone"})
    assert changed.status_code == 200 and changed.json()["submit_level"] == "alone"
    assert config.submit_level(core, A) == "alone"
    assert owner.put(URL, json={"submit_level": "read"}).json()["submit_level"] == "read"
    listed = owner.put(URL, json={"allow_hosts": ["Example.com", "*.example.org"]}).json()
    assert listed["allow_hosts"] == ["example.com", "*.example.org"]
    assert listed["submit_level"] == "read"  # what was not sent was not changed
    assert config.allow_hosts(core, A) == ["example.com", "*.example.org"]
    assert owner.put(URL, json={"allow_hosts": []}).json()["allow_hosts"] == []


def test_a_change_that_cannot_be_kept_changes_nothing_and_says_so(owner, core):
    bad_level = owner.put(URL, json={"submit_level": "always"})
    assert bad_level.status_code == 422 and "Nothing was changed" in bad_level.json()["detail"]
    both = owner.put(URL, json={"submit_level": "alone", "allow_hosts": ["https://x/y"]})
    assert both.status_code == 422 and "Nothing was changed" in both.json()["detail"]
    assert config.submit_level(core, A) == "approve" and config.allow_hosts(core, A) == []
    assert owner.put(URL, json={"drive": "click"}).status_code == 422  # there is no driving
    for method in ("post", "delete"):
        assert getattr(owner, method)(URL).status_code in (404, 405)
