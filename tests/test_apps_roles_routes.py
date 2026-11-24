"""Roles over HTTP: only the owner reads an app's roles or fills them, at install and on
the app's page."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps_roles_helpers import agent, caps, install_roles_app, roles_folder
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def app(core):
    return create_app(core)


@pytest.fixture
def owner(app):
    with TestClient(app) as client:
        made = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
        assert made.status_code == 200
        yield client


@pytest.fixture
def token(app, owner, core):
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(app, headers={"authorization": f"Bearer {secret}"}) as client:
        yield client


def test_the_install_dialog_reads_a_plans_roles_and_the_agents_to_choose_from(core, owner,
                                                                            tmp_path):
    agent(core, "agent-a", name="Wren")
    plan = owner.post("/api/apps/_install/plan",
                      json={"folder": str(roles_folder(tmp_path))}).json()
    got = owner.get(f"/api/apps/_install/roles/{plan['plan']}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert [r["id"] for r in body["roles"]] == ["writer", "checker"]
    assert body["agents"][0]["label"] == "Wren"
    assert owner.get("/api/apps/_install/roles/not-a-plan").status_code == 404


def test_the_owner_fills_and_empties_a_role_on_the_apps_page(core, owner, tmp_path):
    install_roles_app(core, tmp_path)
    agent(core, "agent-a")
    put = owner.put("/api/apps/posts/roles",
                    json={"choices": {"writer": {"mode": "existing", "agent_id": "agent-a"}}})
    assert put.status_code == 200, put.text
    assert caps(core, "agent-a") == ["app.posts.writer"]
    shown = owner.get("/api/apps/posts/roles").json()
    assert shown["roles"][0]["holder"]["agent_id"] == "agent-a"
    both = owner.put("/api/apps/posts/roles",
                     json={"choices": {"checker": {"mode": "existing",
                                                   "agent_id": "agent-a"}}})
    assert both.status_code == 409 and "keeps apart" in both.json()["detail"]
    owner.put("/api/apps/posts/roles", json={"choices": {"writer": {"mode": "empty"}}})
    assert caps(core, "agent-a") == []


def test_a_token_cannot_read_or_fill_an_apps_roles(core, owner, token, tmp_path):
    install_roles_app(core, tmp_path)
    agent(core, "agent-a")
    assert token.get("/api/apps/posts/roles").status_code == 403
    put = token.put("/api/apps/posts/roles",
                    json={"choices": {"writer": {"mode": "existing", "agent_id": "agent-a"}}})
    assert put.status_code == 403
    assert caps(core, "agent-a") == []


def test_the_owner_sees_an_unnamed_agent_by_its_working_name_and_an_app_never_does():
    from jimmyhq.apps import roles

    assert roles.owner_label("agent-b", {"name": None}) == "Agent B"
    assert roles.owner_label("agent-27", None) == "Agent 27"
    assert roles.owner_label("agent-b", {"name": "Wren"}) == "Wren"
    assert "agent" not in roles.agent_label({"name": None, "id": "agent-b"}).lower().replace(
        "an agent", "")
