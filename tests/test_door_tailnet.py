"""The door's address on the owner's private Tailscale network."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from jimmyhq.web.app import create_app
from test_remote_fakes import NAME, PORT, FakeTailscale, install, served_doc

PASSWORD = "correct horse battery"
TAILNET = f"https://{NAME}:{PORT}/mcp"


@pytest.fixture
def cfg(cfg):
    return dataclasses.replace(cfg, port=PORT)


@pytest.fixture
def owner(core):
    with TestClient(create_app(core), base_url="http://testserver") as client:
        assert client.post("/api/auth/setup",
                           json={"name": "Sam", "password": PASSWORD}).status_code == 200
        yield client


def test_the_tailscale_address_is_offered_only_when_jimmyhq_is_shared_there_privately(core, owner):
    install(core, FakeTailscale())
    assert owner.get("/api/door/tailnet").json()["address"] is None
    install(core, FakeTailscale(served=served_doc((PORT, PORT), public=[PORT])))
    assert owner.get("/api/door/tailnet").json()["address"] is None
    install(core, FakeTailscale(served=served_doc((PORT, PORT))))
    assert owner.get("/api/door/tailnet").json() == {
        "address": TAILNET, "in_use": False, "moves_door": True}


def test_one_request_switches_to_the_tailscale_address_and_another_switches_back(core, owner):
    install(core, FakeTailscale(served=served_doc((PORT, PORT))))
    status = owner.post("/api/door/address", json={"use": "tailnet"}).json()
    assert status["address"] == TAILNET and status["listen"] == "main_port"
    assert owner.get("/api/door/tailnet").json() == {
        "address": TAILNET, "in_use": True, "moves_door": False}
    back = owner.post("/api/door/address", json={"use": "local"}).json()
    assert back["public"] is None and back["address"] == f"http://127.0.0.1:{PORT}/mcp"


def test_switching_is_refused_in_words_when_nothing_is_shared_and_is_owner_only(core, owner):
    install(core, FakeTailscale())
    refused = owner.post("/api/door/address", json={"use": "tailnet"})
    assert refused.status_code == 409 and "Tailscale" in refused.json()["detail"]
    assert core.settings.get("door.public_url") == ""
    with TestClient(create_app(core), base_url="http://testserver") as stranger:
        assert stranger.get("/api/door/tailnet").status_code == 401
        assert stranger.post("/api/door/address", json={"use": "local"}).status_code == 401
