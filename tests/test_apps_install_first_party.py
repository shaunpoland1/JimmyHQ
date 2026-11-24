"""A first-party app, one that ships in JimmyHQ's own ``apps/`` folder beside the examples,
comes with JimmyHQ exactly as an example does: by where it was copied from and by its
files, never by what its manifest says."""

from __future__ import annotations

import json
import shutil

import pytest

from apps_install_helpers import ROOT, manifest_of, write_manifest
from jimmyhq.apps import install, store, update

FIRST_PARTY = ROOT / "apps"
POSTS = FIRST_PARTY / "posts"


def copy_of(tmp_path, name: str = "posts", to: str = "posts"):
    dest = tmp_path / "src" / to
    shutil.copytree(FIRST_PARTY / name, dest, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    return dest


def confirm(core, plan) -> dict:
    typed = plan["app"]["name"] if plan["needs_typed_name"] else None
    return install.confirm(core, plan["plan"], plan["fingerprint"], typed_name=typed)


def test_the_first_party_folder_is_the_repositorys_apps_folder():
    assert store.first_party_dir() is not None
    assert store.first_party_dir().resolve() == FIRST_PARTY.resolve()


def test_a_first_party_app_planned_by_name_is_bundled(core):
    plan = install.plan_example(core, "posts")
    assert plan["provenance"] == "bundled"
    assert plan["review"] == install.BUNDLED_REVIEW
    assert plan["reviewed"] is False
    assert plan["source"]["example"] == "posts"


def test_the_first_party_folder_given_as_a_folder_is_bundled_too(core):
    assert install.plan_folder(core, POSTS)["provenance"] == "bundled"


def test_every_first_party_app_says_it_is_made_by_the_jimmyhq_project():
    for entry in install.examples():
        if entry["group"] == "first_party":
            assert manifest_of(FIRST_PARTY / entry["id"])["author"] == "The JimmyHQ project"


def test_the_shipped_apps_list_names_posts_as_first_party_and_the_examples_as_examples():
    groups = {e["id"]: e["group"] for e in install.examples()}
    assert groups["posts"] == "first_party"
    assert {groups[n] for n in ("water", "books_read", "chores", "drafts", "watering")} == {
        "example"}


def test_the_examples_folder_is_never_taken_for_an_app():
    assert "examples" not in {e["id"] for e in install.examples()}
    assert install.bundled_example(FIRST_PARTY / "examples") is None
    assert install.bundled_folder("examples") is None


def test_the_examples_folder_is_not_an_app_even_with_a_manifest_in_it(
        core, tmp_path, monkeypatch):
    base = tmp_path / "apps"
    shutil.copytree(FIRST_PARTY / "examples" / "water", base / "examples")
    write_manifest(base / "examples", id="examples")
    monkeypatch.setattr(store, "first_party_dir", lambda: base)
    assert install.bundled_folder("examples") is None
    assert install.bundled_example(base / "examples") is None
    with pytest.raises(store.AppError, match="keeps for itself"):
        install.plan_folder(core, base / "examples")


def test_a_byte_for_byte_copy_of_a_first_party_app_elsewhere_is_not_bundled(core, tmp_path):
    plan = install.plan_folder(core, copy_of(tmp_path))
    assert plan["provenance"] == "folder"
    assert plan["review"] == install.NOT_REVIEWED


def test_a_copy_with_one_file_added_and_jimmyhqs_name_on_it_gets_the_normal_warning(
        core, tmp_path):
    folder = copy_of(tmp_path)
    write_manifest(folder, author="The JimmyHQ project")
    (folder / "web" / "extra.js").write_text("// anything\n", encoding="utf-8")
    plan = install.plan_folder(core, folder)
    assert plan["provenance"] == "folder"
    assert plan["review"] == install.NOT_REVIEWED
    assert plan["needs_typed_name"] is True and "Runs as you" in plan["warning"]


def test_a_folder_named_apps_posts_outside_jimmyhq_is_not_bundled(core, tmp_path):
    fake = tmp_path / "jimmyhq" / "apps" / "_apps" / "posts"
    shutil.copytree(POSTS, fake, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    assert install.plan_folder(core, fake)["provenance"] == "folder"
    fake = tmp_path / "checkout" / "apps" / "posts"
    shutil.copytree(POSTS, fake, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    assert install.plan_folder(core, fake)["provenance"] == "folder"


def test_a_first_party_folder_whose_files_differ_from_what_ships_is_not_bundled(
        core, tmp_path, monkeypatch):
    base = tmp_path / "apps"
    copy_of(tmp_path, to="ignored")
    shutil.copytree(tmp_path / "src" / "ignored", base / "posts")
    monkeypatch.setattr(store, "first_party_dir", lambda: base)
    real = install.bundled_tree(core, "posts")
    assert real is not None
    assert install.provenance(core, base / "posts", "folder", real, "posts") == "bundled"
    assert install.provenance(core, base / "posts", "folder", "0" * 64, "posts") == "folder"
    assert install.provenance(core, base / "posts", "git", real, "posts") == "git"


def test_a_first_party_folder_whose_manifest_names_another_id_is_not_bundled(
        core, tmp_path, monkeypatch):
    base = tmp_path / "apps"
    shutil.copytree(FIRST_PARTY / "examples" / "water", base / "posts")
    data = json.loads((base / "posts" / "jimmyhq.app.json").read_text(encoding="utf-8"))
    (base / "posts" / "jimmyhq.app.json").write_text(json.dumps({**data, "id": "water"}),
                                                     encoding="utf-8")
    monkeypatch.setattr(store, "first_party_dir", lambda: base)
    monkeypatch.setattr(store, "examples_dir", lambda: tmp_path / "none")
    assert install.bundled_tree(core, "posts") is None
    assert install.plan_folder(core, base / "posts")["provenance"] == "folder"


@pytest.mark.parametrize("name", ["../posts", "posts/", "Posts", "..", "", "examples/water"])
def test_a_name_that_is_not_a_shipped_id_is_never_joined_onto_a_path(name):
    assert install.bundled_folder(name) is None


def test_the_tree_of_a_first_party_app_ignores_caches_left_by_its_tests(core, tmp_path,
                                                                         monkeypatch):
    base = tmp_path / "apps"
    copy_of(tmp_path, to="ignored")
    shutil.copytree(tmp_path / "src" / "ignored", base / "posts")
    monkeypatch.setattr(store, "first_party_dir", lambda: base)
    before = install.bundled_tree(core, "posts")
    (base / "posts" / "posts" / "__pycache__").mkdir(exist_ok=True)
    (base / "posts" / "posts" / "__pycache__" / "x.pyc").write_bytes(b"cache")
    (base / "posts" / ".pytest_cache").mkdir()
    (base / "posts" / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")
    assert install.bundled_tree(core, "posts") == before


def test_an_installed_first_party_app_reads_as_bundled(core):
    confirm(core, install.plan_example(core, "posts"))
    row = install.detail(core, "posts")
    assert row["provenance"] == "bundled" and row["review"] == install.BUNDLED_REVIEW
    assert row["author"] == "The JimmyHQ project"


def test_an_installed_first_party_app_is_updated_from_the_copy_that_ships(core):
    confirm(core, install.plan_example(core, "posts"))
    # Found among the shipped apps (not "nothing to update it from"), and the same
    # version is refused as not an update.
    with pytest.raises(store.AppError, match="not an update"):
        update.plan_example(core, "posts")
    assert update.newer_example(core, {**store.need(core, "posts"), "example": "posts"}) \
        is False


def test_the_apps_screen_offers_every_first_party_app_that_ships_and_names_none_itself():
    """The screen's list once was written by hand in the web app and named only Posts, so
    Reader and Articles could not be installed from the screen. Now the server lists the
    folders that are really in this copy, and the web app lists nothing of its own."""
    shipped = {e["id"] for e in install.examples() if e["group"] == install.FIRST_PARTY}
    on_disk = {p.parent.name for p in FIRST_PARTY.glob("*/jimmyhq.app.json")} - {"examples"}
    assert shipped == on_disk and shipped
    text = (ROOT / "web/src/modules/apps/lib/examples.ts").read_text(encoding="utf-8")
    assert "FIRST_PARTY: readonly" not in text
    for app_id in shipped:
        assert f'id: "{app_id}"' not in text, f"examples.ts names {app_id} by hand"


def _pyproject() -> dict:
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_release_packs_the_whole_apps_folder_where_the_server_looks_and_names_no_app():
    """A copy of the repository with fewer apps must pack and test the same way, so no app
    is named in pyproject.toml: the wheel takes the folder as it is, and the examples sit in
    it as they do in a checkout."""
    config = _pyproject()
    packed = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert packed["apps"] == "jimmyhq/apps/_apps"
    assert not [src for src in packed if src.startswith("apps/")]
    # What the server looks for in an installed copy, beside the package.
    import inspect

    assert '"_apps"' in inspect.getsource(store.first_party_dir)
    assert '"_apps/examples"' in inspect.getsource(store.examples_dir)
    names = {p.parent.name for p in (ROOT / "apps").glob("*/jimmyhq.app.json")}
    assert names and not any(f"apps/{n}" in str(config) for n in names)


def test_the_tests_of_every_app_in_the_checkout_are_collected_by_one_pattern():
    paths = _pyproject()["tool"]["pytest"]["ini_options"]["testpaths"]
    assert paths == ["tests", "apps/*/tests"]
    assert (ROOT / "apps" / "posts" / "tests").is_dir()
