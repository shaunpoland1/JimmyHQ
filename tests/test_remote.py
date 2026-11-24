"""Finding Tailscale and reading what it says. The command line tool is always faked."""

from __future__ import annotations

import dataclasses
import subprocess
import sys

import pytest

from jimmyhq import modules
from jimmyhq.remote import tailscale as ts
from jimmyhq.settings import SettingError, sections
from test_remote_fakes import CLI, NAME, PORT, FakeTailscale, Ran, install, ready_status, served_doc


@pytest.fixture
def cfg(cfg):
    return dataclasses.replace(cfg, port=PORT)


def test_remote_access_is_a_module_whose_one_setting_lives_in_the_access_section(core):
    assert "remote" in modules.MODULES
    assert core.settings.get("remote.tailscale_path") == ""
    assert dict(sections())["access"] == "Access"
    every = core.settings.describe()["settings"]
    spec = next(s for s in every if s["key"] == "remote.tailscale_path")
    assert spec["section"] == "access" and spec["portable"] is False


def test_a_command_path_with_quotes_or_a_line_break_is_cleaned_or_refused(core):
    core.settings.set("remote.tailscale_path", ' "C:\\Tools\\tailscale.exe" ', actor="owner")
    assert core.settings.get("remote.tailscale_path") == "C:\\Tools\\tailscale.exe"
    with pytest.raises(SettingError, match="one line"):
        core.settings.set("remote.tailscale_path", "a\nb", actor="owner")


def test_tailscale_is_reported_not_installed_when_it_is_nowhere_to_be_found(core):
    fake = install(core, FakeTailscale(), found=False)
    found = ts.detect(core)
    assert found.state == "not_installed" and found.path is None
    assert fake.calls == []  # nothing to run


def test_tailscale_is_found_in_the_usual_install_folders_when_it_is_not_on_the_path(core, tmp_path):
    install(core, FakeTailscale(), found=False)
    program = tmp_path / "tailscale"
    program.write_text("")
    ts.service(core).standard = lambda: [str(tmp_path / "nothing-here"), str(program)]
    found = ts.detect(core)
    assert (found.source, found.path, found.state) == ("standard", str(program), "ready")


def test_a_path_in_the_setting_is_used_before_any_search(core, tmp_path):
    fake = install(core, FakeTailscale())
    program = tmp_path / "my tools" / "tailscale"
    program.parent.mkdir()
    program.write_text("")
    core.settings.set("remote.tailscale_path", str(program), actor="owner")
    found = ts.detect(core)
    assert found.source == "setting" and not found.setting_missing
    assert fake.calls[0][0] == str(program)


def test_a_path_in_the_setting_that_points_at_nothing_falls_back_to_the_search_and_says_so(
        core, tmp_path):
    fake = install(core, FakeTailscale())
    core.settings.set("remote.tailscale_path", str(tmp_path / "gone"), actor="owner")
    found = ts.detect(core)
    assert found.setting_missing and found.source == "path" and fake.calls[0][0] == CLI
    install(core, FakeTailscale(), found=False)
    assert ts.detect(core).state == "not_installed" and ts.detect(core).setting_missing


@pytest.mark.parametrize("said", [
    "failed to connect to local tailscaled; it doesn't appear to be running",
    "Failed to connect to local Tailscale service; is Tailscale running?",
])
def test_tailscale_that_cannot_be_reached_is_reported_not_running(core, said):
    install(core, FakeTailscale(status=Ran(1, "", said)))
    assert ts.detect(core).state == "not_running"


@pytest.mark.parametrize(("word", "state"), [
    ("NeedsLogin", "needs_login"), ("NoState", "needs_login"),
    ("NeedsMachineAuth", "needs_login"), ("Stopped", "stopped"), ("Running", "ready"),
    ("SomethingNew", "unknown"),
])
def test_each_backend_state_tailscale_reports_maps_to_one_plain_state(core, word, state):
    install(core, FakeTailscale(status=ready_status(BackendState=word)))
    found = ts.detect(core)
    assert found.state == state and found.backend_state == word


def test_a_stopped_message_without_json_is_reported_stopped(core):
    install(core, FakeTailscale(status=Ran(1, "Tailscale is stopped.\n")))
    assert ts.detect(core).state == "stopped"


def test_a_ready_tailscale_gives_this_computers_name_lower_case_without_the_final_dot(core):
    loud = "Example-Machine.Example-Tailnet.ts.net."
    install(core, FakeTailscale(status=ready_status(Self={"DNSName": loud})))
    found = ts.detect(core)
    assert found.state == "ready" and found.name == NAME and found.https_enabled is True


def test_an_old_cli_that_leaves_out_fields_is_still_understood(core):
    # No BackendState and no CertDomains.
    install(core, FakeTailscale(status={"Self": {"DNSName": NAME + "."}}))
    found = ts.detect(core)
    assert found.state == "ready" and found.name == NAME and found.https_enabled is None
    install(core, FakeTailscale(status={"Version": "1.0"}))  # nothing useful at all
    assert ts.detect(core).state == "unknown"
    install(core, FakeTailscale(status=ready_status(Self=None)))
    assert ts.detect(core).name is None


def test_a_running_tailscale_with_https_switched_off_is_reported_so(core):
    install(core, FakeTailscale(status=ready_status(CertDomains=[])))
    assert ts.detect(core).https_enabled is False


