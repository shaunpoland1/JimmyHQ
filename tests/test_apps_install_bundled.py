"""Where an app comes from: an example that ships inside JimmyHQ is "bundled", and nothing
else can pass for one, whatever its manifest says about itself."""

from __future__ import annotations

import json
import shutil

import pytest

from apps_install_helpers import (
    EXAMPLES,
    URL,
    example,
    make_repo,
    manifest_of,
    use_local,
    write_manifest,
)
from jimmyhq.apps import install, store

ALL = ("books_read", "chores", "water", "drafts", "watering")


def confirm(core, plan) -> dict:
    typed = plan["app"]["name"] if plan["needs_typed_name"] else None
    return install.confirm(core, plan["plan"], plan["fingerprint"], typed_name=typed)


@pytest.mark.parametrize("name", ALL)
def test_an_example_planned_from_the_copy_inside_jimmyhq_is_bundled(core, name):
    plan = install.plan_example(core, name)
    assert plan["provenance"] == "bundled"
    assert plan["review"] == install.BUNDLED_REVIEW
    assert plan["reviewed"] is False  # bundled is not the same as listed in a review index


def test_the_bundled_examples_folder_given_as_a_folder_is_bundled_too(core):
    plan = install.plan_folder(core, EXAMPLES / "water")
    assert plan["provenance"] == "bundled"


@pytest.mark.parametrize("name", ALL)
def test_every_bundled_example_says_who_made_it(name):
    assert manifest_of(EXAMPLES / name)["author"] == "The JimmyHQ project"


def test_a_byte_for_byte_copy_of_an_example_in_another_folder_is_not_bundled(core, tmp_path):
    plan = install.plan_folder(core, example(tmp_path, "water"))
    assert plan["provenance"] == "folder"
    assert plan["review"] == install.NOT_REVIEWED


def test_an_app_that_claims_to_be_jimmyhqs_own_example_still_gets_the_normal_warning(
        core, tmp_path):
    # The attacker copies the real example, keeps its id, name and author, and adds a file.
    folder = example(tmp_path, "drafts")
    write_manifest(folder, author="The JimmyHQ project", name="Drafts", id="drafts")
    (folder / "web" / "extra.js").write_text("// anything\n", encoding="utf-8")
    plan = install.plan_folder(core, folder)
    assert plan["provenance"] == "folder"
    assert plan["review"] == install.NOT_REVIEWED
    assert plan["needs_typed_name"] is True and "Runs as you" in plan["warning"]


def test_a_folder_named_like_an_example_outside_jimmyhq_is_not_bundled(core, tmp_path):
    fake = tmp_path / "jimmyhq" / "apps" / "_apps" / "examples" / "water"
    shutil.copytree(EXAMPLES / "water", fake)
    assert install.plan_folder(core, fake)["provenance"] == "folder"


def test_an_app_from_a_url_is_never_bundled_even_with_jimmyhqs_exact_files(
        core, tmp_path, monkeypatch):
    repo = example(tmp_path, "water")
    commit = make_repo(repo)
    use_local(monkeypatch, repo)
    plan = install.plan_git(core, URL, commit=commit)
    assert plan["provenance"] == "git" and plan["review"] == install.NOT_REVIEWED


def test_an_example_folder_whose_files_differ_from_what_ships_is_not_bundled(
        core, tmp_path, monkeypatch):
    # Stand in a folder of examples, then check the tree against a different one.
    base = tmp_path / "examples"
    shutil.copytree(EXAMPLES / "water", base / "water")
    monkeypatch.setattr(store, "examples_dir", lambda: base)
    real = install.bundled_tree(core, "water")
    assert install.provenance(core, base / "water", "folder", real, "water") == "bundled"
    assert install.provenance(core, base / "water", "folder", "0" * 64, "water") == "folder"
    assert install.provenance(core, base / "water", "folder", real, "chores") == "folder"
    assert install.provenance(core, base / "water", "git", real, "water") == "git"


def test_an_example_folder_whose_manifest_names_another_id_is_not_bundled(
        core, tmp_path, monkeypatch):
    base = tmp_path / "examples"
    shutil.copytree(EXAMPLES / "water", base / "water")
    data = json.loads((base / "water" / "jimmyhq.app.json").read_text(encoding="utf-8"))
    (base / "water" / "jimmyhq.app.json").write_text(
        json.dumps({**data, "id": "chores"}), encoding="utf-8")
    monkeypatch.setattr(store, "examples_dir", lambda: base)
    assert install.bundled_tree(core, "water") is None
    assert install.plan_folder(core, base / "water")["provenance"] == "folder"


def test_an_installed_example_reads_as_bundled_and_a_changed_copy_does_not(core, tmp_path):
    confirm(core, install.plan_example(core, "water"))
    row = install.detail(core, "water")
    assert row["provenance"] == "bundled" and row["review"] == install.BUNDLED_REVIEW
    assert row["author"] == "The JimmyHQ project"

    folder = example(tmp_path, "books_read")
    (folder / "notes.txt").write_text("changed\n", encoding="utf-8")
    confirm(core, install.plan_folder(core, folder))
    row = install.detail(core, "books_read")
    assert row["provenance"] == "folder" and row["review"] == install.NOT_REVIEWED
