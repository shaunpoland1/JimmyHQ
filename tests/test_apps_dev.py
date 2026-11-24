"""Writing an app: ``jimmyhq apps new``, ``check`` and ``dev``, and the Watering example.
The development loop is driven a step at a time; nothing is started and nothing reaches
the network."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from apps_install_helpers import install_folder
from apps_roles_helpers import agent, caps, kinds_printed, roles_folder
from jimmyhq import cli
from jimmyhq.apps import bridge, dev, inbound, install, plugs, roles, store, update
from jimmyhq.apps.store import AppError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))
EXAMPLE = ROOT / "apps" / "examples" / "watering"

from jimmyhq_app.testing import FakeCore  # noqa: E402


def yes(_plan):
    return ""


def no(_plan):
    return None


# --- new -------------------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["full", "sandboxed"])
def test_a_new_app_passes_the_checks_and_installs(core, tmp_path, tier):
    folder = dev.new("plant_care", tmp_path / "plant_care", tier=tier)
    assert dev.passes(dev.check(core, folder))
    done = install_folder(core, folder)
    assert done["id"] == "plant_care" and done["tier"] == tier


def test_a_new_full_apps_own_test_runs_against_the_fake_core(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care")
    fake = FakeCore("plant_care", data_dir=tmp_path / "data",
                    permissions=["bench.file", "jobs"])
    sys.path.insert(0, str(folder))
    try:
        with fake.installed():
            from plant_care import main  # noqa: F401
            card = fake.run_job("check")
            fake.decide(card, "yes")
            assert fake.call("answers") == [[card, "yes"]]
            assert fake.emitted == [{"event": "answers.changed", "data": None}]
    finally:
        sys.path.remove(str(folder))
        for name in [n for n in sys.modules if n == "plant_care" or n.startswith("plant_care.")]:
            del sys.modules[name]


def test_a_new_app_is_never_written_over_a_folder_that_is_there(tmp_path):
    (tmp_path / "taken").mkdir()
    with pytest.raises(FileExistsError, match="nothing was written"):
        dev.new("plant_care", tmp_path / "taken")


@pytest.mark.parametrize("bad", ["Plant", "p", "1plant", "plant-care", "../up"])
def test_a_new_apps_id_is_held_to_the_id_rule(tmp_path, bad):
    with pytest.raises(ValueError, match="Nothing was written"):
        dev.new(bad, tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_every_file_a_new_app_gets_ends_lines_with_a_plain_newline(tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care")
    for path in folder.rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes(), path.name


# --- check -----------------------------------------------------------------------------------


def test_check_names_each_mistake_in_the_manifest(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care")
    data = json.loads((folder / "jimmyhq.app.json").read_text(encoding="utf-8"))
    data |= {"version": "one", "colour": "green"}
    (folder / "jimmyhq.app.json").write_text(json.dumps(data), encoding="utf-8")
    found = dev.check(core, folder)
    assert not dev.passes(found)
    assert {f.where for f in found} >= {"version", "colour"}


def test_check_finds_python_that_does_not_read_without_running_it(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care")
    marker = tmp_path / "ran"
    (folder / "plant_care" / "broken.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\ndef (:\n", encoding="utf-8")
    found = dev.check(core, folder)
    assert [f.where for f in found if f.level == "problem"] == ["plant_care/broken.py"]
    assert not marker.exists()


def test_check_refuses_a_lock_file_missing_and_warns_without_a_project(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care")
    assert [f.level for f in dev.check(core, folder)] == ["warning"]
    (folder / "pyproject.toml").write_text("[project]\nname = 'plant-care'\n", encoding="utf-8")
    [finding] = dev.check(core, folder)
    assert finding.level == "problem" and "uv lock" in finding.sentence


def test_check_says_where_to_point_when_there_is_no_folder(core, tmp_path):
    [finding] = dev.check(core, tmp_path / "nowhere")
    assert finding.level == "problem" and "jimmyhq.app.json" in finding.sentence


def test_check_from_the_terminal_says_whether_it_passes(core, cfg, tmp_path, monkeypatch,
                                                        capsys):
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))
    folder = dev.new("plant_care", tmp_path / "plant_care")
    assert cli.main(["apps", "check", str(folder)]) == 0
    assert "It passes the checks." in capsys.readouterr().out
    (folder / "jimmyhq.app.json").write_text("{}", encoding="utf-8")
    assert cli.main(["apps", "check", str(folder)]) == 1
    assert "It does not pass. Nothing was changed." in capsys.readouterr().out


def test_new_from_the_terminal_writes_the_skeleton(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMMYHQ_DATA", str(cfg.data))
    where = tmp_path / "made"
    assert cli.main(["apps", "new", "plant_care", "--folder", str(where)]) == 0
    assert (where / "jimmyhq.app.json").is_file()
    assert "jimmyhq apps dev" in capsys.readouterr().out


# --- dev -------------------------------------------------------------------------------------


def run_loop(core, folder, *, agree=yes):
    said: list[str] = []
    stop = threading.Event()
    out: dict = {}
    thread = threading.Thread(target=lambda: out.update(code=dev.watch(
        core, folder, agree=agree, say=said.append, stop=stop, poll_s=0.05)))
    thread.start()
    return thread, stop, said, out


def wait_for(check, what, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f"gave up waiting for {what}")


def test_dev_installs_marks_development_and_puts_each_change_in_place(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care", tier="sandboxed")
    thread, stop, said, _ = run_loop(core, folder)
    try:
        wait_for(lambda: store.get(core, "plant_care") is not None, "the install")
        # The mark is written just after the install lands: wait for it, do not race it.
        wait_for(lambda: dev.is_development(core, "plant_care"), "the development mark")
        assert bridge.find_app(core, "plant_care").development
        first = store.get(core, "plant_care")["tree"]
        (folder / "web" / "index.html").write_text("<!doctype html><p>New</p>\n",
                                                   encoding="utf-8")
        wait_for(lambda: (store.get(core, "plant_care") or {}).get("tree") not in (None, first),
                 "the new copy")
    finally:
        stop.set()
        thread.join(30)
    assert any("Put the new copy" in line for line in said)
    assert not dev.is_development(core, "plant_care")  # the loop ended
    assert store.get(core, "plant_care") is not None  # what was installed stays


def test_dev_keeps_the_apps_data_across_a_new_copy(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care", tier="sandboxed")
    dev.install_copy(core, folder, replacing=None, agree=yes)
    data = store.data_dir(core, "plant_care")
    data.mkdir(parents=True, exist_ok=True)
    (data / "kept.txt").write_text("still here", encoding="utf-8")
    m = store.manifest_of(core, "plant_care")
    (folder / "web" / "app.js").write_text("// changed\n", encoding="utf-8")
    dev.install_copy(core, folder, replacing=m, agree=no)
    assert (store.data_dir(core, "plant_care") / "kept.txt").read_text(
        encoding="utf-8") == "still here"


def typed(plan):
    return plan["app"]["name"]


@pytest.fixture
def developed(core, tmp_path):
    """A full app under ``apps dev`` with a secret typed in, a role filled and a device
    key made, as the owner leaves it between two saves."""
    folder = roles_folder(tmp_path, permissions=("agents.tools", "inbound"),
                          inbound=[{"name": "event", "label": "Home events"}])
    m = dev.install_copy(core, folder, replacing=None, agree=typed)
    core.secrets.set("app.posts.api_key", "typed in by the owner")
    agent(core, "agent-a")
    roles.fill(core, "posts", "writer", "agent-a")
    inbound.make_key(core, "posts", "Home hub", actor="owner")
    return folder, m


def test_a_new_copy_under_dev_keeps_the_apps_secrets_roles_and_device_keys(core, developed):
    folder, m = developed
    (folder / "roles" / "writer.md").write_text("# Writer\n\nShorter.\n", encoding="utf-8")
    dev.install_copy(core, folder, replacing=m, agree=no)
    assert "app.posts.api_key" in core.secrets.names()
    assert roles.holder(core, "posts", "writer").agent_id == "agent-a"
    assert "app.posts.writer" in caps(core, "agent-a")
    kept = inbound.state(core, "posts")
    assert [k["label"] for k in kept["keys"]] == ["Home hub"] and kept["open"] is True


def test_a_new_copy_under_dev_is_recorded_as_an_update_not_a_removal(core, developed):
    folder, m = developed
    before = store.get(core, "posts")["tree"]
    (folder / "posts_main" / "__init__.py").write_text("# changed\n", encoding="utf-8")
    dev.install_copy(core, folder, replacing=m, agree=no)
    assert kinds_printed(core, "apps.removed") == []
    [updated] = kinds_printed(core, "apps.updated")
    assert updated.payload["from_version"] == updated.payload["version"] == "1.0.0"
    assert store.get(core, "posts")["tree"] == updated.payload["tree"] != before


def test_a_copy_that_changed_nothing_is_left_as_it_is(core, developed):
    folder, m = developed
    dev.install_copy(core, folder, replacing=m, agree=no)
    assert kinds_printed(core, "apps.updated") == kinds_printed(core, "apps.removed") == []


def test_a_development_copy_cannot_go_in_once_the_app_is_no_longer_developed(core, developed):
    folder, _m = developed
    (folder / "posts_main" / "__init__.py").write_text("# changed\n", encoding="utf-8")
    plan = update.plan_folder(core, "posts", folder, development=True)
    dev._mark(core, "posts", None)  # noqa: SLF001 - the loop ended meanwhile
    with pytest.raises(AppError) as refused:
        update.confirm_update(core, plan["plan"], plan["fingerprint"])
    assert refused.value.status == 409 and "Nothing was changed" in refused.value.detail
    assert kinds_printed(core, "apps.updated") == []


def test_outside_development_a_copy_of_the_same_version_is_still_not_an_update(
        core, developed):
    folder, _m = developed
    (folder / "posts_main" / "__init__.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(AppError, match="not an update"):
        update.plan_folder(core, "posts", folder)


def test_dev_stops_rather_than_grant_a_permission_nobody_agreed_to(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care", tier="sandboxed")
    m = dev.install_copy(core, folder, replacing=None, agree=yes)
    data = json.loads((folder / "jimmyhq.app.json").read_text(encoding="utf-8"))
    data["permissions"] = ["tasks.read"]
    (folder / "jimmyhq.app.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(dev.ConsentNeeded, match="not agreed to"):
        dev.install_copy(core, folder, replacing=m, agree=yes)
    assert store.granted(core, "plant_care") == ()


def test_dev_installs_nothing_when_the_owner_says_no(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care", tier="sandboxed")
    thread, _stop, said, out = run_loop(core, folder, agree=no)
    thread.join(30)
    assert out["code"] == 1 and store.get(core, "plant_care") is None
    assert "Nothing was installed." in said


def test_dev_installs_nothing_that_fails_the_checks(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care", tier="sandboxed")
    (folder / "jimmyhq.app.json").write_text("{}", encoding="utf-8")
    thread, _stop, said, out = run_loop(core, folder)
    thread.join(30)
    assert out["code"] == 1 and store.get(core, "plant_care") is None


def test_a_copy_asking_for_the_same_things_is_the_same_consent(core, tmp_path):
    folder = dev.new("plant_care", tmp_path / "plant_care")
    m = store.manifest_of(core, install_folder(core, folder)["id"])
    assert dev.same_consent(m, m)
    import dataclasses
    assert not dev.same_consent(m, dataclasses.replace(m, permissions=("jobs",)))
    assert not dev.same_consent(m, dataclasses.replace(m, tier="sandboxed"))


# --- the Watering example --------------------------------------------------------------------


def load_watering():
    spec = importlib.util.spec_from_file_location("example_watering_main",
                                                  EXAMPLE / "watering" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_watering_example_asks_keeps_the_answer_and_never_files_twice(tmp_path):
    fake = FakeCore("watering", data_dir=tmp_path, settings={"ask_every_minutes": 1440},
                    permissions=["bench.file", "jobs"], cards=["round"])
    with fake.installed():
        load_watering().app.run()
    assert fake.plugs["jobs"] == [{"name": "ask", "every_setting": "ask_every_minutes"}]
    assert fake.plugs["decisions"] == ["round"] and fake.plugs["calls"] == ["rounds"]
    card = fake.run_job("ask")
    assert fake.run_job("ask") == card  # still waiting: updated, not filed again
    assert len(fake.cards) == 1
    assert [a["id"] for a in fake.cards[card]["actions"]] == ["watered", "skipped"]
    fake.decide(card, "watered")
    [row] = fake.call("rounds")
    assert row["card"] == card and row["answer"] == "watered"
    assert {"event": "rounds.changed", "data": None} in fake.emitted


def test_the_watering_example_passes_the_checks_and_comes_with_jimmyhq(core):
    assert dev.passes(dev.check(core, EXAMPLE))
    plan = install.plan_example(core, "watering")
    assert plan["provenance"] == "bundled" and plan["review"] == install.BUNDLED_REVIEW
    assert plan["app"]["author"] == "The JimmyHQ project"
    install.cancel(core, plan["plan"])


def test_the_watering_examples_settings_reach_the_settings_screen(core):
    plan = install.plan_example(core, "watering")
    install.confirm(core, plan["plan"], plan["fingerprint"], typed_name="Watering")
    plugs.setup(core)
    assert core.settings.get("app.watering.ask_every_minutes") == 1440