def test_output_that_is_not_json_or_a_timeout_is_reported_unknown_not_a_crash(core):
    install(core, FakeTailscale(status=Ran(0, "<html>captive portal</html>")))
    assert ts.detect(core).state == "unknown"
    install(core, FakeTailscale(status=Ran(None, "", "", timed_out=True)))
    assert ts.detect(core).state == "unknown"
    install(core, FakeTailscale(status=Ran(None, "", "permission denied")))
    assert ts.detect(core).state == "unknown"


def test_a_name_that_is_not_a_host_name_is_never_accepted(core):
    for bad in ("evil name/../x.example.", "a b.example", "-x.example", "", 7, ["x"]):
        install(core, FakeTailscale(status=ready_status(Self={"DNSName": bad})))
        assert ts.detect(core).name is None, bad


def test_an_existing_mapping_to_jimmyhqs_port_is_found_with_its_https_port(core):
    install(core, FakeTailscale(served=served_doc((443, PORT))))
    found = ts.detect(core)
    entry = found.entry(PORT)
    assert found.serve_known and entry is not None and entry.https_port == 443
    assert entry.public is False


def test_a_mapping_that_goes_somewhere_else_is_not_ours_and_clashes_on_its_port(core):
    install(core, FakeTailscale(served=served_doc((PORT, 3000), (9000, None))))
    found = ts.detect(core)
    assert found.entry(PORT) is None
    assert found.clash(PORT) is not None and found.clash(9000) is not None


def test_a_mapping_that_the_internet_can_reach_is_flagged_public(core):
    install(core, FakeTailscale(served=served_doc((PORT, PORT), public=[PORT])))
    assert ts.detect(core).entry(PORT).public is True


def test_mappings_under_another_machine_name_are_ignored(core):
    doc = served_doc((PORT, PORT))
    doc["Web"] = {k.replace(NAME, "other.example-tailnet.ts.net"): v for k, v in doc["Web"].items()}
    install(core, FakeTailscale(served=doc))
    assert ts.detect(core).entries == []


def test_an_old_cli_without_serve_status_json_leaves_the_mapping_unknown(core):
    fake = install(core, FakeTailscale())
    original = fake.__call__

    def old(argv, timeout):
        if list(argv[1:]) == ["serve", "status", "--json"]:
            return Ran(1, "", "unknown flag: --json")
        return original(argv, timeout)

    ts.service(core).runner = old
    found = ts.detect(core)
    assert found.state == "ready" and found.serve_known is False and found.entries == []


def test_detection_only_reads_and_never_asks_for_a_change(core):
    fake = install(core, FakeTailscale(served=served_doc((PORT, PORT))))
    ts.detect(core)
    assert fake.calls == [[CLI, "status", "--json"], [CLI, "serve", "status", "--json"]]
    assert all(t <= ts.READ_TIMEOUT_S for t in fake.timeouts)


def test_the_turn_on_command_maps_https_to_the_local_port_and_never_funnels():
    args = ts.serve_args(PORT)
    assert args == ["serve", "--bg", f"--https={PORT}", f"http://127.0.0.1:{PORT}"]
    assert ts.shown(args) == f"tailscale serve --bg --https={PORT} http://127.0.0.1:{PORT}"
    assert ts.shown(ts.off_args(PORT)) == f"tailscale serve --https={PORT} off"
    assert "funnel" not in " ".join(args + ts.off_args(PORT))


def test_an_address_leaves_out_the_default_https_port():
    assert ts.address(NAME, 443) == f"https://{NAME}"
    assert ts.address(NAME, 8440) == f"https://{NAME}:8440"


def test_the_real_runner_uses_an_argument_list_with_no_shell_no_input_and_a_time_limit(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return subprocess.CompletedProcess(args, 0, b'{"ok": 1}', b"")

    monkeypatch.setattr(ts.subprocess, "run", fake_run)
    ran = ts.run_argv(["/some dir/tailscale; echo hi", "status", "--json"], 3)
    assert ran == Ran(0, '{"ok": 1}', "")
    assert isinstance(seen["args"], list) and seen["args"][0] == "/some dir/tailscale; echo hi"
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["timeout"] == 3
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL


def test_the_real_runner_reports_a_timeout_and_a_missing_program_without_raising(
        monkeypatch, tmp_path):
    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output=b"partial")

    monkeypatch.setattr(ts.subprocess, "run", slow)
    ran = ts.run_argv(["x"], 1)
    assert ran.timed_out and ran.code is None and ran.out == "partial"
    monkeypatch.undo()
    assert ts.run_argv([str(tmp_path / "missing")], 1).code is None


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in program is a script")
def test_a_real_program_in_a_folder_with_odd_characters_runs_without_a_shell(core, tmp_path):
    folder = tmp_path / "it's; a folder $(odd)"
    folder.mkdir()
    program = folder / "tailscale"
    program.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"print(json.dumps({{'BackendState': 'Running', 'Self': {{'DNSName': '{NAME}.'}}, "
        f"'args': sys.argv[1:]}}))\n")
    program.chmod(0o755)
    install(core, None)
    svc = ts.service(core)
    svc.runner, svc.which = ts.run_argv, lambda _n: None
    core.settings.set("remote.tailscale_path", str(program), actor="owner")
    found = ts.detect(core)
    assert found.state == "ready" and found.name == NAME and found.source == "setting"
