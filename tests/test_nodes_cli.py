"""The commands typed on the second machine, and its service definition."""

from __future__ import annotations

import pytest

from jimmyhq import service
from jimmyhq.cli import main
from jimmyhq.nodes import agent


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("JIMMYHQ_DATA", str(tmp_path))
    return tmp_path


def test_a_machine_that_has_not_joined_says_so_and_will_not_run(data, capsys):
    assert main(["node", "status"]) == 0
    assert "This machine has not joined a hub." in capsys.readouterr().out
    assert main(["node", "run"]) == 2
    assert main(["node", "leave"]) == 1
    assert "Nothing was changed" in capsys.readouterr().err
    assert not (data / "tape.db").exists()  # a node never opens a Tape of its own


def test_a_model_server_is_offered_and_taken_back_in_the_nodes_own_config(data, capsys):
    assert main(["node", "offer", "model", "--name", "big",
                 "--url", "http://127.0.0.1:8080/v1/"]) == 0
    assert agent.NodeStore(data).read()["models"] == {"big": "http://127.0.0.1:8080/v1"}
    assert main(["node", "status"]) == 0
    assert "big  http://127.0.0.1:8080/v1" in capsys.readouterr().out
    assert main(["node", "offer", "model", "--name", "big", "--remove"]) == 0
    assert agent.NodeStore(data).read()["models"] == {}


def test_an_offer_that_cannot_be_used_changes_nothing_and_says_what_to_type(data, capsys):
    for args in (["--name", "Big One", "--url", "http://127.0.0.1:8080/v1"],
                 ["--name", "big", "--url", "127.0.0.1:8080"],
                 ["--name", "big", "--url", "http://user:pw@" + "127.0.0.1:8080/v1"],
                 ["--name", "big"],
                 ["--name", "gone", "--remove"]):
        assert main(["node", "offer", "model", *args]) == 1
        assert "Nothing was changed" in capsys.readouterr().err
    assert agent.NodeStore(data).read()["models"] == {}


def test_joining_over_plain_http_to_a_public_address_is_refused_before_anything_is_sent(
        data, capsys):
    assert main(["node", "join", "http://192.0.2.10:8440", "AAAAA-AAAAA"]) == 1
    said = capsys.readouterr().err
    assert said.startswith("Nothing was sent") and "--allow-plain-http" in said
    assert agent.NodeStore(data).token is None


def test_joining_a_hub_that_is_not_there_says_what_to_check(data, capsys):
    assert main(["node", "join", "http://127.0.0.1:9", "AAAAA-AAAAA", "--name", "box"]) == 1
    said = capsys.readouterr().err
    assert "Nothing was joined: the hub at http://127.0.0.1:9 could not be reached" in said
    assert main(["node", "join", "not a url at all", "AAAAA-AAAAA"]) == 1
    assert "Nothing was joined. The hub's address" in capsys.readouterr().err


def test_the_nodes_service_runs_node_run_under_a_name_of_its_own(tmp_path):
    mac = service.render("darwin", tmp_path, env={}, node=True)
    assert mac.path.name == "ai.jimmyhq.node.plist"
    assert "<string>node</string>\n    <string>run</string>" in mac.content
    assert mac.disable.endswith("ai.jimmyhq.node")
    linux = service.render("linux", tmp_path, env={}, node=True)
    assert linux.path.name == "jimmyhq-node.service" and '"node" "run"' in linux.content
    assert "enable --now jimmyhq-node" in linux.enable
    windows = service.render("win32", tmp_path, env={}, account="BOX\\sam", node=True)
    assert "JimmyHQNode" in windows.enable and "node run" in windows.content
    # The hub's own service is as it was.
    hub = service.render("linux", tmp_path, env={})
    assert hub.path.name == "jimmyhq.service" and '"node"' not in hub.content


def test_the_service_command_prints_the_nodes_definition(data, capsys):
    assert main(["service", "print", "--node"]) == 0
    assert "node" in capsys.readouterr().out
