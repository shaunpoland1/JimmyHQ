from __future__ import annotations

import json
from pathlib import Path

import pytest

from jimmyhq import settings as s

PROFILES = Path(__file__).resolve().parents[1] / "profiles"
FILES = sorted(PROFILES.glob("*.json"))


def test_the_example_profiles_are_there():
    assert [f.name for f in FILES] == [
        "hands-off-agents.json", "private-by-default.json", "quiet.json"]
    assert (PROFILES / "README.md").is_file()


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_shipped_profile_is_valid_and_changes_something(core, path):
    document = json.loads(path.read_text(encoding="utf-8"))
    plan = core.settings.plan(document)
    assert [(e.key, e.reason) for e in plan.refused] == []
    assert plan.changes, "a profile that changes nothing from the defaults shows nothing"
    assert plan.name and plan.description


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_a_shipped_profile_holds_only_settings_that_can_be_shared(path):
    keys = json.loads(path.read_text(encoding="utf-8"))["settings"].keys()
    personal = {x.key for x in s.declared() if not x.portable}
    assert not personal & keys


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_a_shipped_profile_applies_cleanly_and_can_be_undone(core, path):
    document = json.loads(path.read_text(encoding="utf-8"))
    before = {k: core.settings.get(k) for k in document["settings"]}
    plan = core.settings.apply(document, actor="owner")
    assert plan.applied
    for key, value in document["settings"].items():
        assert core.settings.get(key) == value
    core.settings.apply(plan.undo_document(), actor="owner")
    assert {k: core.settings.get(k) for k in document["settings"]} == before
