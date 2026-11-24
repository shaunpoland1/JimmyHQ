"""Installing from a folder, the two-step consent, and the rest of an app's life."""

from __future__ import annotations

import json
import os

import pytest

from apps_install_helpers import (
    EXAMPLES,
    events,
    example,
    expected_tree,
    install_folder,
    manifest_of,
    staging,
    write_manifest,
)
from jimmyhq.apps import install, permissions, store
from jimmyhq.apps.store import AppError

ALL = ("books_read", "water", "drafts")


def refused(fn, *args, **kwargs) -> AppError:
    with pytest.raises(AppError) as caught:
        fn(*args, **kwargs)
    return caught.value


def link(target, at):
    try:
        os.symlink(target, at)
    except (OSError, NotImplementedError):
        pytest.skip("this system cannot make a link here")


# --- the ordinary flow ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ALL)
def test_a_plan_installs_nothing_and_shows_what_the_owner_must_read(core, tmp_path, name):
    folder = example(tmp_path, name)
    plan = install.plan_folder(core, folder)
    m = manifest_of(folder)
    assert plan["app"]["id"] == name and plan["app"]["version"] == m["version"]
    assert plan["source"]["kind"] == "folder" and plan["source"]["repo"] is None
    assert plan["source"]["tree"] == expected_tree(folder)
    assert plan["reviewed"] is False and plan["review"].startswith("Not reviewed.")
    sentences = [line["sentence"] for line in plan["permissions"]]
    assert "Keep its own data on this computer." in sentences
    assert len(plan["fingerprint"]) == 64
    full = m["tier"] == "full"
    assert plan["needs_typed_name"] is full
    assert (plan["warning"] is not None) is full
    if full:
        assert plan["permissions"][0]["strong"] and "Runs as you" in plan["warning"]
    assert store.installed(core) == {} and events(core) == []
    assert not (core.config.data / "apps" / name).exists()


@pytest.mark.parametrize("name", ALL)
def test_confirming_a_plan_installs_exactly_those_files_and_records_it(core, tmp_path, name):
    folder = example(tmp_path, name)
    plan = install.plan_folder(core, folder)
    done = install.confirm(core, plan["plan"], plan["fingerprint"],
                           typed_name=plan["app"]["name"], actor="owner")
    m = manifest_of(folder)
    assert done["id"] == name and done["enabled"] is True
    code = store.code_dir(core, name)
    assert code.name == f"{m['version']}-{plan['source']['tree'][:8]}"
    assert expected_tree(code) == plan["source"]["tree"]
    [event] = events(core)
    assert event.kind == "apps.installed" and event.actor == "owner"
    assert event.subject == f"app:{name}"
    assert event.payload == {
        "app_id": name, "name": m["name"], "version": m["version"], "tier": m["tier"],
        "source": "folder", "repo": None, "commit": None, "tree": plan["source"]["tree"],
        "permissions": m["permissions"], "fingerprint": plan["fingerprint"],
        "reviewed": False, "ref": None, "example": None}
    assert store.granted(core, name) == tuple(m["permissions"])
    assert store.manifest_of(core, name).id == name
    assert staging(core) == []


def test_the_tree_hash_is_the_sha256_of_each_path_with_its_files_sha256_sorted():
    import hashlib
    a, b = hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()
    by_hand = hashlib.sha256(f"{b}  a/z.txt\n{a}  b.txt\n".encode()).hexdigest()
    assert store.tree_hash([("b.txt", a), ("a/z.txt", b)]) == by_hand


def test_dot_files_and_python_caches_are_left_out_of_what_is_installed(core, tmp_path):
    folder = example(tmp_path, "water")
    clean = expected_tree(folder)
    (folder / ".git").mkdir()
    (folder / ".git" / "config").write_text("x")
    (folder / ".env").write_text("x")
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "a.pyc").write_bytes(b"x")
    plan = install.plan_folder(core, folder)
    assert plan["source"]["tree"] == clean and plan["source"]["left_out"] == 3
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert sorted(p.name for p in store.code_dir(core, "water").iterdir()) == [
        "app.js", "index.html", "jimmyhq.app.json"]


