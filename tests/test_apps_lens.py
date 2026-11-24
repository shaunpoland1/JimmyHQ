"""The ``apps`` lens: what is installed, at which version, in what state, and what was
consented to and when. Folded from events alone."""

from __future__ import annotations

from jimmyhq.apps import store
from jimmyhq.apps.kinds import LIFECYCLE, subject
from jimmyhq.apps.lens import KEEP_CONSENTS, AppsLens

TREE1, TREE2 = "1" * 64, "2" * 64
MARK1, MARK2 = "a" * 64, "b" * 64
COMMIT = "3f2a9c1e5b7d4a6c8e0f1a2b3c4d5e6f70819203"
WATER = {
    "app_id": "water", "name": "Water", "version": "1.0.0", "tier": "sandboxed",
    "source": "git", "repo": "https://git.example.com/apps/water.git", "commit": COMMIT,
    "tree": TREE1, "permissions": ["tasks.write"], "fingerprint": MARK1, "reviewed": False,
}


def put(core, kind, payload, actor="owner"):
    return core.tape.print(kind, payload, actor=actor, subject=subject(payload["app_id"]))


def apps(core):
    return store.state(core)["apps"]


def test_the_lens_folds_every_lifecycle_kind_and_nothing_else():
    assert AppsLens.kinds == LIFECYCLE and AppsLens.name == "apps"
    assert AppsLens().initial() == {"apps": {}, "removed": {}}


def test_an_installed_app_is_listed_enabled_with_its_version_source_and_first_consent(core):
    event = put(core, "apps.installed", WATER)
    row = apps(core)["water"]
    assert {k: row[k] for k in WATER} == WATER
    assert row["enabled"] is True and row["disabled_reason"] is None
    assert row["installed_at"] == row["consented_at"] == event.ts
    assert row["installed_by"] == "owner" and row["updated_at"] is None
    assert row["consents"] == [{
        "at": event.ts, "by": "owner", "version": "1.0.0", "tier": "sandboxed",
        "permissions": ["tasks.write"], "added": ["tasks.write"], "removed": [],
        "fingerprint": MARK1}]


def test_switching_off_and_on_changes_only_the_state(core):
    put(core, "apps.installed", WATER)
    put(core, "apps.disabled", {"app_id": "water", "reason": "crashing"}, actor="system")
    row = apps(core)["water"]
    assert row["enabled"] is False and row["disabled_reason"] == "crashing"
    assert row["permissions"] == ["tasks.write"]
    put(core, "apps.enabled", {"app_id": "water"})
    row = apps(core)["water"]
    assert row["enabled"] is True and row["disabled_reason"] is None


def test_a_new_consent_replaces_what_is_granted_and_is_kept_with_its_time(core):
    first = put(core, "apps.installed", WATER)
    more = ["tasks.write", "net:api.example.com"]
    second = put(core, "apps.permissions_granted", {
        "app_id": "water", "version": "1.1.0", "tier": "sandboxed", "permissions": more,
        "added": ["net:api.example.com"], "removed": [], "fingerprint": MARK2})
    row = apps(core)["water"]
    assert row["permissions"] == more and row["fingerprint"] == MARK2
    assert row["consented_at"] == second.ts and row["installed_at"] == first.ts
    assert [c["added"] for c in row["consents"]] == [["tasks.write"], ["net:api.example.com"]]
    assert row["version"] == "1.0.0"


def test_only_the_most_recent_consents_are_kept(core):
    put(core, "apps.installed", WATER)
    for n in range(KEEP_CONSENTS + 5):
        put(core, "apps.permissions_granted", {
            "app_id": "water", "version": f"1.{n}.0", "tier": "sandboxed",
            "permissions": ["tasks.write"], "fingerprint": MARK1})
    consents = apps(core)["water"]["consents"]
    assert len(consents) == KEEP_CONSENTS and consents[-1]["version"] == "1.54.0"


def test_an_update_changes_the_version_and_code_and_keeps_the_install_time(core):
    first = put(core, "apps.installed", WATER)
    second = put(core, "apps.updated", WATER | {
        "version": "1.1.0", "tree": TREE2, "from_version": "1.0.0"})
    row = apps(core)["water"]
    assert (row["version"], row["tree"], row["previous_version"]) == ("1.1.0", TREE2, "1.0.0")
    assert row["installed_at"] == first.ts and row["updated_at"] == second.ts
    assert row["permissions"] == ["tasks.write"] and len(row["consents"]) == 1


def test_a_roll_back_restores_the_code_that_version_was_installed_with(core):
    put(core, "apps.installed", WATER)
    put(core, "apps.updated", WATER | {"version": "1.1.0", "tree": TREE2, "commit": None,
                                       "from_version": "1.0.0"})
    put(core, "apps.rolled_back", {"app_id": "water", "from_version": "1.1.0",
                                   "version": "1.0.0"})
    row = apps(core)["water"]
    assert (row["version"], row["tree"], row["commit"]) == ("1.0.0", TREE1, COMMIT)
    assert row["previous_version"] == "1.1.0"


def test_a_removed_app_leaves_the_list_and_is_remembered_as_removed(core):
    put(core, "apps.installed", WATER)
    gone = put(core, "apps.removed", {"app_id": "water", "version": "1.0.0", "data": "kept"})
    state = store.state(core)
    assert state["apps"] == {}
    assert state["removed"]["water"] == {
        "app_id": "water", "name": "Water", "version": "1.0.0", "tier": "sandboxed",
        "removed_at": gone.ts, "removed_by": "owner", "data": "kept", "tape_erased": False}
    again = put(core, "apps.installed", WATER | {"version": "2.0.0"})
    state = store.state(core)
    assert state["removed"] == {} and state["apps"]["water"]["version"] == "2.0.0"
    assert state["apps"]["water"]["installed_at"] == again.ts
    assert len(state["apps"]["water"]["consents"]) == 1


def test_events_about_an_app_that_is_not_installed_change_nothing(core):
    put(core, "apps.enabled", {"app_id": "ghost"})
    put(core, "apps.disabled", {"app_id": "ghost"})
    put(core, "apps.removed", {"app_id": "ghost", "version": "1.0.0"})
    put(core, "apps.rolled_back", {"app_id": "ghost", "from_version": "2.0.0",
                                   "version": "1.0.0"})
    put(core, "apps.fetched", {"app_id": "ghost", "host": "api.example.com", "size": 1})
    assert store.state(core) == {"apps": {}, "removed": {}}


def test_apps_are_kept_apart(core):
    put(core, "apps.installed", WATER)
    put(core, "apps.installed", WATER | {"app_id": "books_read", "name": "Books read",
                                         "permissions": []})
    put(core, "apps.disabled", {"app_id": "water"})
    assert store.is_enabled(core, "books_read") and not store.is_enabled(core, "water")
    assert store.granted(core, "books_read") == () and sorted(apps(core)) == [
        "books_read", "water"]


def test_what_is_granted_is_empty_for_an_app_that_is_off_or_not_installed(core):
    assert store.granted(core, "water") == ()
    put(core, "apps.installed", WATER)
    assert store.granted(core, "water") == ("tasks.write",)
    put(core, "apps.disabled", {"app_id": "water"})
    assert store.granted(core, "water") == ()


def test_the_fold_is_the_same_when_replayed_from_the_first_event(core):
    put(core, "apps.installed", WATER)
    put(core, "apps.disabled", {"app_id": "water"})
    live = store.state(core)

    class Again(AppsLens):
        name = "apps_again"

    assert core.lenses.read(Again()) == live
