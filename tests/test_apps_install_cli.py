"""``jimmyhq apps install|remove|list`` from a terminal."""

from __future__ import annotations

import pytest

from apps_install_helpers import URL, events, example, make_repo, staging, use_local
from jimmyhq import cli
from jimmyhq.apps import install, store


@pytest.fixture
def run(core, cfg, monkeypatch, capsys):
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))

    def go(*argv: str, typed: str | type[BaseException] | None = None) -> tuple[int, str, str]:
        def answer(_prompt=""):
            if typed is None:
                raise AssertionError("the command asked a question")
            if not isinstance(typed, str):
                raise typed  # a closed input (EOFError) or Ctrl-C (KeyboardInterrupt)
            return typed
        monkeypatch.setattr("builtins.input", answer)
        code = cli.main(["apps", *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return go


def test_install_shows_the_plan_and_installs_only_after_yes(run, core, tmp_path):
    folder = str(example(tmp_path, "water"))
    code, out, _ = run("install", folder, typed="no")
    assert code == 1 and "Nothing was installed." in out
    assert "Not reviewed. Install only if you trust who wrote it." in out
    assert "- Add tasks, and mark the ones it added as done." in out
    assert store.installed(core) == {} and staging(core) == []
    code, out, _ = run("install", folder, typed="yes")
    assert code == 0 and "Installed Water 1.0.0." in out
    [event] = events(core, "apps.installed")
    assert event.actor == "owner"


def test_install_with_yes_asks_nothing_for_a_sandboxed_app(run, core, tmp_path):
    assert run("install", str(example(tmp_path, "books_read")), "--yes")[0] == 0
    assert store.is_enabled(core, "books_read")


def test_a_full_app_needs_its_name_even_with_yes(run, core, tmp_path):
    folder = str(example(tmp_path, "drafts"))
    code, out, err = run("install", folder, "--yes", typed="nope")
    assert code == 1 and "type its name" in err and "!! Runs as you" in out
    assert store.installed(core) == {} and staging(core) == []
    assert run("install", folder, "--yes", "--name", "Drafts")[0] == 0
    assert store.get(core, "drafts")["tier"] == "full"


def test_install_from_a_git_address_takes_a_commit(run, core, tmp_path, monkeypatch):
    repo = example(tmp_path, "water")
    commit = make_repo(repo)
    use_local(monkeypatch, repo)
    code, out, _ = run("install", URL, "--commit", commit, "--yes")
    assert code == 0 and f"at commit {commit}" in out
    assert store.get(core, "water")["commit"] == commit


def test_a_refused_address_exits_1_with_the_sentence(run, core):
    for source in ("ext::sh -c id", "http://git.example.com/a.git", "ssh://git.example.com/a"):
        code, out, err = run("install", source, "--commit", "0" * 40, "--yes")
        assert code == 1 and out == "" and "Nothing was fetched or installed" in err


def test_list_and_remove(run, core, tmp_path):
    code, out, _ = run("list")
    assert code == 0 and out.startswith("No apps are installed.")
    run("install", str(example(tmp_path, "water")), "--yes")
    assert run("list")[1] == "water\t1.0.0\tsandboxed\ton\tWater\n"
    data = store.data_dir(core, "water")
    data.mkdir(parents=True)
    (data / "app.db").write_bytes(b"x")
    code, out, _ = run("remove", "water")
    assert code == 0 and out == "Removed Water 1.0.0. Its data was kept.\n" and data.exists()
    code, _, err = run("remove", "water")
    assert code == 1 and "Nothing was changed" in err
    run("install", str(example(tmp_path, "water", "again")), "--yes")
    assert "deleted" in run("remove", "water", "--delete-data")[1] and not data.exists()


@pytest.mark.parametrize("gone", [EOFError, KeyboardInterrupt])
def test_install_with_closed_input_or_ctrl_c_exits_1_with_no_traceback_and_leaves_nothing_staged(
        run, core, tmp_path, gone):
    code, _, err = run("install", str(example(tmp_path, "water")), typed=gone)
    assert code == 1 and "Nothing was installed." in err and "--yes" in err
    assert "Traceback" not in err
    assert store.installed(core) == {} and staging(core) == []


@pytest.mark.parametrize("gone", [EOFError, KeyboardInterrupt])
def test_a_full_app_whose_name_cannot_be_asked_for_exits_1_and_leaves_nothing_staged(
        run, core, tmp_path, gone):
    code, _, err = run("install", str(example(tmp_path, "drafts")), "--yes", typed=gone)
    assert code == 1 and "Nothing was installed." in err and '--name "Drafts"' in err
    assert "Traceback" not in err
    assert store.installed(core) == {} and staging(core) == []


def test_install_with_no_terminal_at_all_exits_1_with_no_traceback(core, cfg, tmp_path):
    """The real command with its input closed, as a script or a pipe would run it."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "JIMMYHQ_DATA": str(cfg.data)}
    done = subprocess.run(
        [sys.executable, "-m", "jimmyhq", "apps", "install", str(example(tmp_path, "water"))],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
    assert done.returncode == 1 and "Nothing was installed." in done.stderr
    assert "Traceback" not in done.stderr and "Install it?" in done.stdout
    assert store.installed(core) == {} and staging(core) == []


def test_an_app_that_ships_with_jimmyhq_installs_by_its_id_as_the_apps_screen_does(run, core):
    code, out, _ = run("install", "water", "--yes")
    assert code == 0 and "Installed Water" in out
    assert "From the apps that ship with JimmyHQ" in out and install.BUNDLED_REVIEW in out
    assert store.is_enabled(core, "water") and staging(core) == []


def test_a_folder_named_like_a_shipped_app_is_installed_as_the_folder_it_is(
        run, core, tmp_path, monkeypatch):
    example(tmp_path, "water")
    monkeypatch.chdir(tmp_path / "src")
    code, out, _ = run("install", "water", "--yes")
    assert code == 0 and "From a folder on this computer" in out
    assert install.NOT_REVIEWED in out and install.BUNDLED_REVIEW not in out


def test_a_name_that_is_neither_a_folder_nor_a_shipped_app_is_refused(run, core):
    code, _, err = run("install", "no_such_app", "--yes")
    assert code == 1 and "nothing was installed" in err and staging(core) == []


def test_apps_with_no_command_prints_help(run):
    code, out, _ = run()
    assert code == 2 and "install" in out