def test_a_full_app_is_not_installed_until_its_name_is_typed_exactly(core, tmp_path):
    plan = install.plan_folder(core, example(tmp_path, "drafts"))
    for typed in (None, "", "drafts", "Draft"):
        error = refused(install.confirm, core, plan["plan"], plan["fingerprint"],
                        typed_name=typed)
        assert error.status == 422 and "Nothing was installed" in error.detail
    assert store.installed(core) == {}
    install.confirm(core, plan["plan"], plan["fingerprint"], typed_name="Drafts")
    assert store.get(core, "drafts")["tier"] == "full"


def test_a_sandboxed_app_needs_no_typed_name(core, tmp_path):
    plan = install.plan_folder(core, example(tmp_path, "water"))
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert store.is_enabled(core, "water")


def test_an_app_that_is_already_installed_is_refused_at_the_plan_and_at_the_confirm(
        core, tmp_path):
    folder = example(tmp_path, "water")
    waiting = install.plan_folder(core, folder)
    install_folder(core, folder)
    assert refused(install.plan_folder, core, folder).status == 409
    error = refused(install.confirm, core, waiting["plan"], waiting["fingerprint"])
    assert error.status == 409 and "already have Water 1.0.0" in error.detail
    assert len(events(core, "apps.installed")) == 1


def test_an_app_whose_computed_field_cannot_be_worked_out_is_refused(core, tmp_path):
    folder = example(tmp_path, "books_read")
    data = manifest_of(folder)
    table = next(iter(data["tables"]))
    data["tables"][table]["fields"]["odd"] = {"type": "computed", "formula": "pages ** 2"}
    write_manifest(folder, tables=data["tables"])
    error = refused(install.plan_folder, core, folder)
    assert error.status == 422 and "Nothing was installed" in error.detail
    assert staging(core) == [] and events(core) == []


def test_an_app_whose_tool_would_clash_with_an_installed_apps_tool_is_refused(core, tmp_path):
    tool = {"description": "Look.", "params": {}, "required": [], "effect": "read"}
    perms = ["bench.file", "model.ask", "jobs", "agents.tools"]
    one = example(tmp_path, "drafts", "one")
    write_manifest(one, id="ab_cd", permissions=perms, tools=[{"name": "ef", **tool}])
    two = example(tmp_path, "drafts", "two")
    write_manifest(two, id="ab", permissions=perms, tools=[{"name": "cd_ef", **tool}])
    install_folder(core, one)
    error = refused(install.plan_folder, core, two)
    assert error.status == 422 and "app_ab_cd_ef" in error.detail


