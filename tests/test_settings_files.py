from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from jimmyhq import config
from jimmyhq import settings as s
from jimmyhq.core import open_core
from jimmyhq.settings import SettingsFileError
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"


def doc(settings: dict, **extra) -> dict:
    return {"jimmyhq_settings": 1, **extra, "settings": settings}


def changes(core) -> list:
    return core.tape.after(0, kind="setting.updated")


# --- the format --------------------------------------------------------------------


def test_exporting_everything_and_importing_it_back_changes_nothing(core):
    core.settings.set("appearance.theme", "dark", actor="owner")
    core.settings.set("owner.name", "Sam", actor="owner")
    core.settings.set("tasks.areas", ["home", "garden"], actor="owner")
    before = core.tape.last_seq()
    exported = core.settings.export(portable_only=False, changed_only=False)
    plan = core.settings.apply(json.loads(json.dumps(exported)), actor="owner")
    assert plan.applied and plan.changes == [] and plan.refused == []
    assert core.tape.last_seq() == before


def test_a_backup_restores_every_setting_on_a_fresh_install(core, tmp_path_factory):
    core.settings.set("appearance.theme", "dark", actor="owner")
    core.settings.set("owner.name", "Sam", actor="owner")
    core.settings.set("access.session_days", 7, actor="owner")
    backup = json.loads(json.dumps(core.settings.export(portable_only=False, changed_only=True)))
    other = open_core(config.Config(data=tmp_path_factory.mktemp("fresh"), host="127.0.0.1",
                                    port=0))
    try:
        other.settings.apply(backup, actor="owner")
        assert other.settings.get("appearance.theme") == "dark"
        assert other.settings.get("owner.name") == "Sam"
        assert other.settings.get("access.session_days") == 7
    finally:
        other.close()


def test_exporting_only_what_changed_leaves_out_what_is_at_its_default(core):
    core.settings.set("appearance.theme", "dark", actor="owner")
    core.settings.set("statusbar.show", True, actor="owner")  # the default, so no change
    exported = core.settings.export(portable_only=False, changed_only=True)
    assert exported["settings"] == {"appearance.theme": "dark"}
    assert exported["jimmyhq_settings"] == 1


def test_a_profile_leaves_out_what_is_personal_and_a_full_export_keeps_it(core):
    core.settings.set("owner.name", "Sam", actor="owner")
    core.settings.set("owner.timezone", "Europe/Paris", actor="owner")
    core.settings.set("access.allowed_hosts", ["box"], actor="owner")
    core.settings.set("appearance.theme", "dark", actor="owner")
    profile = core.settings.export(portable_only=True, changed_only=False)["settings"]
    everything = core.settings.export(portable_only=False, changed_only=False)["settings"]
    personal = {x.key for x in s.declared() if not x.portable}
    assert {"owner.name", "owner.timezone", "access.allowed_hosts"} <= personal
    assert not personal & profile.keys()
    assert personal <= everything.keys()
    assert profile["appearance.theme"] == "dark"
    assert "Sam" not in json.dumps(profile)


def test_a_settings_file_never_holds_anything_from_the_secret_store(core):
    core.secrets.set("provider-key", "sk-live-should-never-travel")
    for portable in (True, False):
        text = json.dumps(core.settings.export(portable_only=portable, changed_only=False))
        assert "sk-live-should-never-travel" not in text
        assert "provider-key" not in text


def test_no_declared_setting_is_named_like_a_credential():
    from jimmyhq.settings import _SECRET_WORDS

    assert [x.key for x in s.declared() if _SECRET_WORDS.search(x.key)] == []


def test_a_name_and_description_make_a_profile_and_are_read_back(core):
    exported = core.settings.export(portable_only=True, changed_only=True, name="Calm",
                                    description="Fewer nudges.")
    assert (exported["name"], exported["description"]) == ("Calm", "Fewer nudges.")
    plan = core.settings.plan(exported)
    assert (plan.name, plan.description) == ("Calm", "Fewer nudges.")


# --- what a file is refused for ----------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "words"),
    [
        (("appearance.theme", "purple"), ["Theme must be one of"]),
        (("access.session_days", 0), ["cannot be less than 1"]),
        (("access.session_days", 9999), ["cannot be more than 365"]),
        (("access.session_days", "30"), ["must be a number"]),
        (("statusbar.show", "yes"), ["on or off"]),
        (("appearance.sidebar_order", ["bench", "bench"]), ["same item twice"]),
        (("owner.timezone", "Mars/Olympus"), ["Time zone is not a name"]),
    ],
)
def test_a_wrong_or_out_of_range_value_is_refused_with_a_sentence_that_says_why(
    core, entry, words
):
    key, value = entry
    plan = core.settings.plan(doc({key: value}))
    [refused] = plan.refused
    assert refused.key == key and plan.changes == []
    assert all(w in refused.reason for w in words)


