from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from jimmyhq import cli, config
from jimmyhq.core import open_core
from jimmyhq.web.app import create_app


@pytest.fixture
def run(core, cfg, monkeypatch, capsys):
    """Run `jimmyhq settings ...` against the same data folder the `core` fixture holds open."""
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))

    def go(*argv: str) -> tuple[int, str, str]:
        code = cli.main(["settings", *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return go


def test_a_value_set_from_the_command_line_is_read_back_and_recorded_as_the_owner(run, core):
    assert run("set", "appearance.theme", "dark")[0] == 0
    code, out, _ = run("get", "appearance.theme")
    assert (code, out) == (0, "dark\n")
    [event] = core.tape.after(0, kind="setting.updated")
    assert (event.payload, event.actor) == ({"key": "appearance.theme", "value": "dark"}, "owner")


def test_values_are_json_and_plain_text_works_where_the_setting_is_text(run, core):
    assert run("set", "access.session_days", "14")[0] == 0
    assert run("set", "statusbar.show", "false")[0] == 0
    assert run("set", "owner.name", "Sam Q")[0] == 0
    assert run("set", "owner.name", "123")[0] == 0
    assert run("set", "appearance.sidebar_order", '["chat","bench"]')[0] == 0
    assert core.settings.get("access.session_days") == 14
    assert core.settings.get("statusbar.show") is False
    assert core.settings.get("owner.name") == "123"
    assert core.settings.get("appearance.sidebar_order") == ["chat", "bench"]


def test_a_refused_value_says_what_to_do_and_exits_with_a_failure(run, core):
    for argv in (("set", "access.session_days", "0"), ("set", "access.session_days", "lots"),
                 ("set", "no.such", "1"), ("get", "no.such"), ("reset", "no.such")):
        code, out, err = run(*argv)
        assert code == 1 and out == "" and err.strip(), argv
    assert "jimmyhq settings list" in run("get", "no.such")[2]
    assert "whole number" in run("set", "access.session_days", "lots")[2]
    assert core.tape.after(0, kind="setting.updated") == []


def test_list_shows_one_setting_per_line_and_changed_shows_only_what_is_not_default(run):
    run("set", "appearance.theme", "dark")
    _, everything, _ = run("list")
    assert 'appearance.theme = "dark"' in everything and "statusbar.show = true" in everything
    assert run("list", "--changed")[1] == 'appearance.theme = "dark"\n'


def test_reset_puts_a_setting_back_to_its_default(run, core):
    run("set", "access.session_days", "7")
    code, out, _ = run("reset", "access.session_days")
    assert code == 0 and "30" in out and core.settings.get("access.session_days") == 30


def test_export_prints_or_writes_a_file_and_a_profile_leaves_out_personal_settings(run, tmp_path):
    run("set", "owner.name", "Sam")
    run("set", "appearance.theme", "dark")
    profile = json.loads(run("export", "--profile", "--changed", "--name", "Mine")[1])
    assert profile == {"jimmyhq_settings": 1, "name": "Mine",
                       "settings": {"appearance.theme": "dark"}}
    target = tmp_path / "all.json"
    code, out, _ = run("export", "-o", str(target))
    assert code == 0 and "Wrote" in out
    assert json.loads(target.read_text(encoding="utf-8"))["settings"]["owner.name"] == "Sam"


def test_import_applies_a_file_and_dry_run_only_describes_it(run, core, tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"jimmyhq_settings": 1, "name": "Calm", "settings": {
        "appearance.theme": "dark", "statusbar.show": True}}), encoding="utf-8")
    code, out, _ = run("import", str(path), "--dry-run")
    assert code == 0 and "Profile: Calm" in out and "1 would change" in out
    assert core.settings.get("appearance.theme") == "system"
    code, out, _ = run("import", str(path))
    assert code == 0 and "1 changed" in out and core.settings.get("appearance.theme") == "dark"


def test_import_refuses_a_file_with_a_bad_entry_unless_told_to_skip_it(run, core, tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"jimmyhq_settings": 1, "settings": {
        "appearance.theme": "dark", "app.nope.thing": 1}}), encoding="utf-8")
    for argv in (("--dry-run",), ()):
        code, out, err = run("import", str(path), *argv)
        assert code == 1 and "app.nope.thing" in out and "--skip-invalid" in err
    assert core.settings.get("appearance.theme") == "system"
    code, out, _ = run("import", str(path), "--skip-invalid")
    assert code == 0 and "1 refused" in out and core.settings.get("appearance.theme") == "dark"


def test_a_file_that_cannot_be_read_exits_with_two_and_says_what_to_do(run, tmp_path):
    code, _, err = run("import", str(tmp_path / "missing.json"))
    assert code == 2 and "Check the path" in err
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    code, _, err = run("import", str(broken))
    assert code == 2 and "not valid JSON" in err
    newer = tmp_path / "newer.json"
    newer.write_text('{"jimmyhq_settings": 9, "settings": {}}', encoding="utf-8")
    code, _, err = run("import", str(newer))
    assert code == 1 and "Update JimmyHQ" in err


def test_a_running_server_sees_a_command_line_change_on_its_next_read(run, core):
    """`core` stands in for the running server, with its own open stores and cache."""
    with TestClient(create_app(core), base_url="http://testserver") as client:
        client.post("/api/auth/setup", json={"name": "Sam", "password": "correct horse battery"})
        theme = next(x for x in client.get("/api/settings").json()["settings"]
                     if x["key"] == "appearance.theme")
        assert theme["value"] == "system"
        assert run("set", "appearance.theme", "dark")[0] == 0
        theme = next(x for x in client.get("/api/settings").json()["settings"]
                     if x["key"] == "appearance.theme")
        assert theme["value"] == "dark"


def test_two_open_copies_of_the_settings_can_both_write_and_both_see_every_change(cfg):
    server = open_core(cfg)
    other = open_core(config.Config(data=cfg.data, host=cfg.host, port=cfg.port))
    try:
        server.settings.set("appearance.theme", "dark", actor="owner")
        other.settings.set("access.session_days", 7, actor="owner")
        server.settings.set("statusbar.show", False, actor="owner")
        for copy in (server, other):
            assert copy.settings.get("appearance.theme") == "dark"
            assert copy.settings.get("access.session_days") == 7
            assert copy.settings.get("statusbar.show") is False
    finally:
        other.close()
        server.close()


def test_a_second_process_can_change_a_setting_while_the_first_has_the_data_open(core, cfg):
    env = {**os.environ, "JIMMYHQ_DATA": str(cfg.data)}
    result = subprocess.run(
        [sys.executable, "-m", "jimmyhq.cli", "settings", "set", "appearance.theme", "dark"],
        env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    assert core.settings.get("appearance.theme") == "dark"