def test_a_folder_without_a_manifest_or_with_a_broken_one_is_refused_with_its_sentences(
        core, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "not a JimmyHQ app" in refused(install.plan_folder, core, empty).detail
    assert refused(install.plan_folder, core, tmp_path / "nowhere").status == 422
    folder = example(tmp_path, "water")
    write_manifest(folder, tier="mighty", surprise=1)
    error = refused(install.plan_folder, core, folder)
    assert "tier" in error.detail and "surprise" in error.detail
    assert staging(core) == []


# --- from the attacker's side ---------------------------------------------------------------


def test_a_link_out_of_the_folder_is_refused_and_nothing_is_staged(core, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("the owner's own file")
    folder = example(tmp_path, "water")
    link(secret, folder / "notes.txt")
    error = refused(install.plan_folder, core, folder)
    assert "\"notes.txt\" is a link" in error.detail
    assert "nothing was installed" in error.detail
    assert staging(core) == []


def test_a_linked_folder_inside_the_app_is_refused_too(core, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("x")
    folder = example(tmp_path, "water")
    link(outside, folder / "lib")
    assert "is a link" in refused(install.plan_folder, core, folder).detail


def test_a_link_given_as_the_folder_itself_is_refused(core, tmp_path):
    folder = example(tmp_path, "water")
    link(folder, tmp_path / "alias")
    assert refused(install.plan_folder, core, tmp_path / "alias").status == 422


def test_a_screen_entry_that_climbs_out_with_dot_dot_is_refused(core, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "evil.html").write_text("<p>")
    folder = example(tmp_path, "water")
    for entry in ("../evil.html", "a/../../evil.html", "/etc/hosts.html", "..\\evil.html"):
        write_manifest(folder, screens=[{"id": "main", "label": "Water", "entry": entry}])
        assert refused(install.plan_folder, core, folder).status == 422
    assert staging(core) == []


@pytest.mark.parametrize("path", [
    "../evil", "a/../../evil", "/etc/passwd", "C:/Windows/x", "a\\b", "a//b", "con.txt",
    "ok/NUL", "a/./b", "trailing.", "sp ace", "",
])
def test_a_file_path_that_could_leave_the_app_on_any_system_is_refused(path):
    with pytest.raises(AppError):
        store.check_tree_path(path)


def test_nothing_can_be_written_outside_the_folder_it_is_meant_for(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(AppError):
        store.write_file(root, "../escaped.txt", b"x")
    assert not (tmp_path / "escaped.txt").exists()


def test_two_names_that_differ_only_by_capitals_are_refused():
    seen: dict[str, str] = {}
    store.check_case(seen, "lib/a.js")
    store.check_case(seen, "lib/b.js")
    with pytest.raises(AppError):
        store.check_case(seen, "Lib/c.js")
    with pytest.raises(AppError):
        store.check_case(seen, "lib/A.js")


def test_changing_the_source_folder_after_the_plan_changes_nothing_that_is_installed(
        core, tmp_path):
    folder = example(tmp_path, "water")
    plan = install.plan_folder(core, folder)
    write_manifest(folder, permissions=["tasks.write", "model.ask", "net:api.example.com"])
    (folder / "app.js").write_text("// something else entirely")
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert store.granted(core, "water") == ("tasks.write",)
    code = store.code_dir(core, "water")
    assert (code / "app.js").read_bytes() == (EXAMPLES / "water" / "app.js").read_bytes()
    assert manifest_of(code)["permissions"] == ["tasks.write"]


def test_a_manifest_changed_in_staging_between_plan_and_install_is_refused(core, tmp_path):
    plan = install.plan_folder(core, example(tmp_path, "water"))
    [staged] = staging(core)
    write_manifest(staged / "tree", permissions=["tasks.write", "model.ask"])
    error = refused(install.confirm, core, plan["plan"], plan["fingerprint"])
    assert error.status == 409 and "not what you were shown" in error.detail
    assert "Nothing was installed" in error.detail
    assert store.installed(core) == {} and events(core) == [] and staging(core) == []
    assert not (core.config.data / "apps" / "water").exists()


def test_a_file_changed_or_added_in_staging_after_the_plan_is_refused(core, tmp_path):
    for change in ("edit", "add", "hidden", "link"):
        plan = install.plan_folder(core, example(tmp_path, "water", f"w_{change}"))
        tree = staging(core)[0] / "tree"
        if change == "edit":
            (tree / "app.js").write_text("fetch('https://collector.example.com')")
        elif change == "add":
            (tree / "extra.js").write_text("x")
        elif change == "hidden":
            (tree / ".hidden").write_text("x")
        else:
            (tmp_path / f"t_{change}").write_text("x")
            link(tmp_path / f"t_{change}", tree / "extra.js")
        assert refused(install.confirm, core, plan["plan"], plan["fingerprint"]).status == 409
        assert store.installed(core) == {} and staging(core) == []


def test_a_fingerprint_from_one_apps_plan_does_not_install_another_app(core, tmp_path):
    water = install.plan_folder(core, example(tmp_path, "water"))
    books = install.plan_folder(core, example(tmp_path, "books_read"))
    assert water["fingerprint"] != books["fingerprint"]
    error = refused(install.confirm, core, water["plan"], books["fingerprint"])
    assert error.status == 409 and "not the consent for this app" in error.detail
    assert refused(install.confirm, core, books["plan"], water["fingerprint"]).status == 409
    for wrong in ("", "0" * 64, water["fingerprint"][:-1], None, "é" * 64):
        assert refused(install.confirm, core, water["plan"], wrong).status == 409
    assert store.installed(core) == {} and events(core) == []


def test_consent_to_fewer_permissions_does_not_install_a_plan_that_asks_for_more(
        core, tmp_path):
    mild = install.plan_folder(core, example(tmp_path, "water", "mild"))
    greedy = example(tmp_path, "water", "greedy")
    write_manifest(greedy, permissions=["tasks.write", "tasks.read", "model.ask"])
    asked = install.plan_folder(core, greedy)
    assert asked["fingerprint"] != mild["fingerprint"]
    assert refused(install.confirm, core, asked["plan"], mild["fingerprint"]).status == 409
    assert store.installed(core) == {}


def test_a_used_plan_cannot_be_used_again(core, tmp_path):
    plan = install.plan_folder(core, example(tmp_path, "water"))
    install.confirm(core, plan["plan"], plan["fingerprint"])
    install.remove(core, "water")
    assert refused(install.confirm, core, plan["plan"], plan["fingerprint"]).status == 404
    assert store.installed(core) == {}


@pytest.mark.parametrize("plan_id", [
    "..", "../..", "../../tape.db", "water", "", "a/b", "0" * 25, "0" * 27,
    "01HZZZZZZZZZZZZZZZZZZZZZZ/", "01hzzzzzzzzzzzzzzzzzzzzzzz", None, 7,
])
def test_a_plan_id_that_is_not_a_plan_id_is_refused_and_can_never_be_a_path(
        core, tmp_path, plan_id):
    install.plan_folder(core, example(tmp_path, "water"))
    for fn in (lambda: install.confirm(core, plan_id, "0" * 64),
               lambda: install.cancel(core, plan_id)):
        assert refused(fn).status == 404
    assert len(staging(core)) == 1 and (core.config.data / "tape.db").exists()


def test_a_plan_that_waited_too_long_is_thrown_away_and_refused(core, tmp_path):
    plan = install.plan_folder(core, example(tmp_path, "water"))
    record = staging(core)[0] / "plan.json"
    data = json.loads(record.read_text())
    record.write_text(json.dumps(data | {"expires_at": 1}))
    error = refused(install.confirm, core, plan["plan"], plan["fingerprint"])
    assert error.status == 410 and "Start the install again" in error.detail
    assert staging(core) == [] and store.installed(core) == {}


def test_making_a_new_plan_sweeps_away_the_ones_that_ran_out(core, tmp_path):
    install.plan_folder(core, example(tmp_path, "water"))
    record = staging(core)[0] / "plan.json"
    record.write_text(json.dumps(json.loads(record.read_text()) | {"expires_at": 1}))
    fresh = install.plan_folder(core, example(tmp_path, "books_read"))
    assert [p.name for p in staging(core)] == [fresh["plan"]]


def test_cancelling_a_plan_throws_the_fetched_app_away(core, tmp_path):
    plan = install.plan_folder(core, example(tmp_path, "water"))
    assert install.cancel(core, plan["plan"]) is True
    assert staging(core) == [] and install.cancel(core, plan["plan"]) is False
    assert refused(install.confirm, core, plan["plan"], plan["fingerprint"]).status == 404


def test_a_file_larger_than_the_owner_allows_is_refused(core, tmp_path):
    core.settings.set("apps.max_file_mb", 1, actor="owner")
    folder = example(tmp_path, "water")
    (folder / "big.bin").write_bytes(b"\0" * (store.MB + 1))
    error = refused(install.plan_folder, core, folder)
    assert error.status == 413 and "big.bin" in error.detail
    assert "Nothing was installed" in error.detail and staging(core) == []


def test_a_tree_larger_than_the_owner_allows_is_refused(core, tmp_path):
    core.settings.set("apps.max_tree_mb", 1, actor="owner")
    folder = example(tmp_path, "water")
    for n in range(3):
        (folder / f"part{n}.bin").write_bytes(b"\0" * (store.MB // 2))
    assert refused(install.plan_folder, core, folder).status == 413
    assert staging(core) == []


def test_a_tree_with_more_files_than_the_owner_allows_is_refused(core, tmp_path):
    core.settings.set("apps.max_files", 2, actor="owner")
    error = refused(install.plan_folder, core, example(tmp_path, "water"))
    assert error.status == 413 and "more than 2 files" in error.detail


def test_a_folder_nested_too_deep_is_refused(core, tmp_path):
    folder = example(tmp_path, "water")
    deep = folder.joinpath(*["d"] * store.MAX_DEPTH)
    deep.mkdir(parents=True)
    (deep / "x.txt").write_text("x")
    assert "folders deep" in refused(install.plan_folder, core, folder).detail


# --- switching and removing -----------------------------------------------------------------


def test_disabling_and_enabling_are_recorded_once_each_and_take_the_grants_away(
        core, tmp_path):
    install_folder(core, example(tmp_path, "water"))
    assert install.enable(core, "water")["enabled"] is True
    assert events(core, "apps.enabled") == []
    assert install.disable(core, "water", actor="owner")["enabled"] is False
    install.disable(core, "water")
    assert len(events(core, "apps.disabled")) == 1
    assert store.granted(core, "water") == () and not store.is_enabled(core, "water")
    install.enable(core, "water")
    assert store.granted(core, "water") == ("tasks.write",)
    assert [e.kind for e in events(core)] == [
        "apps.installed", "apps.disabled", "apps.enabled"]


def test_removing_keeps_the_apps_data_by_default_and_deletes_its_code_and_secrets(
        core, tmp_path):
    install_folder(core, example(tmp_path, "water"))
    data = store.data_dir(core, "water")
    data.mkdir(parents=True)
    (data / "app.db").write_bytes(b"kept")
    core.secrets.set("app.water.key", "v")
    core.secrets.set("app.waterfall.key", "other")
    done = install.remove(core, "water")
    assert done == {"id": "water", "name": "Water", "version": "1.0.0", "data": "kept",
                    "erased_events": 0}
    assert (data / "app.db").read_bytes() == b"kept"
    assert not (core.config.data / "apps" / "water" / "versions").exists()
    assert core.secrets.names() == ["app.waterfall.key"]
    [event] = events(core, "apps.removed")
    assert event.payload == {"app_id": "water", "version": "1.0.0", "data": "kept",
                             "tape_erased": False}
    assert store.get(core, "water") is None and store.code_dir(core, "water") is None
    assert store.state(core)["removed"]["water"]["data"] == "kept"


def test_removing_deletes_the_data_only_when_the_owner_says_so_or_has_set_it_so(
        core, tmp_path):
    for keep, setting in ((False, True), (None, False)):
        install_folder(core, example(tmp_path, "water", f"w{keep}"))
        data = store.data_dir(core, "water")
        data.mkdir(parents=True, exist_ok=True)
        (data / "app.db").write_bytes(b"x")
        core.settings.set("apps.remove_keeps_data", setting, actor="owner")
        assert install.remove(core, "water", keep_data=keep)["data"] == "deleted"
        assert not (core.config.data / "apps" / "water").exists()
    assert [e.payload["data"] for e in events(core, "apps.removed")] == ["deleted"] * 2


def test_an_app_can_be_installed_again_after_it_was_removed_and_finds_its_data(
        core, tmp_path):
    folder = example(tmp_path, "water")
    install_folder(core, folder)
    data = store.data_dir(core, "water")
    data.mkdir(parents=True)
    (data / "app.db").write_bytes(b"mine")
    install.remove(core, "water")
    install_folder(core, folder)
    assert (data / "app.db").read_bytes() == b"mine"
    assert store.is_enabled(core, "water") and "water" not in store.state(core)["removed"]


@pytest.mark.parametrize("app_id", ["water", "../water", "_staging", "", "Water"])
def test_an_app_that_is_not_installed_cannot_be_switched_removed_or_read(core, app_id):
    for fn in (install.enable, install.disable, install.remove, install.detail):
        error = refused(fn, core, app_id)
        assert error.status == 404 and "Nothing was changed" in error.detail
    assert events(core) == []


def test_the_detail_shows_what_was_consented_to_in_the_dialogs_sentences_and_when(
        core, tmp_path):
    install_folder(core, example(tmp_path, "water"))
    got = install.detail(core, "water")
    assert [line["id"] for line in got["lines"]] == ["tasks.write", permissions.OWN_DATA]
    assert got["lines"][0]["sentence"] == "Add tasks, and mark the ones it added as done."
    [consent] = got["consents"]
    assert consent["by"] == "owner" and consent["permissions"] == ["tasks.write"]
    assert consent["at"] == got["consented_at"] == got["installed_at"] > 1_600_000_000_000
    assert got["adds"]["screens"] == ["Water"] and got["adds"]["settings"] == ["Glasses a day"]
    # Byte for byte the water that ships with JimmyHQ, so it reads as JimmyHQ's own.
    assert got["storage_bytes"] == 0 and got["review"] == install.BUNDLED_REVIEW


def test_an_installed_app_whose_files_went_missing_is_listed_as_broken_not_hidden(
        core, tmp_path):
    install_folder(core, example(tmp_path, "water"))
    code = store.code_dir(core, "water")
    store.service(core).manifests.clear()
    store.delete_tree(code)
    [row] = install.listing(core)
    assert row["id"] == "water" and "missing or damaged" in row["broken"]
    [item] = store.shell_list(core)
    assert item["screens"] == [] and item["broken"]
    assert install.remove(core, "water")["data"] == "kept"