def test_a_name_that_is_not_a_setting_is_refused_and_says_where_it_may_belong(core):
    plan = core.settings.plan(doc({
        "app.weather.units": "metric", "appearance.colour": "red", "zzz.thing": 1,
    }))
    reasons = {e.key: e.reason for e in plan.refused}
    assert "app 'weather'" in reasons["app.weather.units"]
    assert "not installed" in reasons["app.weather.units"]
    assert "skipped" in reasons["app.weather.units"]
    assert "no setting by this name" in reasons["appearance.colour"]
    assert "module or app that is not installed" in reasons["zzz.thing"]


def test_an_entry_that_looks_like_a_secret_is_refused_and_its_value_is_not_echoed(core):
    plan = core.settings.plan(doc({"models.openai_api_key": "sk-secret-value"}))
    [refused] = plan.refused
    assert "never kept in a settings file" in refused.reason
    assert "sk-secret-value" not in json.dumps(plan.to_dict())


def test_a_setting_that_is_not_portable_is_still_accepted_on_import(core):
    plan = core.settings.apply(doc({"owner.name": "Sam"}), actor="owner")
    assert plan.applied and core.settings.get("owner.name") == "Sam"
    assert plan.changes[0].portable is False


@pytest.mark.parametrize(
    ("document", "words"),
    [
        ([1, 2], "not a JimmyHQ settings file"),
        ({"settings": {}}, "not a JimmyHQ settings file"),
        ({"jimmyhq_settings": "one", "settings": {}}, "whole number"),
        ({"jimmyhq_settings": 2, "settings": {}}, "newer than this version"),
        ({"jimmyhq_settings": 1}, 'no "settings" section'),
        ({"jimmyhq_settings": 1, "settings": [1]}, 'no "settings" section'),
        ({"jimmyhq_settings": 1, "name": 5, "settings": {}}, '"name" must be text'),
    ],
)
def test_a_file_that_cannot_be_used_at_all_says_what_is_wrong(core, document, words):
    with pytest.raises(SettingsFileError, match=words):
        core.settings.plan(document)
    with pytest.raises(SettingsFileError, match=words):
        core.settings.apply(document, actor="owner")
    assert changes(core) == []


def test_a_file_from_a_newer_format_says_to_update_jimmyhq(core):
    with pytest.raises(SettingsFileError, match="Update JimmyHQ"):
        core.settings.plan({"jimmyhq_settings": 99, "settings": {}})


# --- applying ----------------------------------------------------------------------


def test_planning_shows_old_and_new_and_changes_nothing(core):
    core.settings.set("access.session_days", 7, actor="owner")
    before = core.tape.last_seq()
    plan = core.settings.plan(doc({"access.session_days": 14, "statusbar.show": True}))
    [change] = plan.changes
    assert (change.key, change.label, change.old, change.new) == (
        "access.session_days", "Stay signed in for (days)", 7, 14)
    assert [e.key for e in plan.unchanged] == ["statusbar.show"]
    assert plan.applied is False and core.tape.last_seq() == before


def test_a_file_with_one_bad_entry_changes_nothing_unless_told_to_skip_it(core):
    document = doc({"appearance.theme": "dark", "access.session_days": 0})
    plan = core.settings.apply(document, actor="owner")
    assert plan.applied is False and [e.key for e in plan.refused] == ["access.session_days"]
    assert core.settings.get("appearance.theme") == "system" and changes(core) == []

    plan = core.settings.apply(document, actor="owner", skip_invalid=True)
    assert plan.applied and plan.skipped_invalid
    assert core.settings.get("appearance.theme") == "dark"
    assert core.settings.get("access.session_days") == 30


def test_each_change_lands_on_the_tape_with_who_made_it(core):
    core.settings.apply(doc({"appearance.theme": "dark", "statusbar.show": False}),
                        actor="token:importer")
    assert [(e.payload["key"], e.payload["value"], e.actor, e.subject) for e in changes(core)] == [
        ("appearance.theme", "dark", "token:importer", "setting:appearance.theme"),
        ("statusbar.show", False, "token:importer", "setting:statusbar.show"),
    ]


def test_a_value_that_is_already_set_writes_nothing(core):
    core.settings.set("appearance.theme", "dark", actor="owner")
    core.settings.apply(doc({"appearance.theme": "dark"}), actor="other")
    assert len(changes(core)) == 1


def test_null_puts_a_setting_back_to_its_default_and_undo_restores_the_old_values(core):
    core.settings.set("access.session_days", 7, actor="owner")
    plan = core.settings.apply(
        doc({"access.session_days": None, "appearance.theme": "dark"}), actor="owner")
    assert core.settings.get("access.session_days") == 30
    assert [e.kind for e in core.tape.after(0, kinds=("setting.reset",))] == ["setting.reset"]
    # Undo: what was chosen is chosen again, and what was only a default is reset.
    assert plan.undo_document()["settings"] == {"access.session_days": 7, "appearance.theme": None}
    core.settings.apply(plan.undo_document(), actor="owner")
    assert core.settings.get("access.session_days") == 7
    assert core.settings.get("appearance.theme") == "system"


def test_a_stored_value_is_applied_as_the_value_the_registry_would_store(core):
    core.settings.apply(doc({"access.session_days": 14.0, "owner.timezone": " Europe/Paris "}),
                        actor="owner")
    assert core.settings.get("access.session_days") == 14
    assert core.settings.get("owner.timezone") == "Europe/Paris"


# --- over HTTP ---------------------------------------------------------------------


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}
                       ).status_code == 200
    return client


def test_the_export_route_hands_over_a_json_file_with_a_sensible_name(owner):
    full = owner.get("/api/settings/export")
    assert full.status_code == 200
    assert full.headers["content-disposition"].startswith('attachment; filename="jimmyhq-settings-')
    assert full.json()["settings"]["owner.name"] == "Sam"

    profile = owner.get("/api/settings/export", params={"portable": "true", "changed": "true"})
    assert 'filename="jimmyhq-profile.json"' in profile.headers["content-disposition"]
    assert "owner.name" not in profile.json()["settings"]
    assert profile.json()["jimmyhq_settings"] == 1


def test_the_plan_route_describes_a_file_and_changes_nothing(owner, core):
    before = core.tape.last_seq()
    r = owner.post("/api/settings/plan", json=doc({"appearance.theme": "dark", "nope": 1}))
    assert r.status_code == 200
    body = r.json()
    assert [c["key"] for c in body["changes"]] == ["appearance.theme"]
    assert [c["key"] for c in body["refused"]] == ["nope"] and body["refused"][0]["reason"]
    assert body["applied"] is False and core.tape.last_seq() == before


def test_the_import_route_applies_a_file_as_the_owner_and_offers_an_undo(owner, core):
    r = owner.post("/api/settings/import", json=doc({"appearance.theme": "dark"}))
    assert r.status_code == 200 and r.json()["applied"] is True
    assert core.settings.get("appearance.theme") == "dark"
    assert [(e.actor, e.payload["key"]) for e in changes(core)][-1] == ("owner", "appearance.theme")
    undo = owner.post("/api/settings/import", json=r.json()["undo"])
    assert undo.status_code == 200 and core.settings.get("appearance.theme") == "system"


def test_the_import_route_refuses_a_file_with_a_bad_entry_unless_asked_to_skip_it(owner, core):
    document = doc({"appearance.theme": "dark", "access.session_days": 0})
    r = owner.post("/api/settings/import", json=document)
    assert r.status_code == 422 and "Nothing was changed" in r.json()["detail"]
    assert core.settings.get("appearance.theme") == "system"
    r = owner.post("/api/settings/import", params={"skip_invalid": "true"}, json=document)
    assert r.status_code == 200 and core.settings.get("appearance.theme") == "dark"
    assert [e["key"] for e in r.json()["refused"]] == ["access.session_days"]


def test_a_file_that_is_too_big_or_not_json_is_refused_with_a_sentence(owner):
    big = owner.post("/api/settings/import", content=b" " * (s.MAX_FILE_BYTES + 1),
                     headers={"content-type": "application/json"})
    assert big.status_code == 413 and "cannot be larger than 256 KB" in big.json()["detail"]
    bad = owner.post("/api/settings/plan", content=b"{nope",
                     headers={"content-type": "application/json"})
    assert bad.status_code == 422 and "not valid JSON" in bad.json()["detail"]
    newer = owner.post("/api/settings/plan", json={"jimmyhq_settings": 5, "settings": {}})
    assert newer.status_code == 422 and "Update JimmyHQ" in newer.json()["detail"]


@pytest.mark.parametrize("scopes", [["read"], ["read", "write"]])
def test_a_token_without_owner_rights_cannot_import_but_can_read(owner, core, scopes):
    _, token = core.auth.create_token("wall", scopes)
    owner.cookies.clear()
    headers = {"authorization": f"Bearer {token}"}
    document = doc({"appearance.theme": "dark"})
    assert owner.post("/api/settings/import", json=document, headers=headers).status_code == 403
    assert core.settings.get("appearance.theme") == "system"
    assert owner.post("/api/settings/plan", json=document, headers=headers).status_code == 200
    assert owner.get("/api/settings/export", headers=headers).status_code == 200


def test_a_signed_out_visitor_cannot_use_the_settings_file_routes(client):
    assert client.get("/api/settings/export").status_code == 401
    assert client.post("/api/settings/plan", json=doc({})).status_code == 401
    assert client.post("/api/settings/import", json=doc({})).status_code == 401
