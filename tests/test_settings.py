from __future__ import annotations

import copy

import pytest

from jimmyhq import settings as s
from jimmyhq.settings import Setting, SettingError


def test_a_setting_reads_its_default_until_it_is_changed(core):
    assert core.settings.get("appearance.theme") == "system"
    core.settings.set("appearance.theme", "dark", actor="owner")
    assert core.settings.get("appearance.theme") == "dark"


def test_a_change_is_recorded_on_the_tape_and_an_unchanged_value_is_not(core):
    core.settings.set("appearance.theme", "dark", actor="owner")
    core.settings.set("appearance.theme", "dark", actor="owner")
    events = core.tape.after(0, kind="setting.updated")
    assert [(e.payload["key"], e.payload["value"], e.actor) for e in events] == [
        ("appearance.theme", "dark", "owner")
    ]


def test_reset_puts_a_setting_back_to_its_default(core):
    core.settings.set("access.session_days", 7, actor="owner")
    assert core.settings.reset("access.session_days", actor="owner") == 30
    assert core.settings.get("access.session_days") == 30


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("appearance.theme", "purple", "must be one of"),
        ("access.session_days", 0, "cannot be less than 1"),
        ("access.session_days", 366, "cannot be more than 365"),
        ("access.session_days", True, "must be a number"),
        ("access.session_days", 1.5, "whole number"),
        ("statusbar.show", "yes", "on or off"),
        ("appearance.sidebar_order", ["bench", 3], "list of text"),
        ("owner.name", 12, "must be text"),
        ("no.such.setting", 1, "no setting called"),
    ],
)
def test_a_bad_value_is_refused_with_a_message_that_says_why(core, key, value, message):
    with pytest.raises(SettingError, match=message):
        core.settings.set(key, value, actor="owner")
    assert core.tape.after(0, kind="setting.updated") == []


def test_a_stored_value_that_no_longer_passes_the_rules_falls_back_to_the_default(core):
    core.tape.print("setting.updated", {"key": "access.session_days", "value": 9999},
                    actor="owner")
    assert core.settings.get("access.session_days") == 30


def test_the_description_carries_everything_the_settings_screen_needs(core):
    described = core.settings.describe()
    section_ids = [x["id"] for x in described["sections"]]
    assert section_ids[:2] == ["profile", "appearance"]
    theme = next(x for x in described["settings"] if x["key"] == "appearance.theme")
    assert theme["type"] == "choice"
    assert {"value": "dark", "label": "Dark"} in theme["choices"]
    days = next(x for x in described["settings"] if x["key"] == "access.session_days")
    assert (days["min"], days["max"], days["default"], days["value"]) == (1, 365, 30, 30)
    for item in described["settings"]:
        assert item["label"] and item["help"] and item["section"] in section_ids


def test_a_setting_whose_default_breaks_its_own_rules_cannot_be_declared():
    with pytest.raises(SettingError):
        s.declare(Setting(key="testing.bad", section="profile", type="int", default=5,
                          min=10, label="Bad", help="x"))


def test_a_list_default_handed_out_cannot_be_changed_by_the_caller(core):
    core.settings.get("appearance.sidebar_order").append("extra")
    core.settings.describe()["settings"][0]["default"] = "changed"
    assert core.settings.get("appearance.sidebar_order")[-1] == "apps"


def test_numbers_that_are_not_ordinary_numbers_are_refused():
    spec = Setting(key="t.ratio", section="profile", type="float", default=1.0, min=0, max=10,
                   label="Ratio", help="x")
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(SettingError, match="ordinary number"):
            spec.check(bad)


# --- sections: order, groups, parts of a page ---------------------------------


@pytest.fixture
def scratch_registry(monkeypatch):
    """Sections and settings declared in a test are gone when it ends."""
    monkeypatch.setattr(s, "_SECTIONS", {k: copy.copy(v) for k, v in s._SECTIONS.items()})
    monkeypatch.setattr(s, "_DECLARED", dict(s._DECLARED))


def _layout(core):
    described = core.settings.describe()
    return [x["id"] for x in described["sections"]], described["layout"]


def test_the_sections_come_in_a_deliberate_order_whatever_order_modules_load_in(core):
    ids, layout = _layout(core)
    pages = [i for i in ids if layout["sections"][i]["parent"] is None]
    assert pages == [
        "profile", "appearance", "access", "models", "voice", "lookup", "chat", "tasks",
        "bench", "memory", "wall", "village", "browser", "hands", "door", "apps", "nodes",
        "backup", "file"]
    assert [g["id"] for g in layout["groups"]] == [
        "you", "models", "work", "agents", "apps", "system"]
    assert layout["sections"]["models"]["group"] == "models"
    assert layout["sections"]["file"]["group"] == "system"


def test_every_section_says_in_one_line_what_it_is_for(core):
    ids, layout = _layout(core)
    for section_id in ids:
        line = layout["sections"][section_id]["description"]
        assert line and line.endswith(".") and len(line) <= 140, section_id


def test_the_village_is_one_page_whose_parts_follow_it_in_order(core):
    ids, layout = _layout(core)
    parts = [i for i in ids if layout["sections"][i]["parent"] == "village"]
    assert parts == ["village_turn", "village_view", "village_body", "village_sleep",
                     "village_guards", "village_comms", "village_night", "village_web"]
    start = ids.index("village")
    assert ids[start + 1:start + 9] == parts
    assert all(layout["sections"][i]["group"] == "agents" for i in parts)
    assert not any(label.startswith("Village:") for _, label in s.sections())


def test_the_import_and_export_page_is_listed_though_it_has_no_settings(core):
    described = core.settings.describe()
    assert described["sections"][-1] == {"id": "file", "label": "Import and export"}
    assert not [x for x in described["settings"] if x["section"] == "file"]


def test_a_section_with_no_group_is_listed_under_more_and_an_apps_own_with_the_apps(
        core, scratch_registry):
    s.section("gadgets", "Gadgets")
    s.section("app.demo", "Demo app")
    for key, where in (("gadgets.on", "gadgets"), ("app.demo.on", "app.demo")):
        s.declare(Setting(key=key, section=where, type="bool", default=True,
                          label="On", help="Whether it is on."))
    ids, layout = _layout(core)
    assert layout["sections"]["gadgets"]["group"] == "more"
    assert layout["sections"]["app.demo"]["group"] == "apps"
    assert [g["id"] for g in layout["groups"]] == [
        "you", "models", "work", "agents", "apps", "more", "system"]
    assert ids.index("apps") < ids.index("app.demo") < ids.index("gadgets") < ids.index("file")


def test_sections_in_a_group_follow_their_order_number_then_the_order_declared(
        scratch_registry):
    s.section("late", "Late", group="you", order=25)
    s.section("early", "Early", group="you", order=5)
    s.section("tie_a", "Tie A", group="you", order=25)
    ids = [i for i, _ in s.sections()]
    assert ids[:6] == ["early", "profile", "appearance", "late", "tie_a", "access"]


def test_declaring_a_section_again_keeps_its_place_and_its_description(scratch_registry):
    before = copy.copy(s._SECTIONS["memory"])
    s.section("memory", "Memory")
    assert s._SECTIONS["memory"] == before


def test_a_part_needs_a_real_page_to_belong_to(scratch_registry):
    with pytest.raises(ValueError, match="parent"):
        s.section("orphan", "Orphan", parent="nowhere")
    with pytest.raises(ValueError, match="parent"):
        s.section("nested", "Nested", parent="village_sleep")
    with pytest.raises(ValueError, match="group"):
        s.section("both", "Both", parent="village", group="you")
    with pytest.raises(ValueError, match="unknown group"):
        s.section("lost", "Lost", group="nowhere")


def test_a_page_with_no_settings_of_its_own_is_listed_when_one_of_its_parts_has(
        core, scratch_registry):
    s.section("shed", "Shed", group="work")
    s.section("shed_tools", "Tools", parent="shed")
    s.declare(Setting(key="shed.tools", section="shed_tools", type="int", default=1,
                      label="Tools", help="How many."))
    ids, layout = _layout(core)
    assert ids[ids.index("shed"):ids.index("shed") + 2] == ["shed", "shed_tools"]
    assert layout["sections"]["shed_tools"]["group"] == "work"


def test_the_time_zone_says_it_is_one_so_the_screen_can_offer_a_list(core):
    spec = next(x for x in core.settings.describe()["settings"] if x["key"] == "owner.timezone")
    assert (spec["type"], spec["format"], spec["default"]) == ("str", "timezone", "")
    assert core.settings.set("owner.timezone", " Europe/Paris ", actor="owner") == "Europe/Paris"
    assert core.settings.set("owner.timezone", "", actor="owner") == ""
    with pytest.raises(SettingError, match="Time zone is not a name this computer knows"):
        core.settings.set("owner.timezone", "Mars/Olympus", actor="owner")


def test_a_settings_file_with_a_time_zone_nobody_knows_is_refused_in_plain_words(core):
    plan = core.settings.plan({"jimmyhq_settings": 1, "settings": {
        "owner.timezone": "Mars/Olympus"}})
    assert [e.key for e in plan.refused] == ["owner.timezone"]
    assert "Use an IANA name such as Europe/Paris" in plan.refused[0].reason


def test_a_format_only_fits_a_text_setting(scratch_registry):
    with pytest.raises(ValueError, match="format"):
        s.declare(Setting(key="t.zone", section="profile", type="int", default=1,
                          format="timezone", label="Zone", help="A zone."))
    with pytest.raises(ValueError, match="format"):
        s.declare(Setting(key="t.zone", section="profile", type="str", default="",
                          format="colour", label="Zone", help="A zone."))


def test_the_status_bar_switch_sits_with_the_other_things_you_can_show_or_hide(core):
    spec = next(x for x in core.settings.describe()["settings"] if x["key"] == "statusbar.show")
    assert spec["section"] == "appearance"


def test_the_long_pages_are_in_headed_parts_and_no_setting_changed_its_key_to_get_there(core):
    described = core.settings.describe()
    ids, layout = [x["id"] for x in described["sections"]], described["layout"]

    def parts(page):
        return [i for i in ids if layout["sections"][i]["parent"] == page]

    assert parts("memory") == ["memory_notes", "memory_search", "memory_learning",
                               "memory_sources"]
    assert parts("door") == ["door_address", "door_limits", "door_messages", "door_tools"]
    assert parts("apps") == ["apps_install", "apps_storage", "apps_limits", "apps_full",
                             "apps_inbound", "apps_roles"]
    where = {x["key"]: x["section"] for x in described["settings"]}
    # The keys are the ones a settings file exported before the pages were split holds.
    assert where["memory.embed_local_only"] == "memory_search"
    assert where["memory.ignore_below"] == "memory_learning"
    assert where["memory.source.chat"] == "memory_sources"
    assert where["door.enabled"] == "door" and where["door.limit.tasks_add"] == "door_tools"
    assert where["apps.quota.storage_mb"] == "apps_storage"
    assert where["village.heartbeat_min_s"] == "village_turn"
    assert where["village.context.total_cap"] == "village_view"
    assert where["village.headcount_cap"] == "village"
    # A link to the old section still names a page: the pages kept their ids.
    assert all(layout["sections"][page]["parent"] is None
               for page in ("memory", "door", "apps", "village"))


def test_the_page_for_other_ai_apps_is_named_plainly_and_still_says_mcp(core):
    described = core.settings.describe()
    assert {"id": "door", "label": "Other AI apps"} in described["sections"]
    assert "(MCP)" in described["layout"]["sections"]["door"]["description"]
    assert described["layout"]["sections"]["door"]["description"].startswith("Others reaching in")
    assert described["layout"]["sections"]["hands"]["description"].startswith(
        "JimmyHQ reaching out")


def test_the_words_on_the_settings_screen_say_turn_and_meaning_search_not_beat_or_embed(core):
    for item in core.settings.describe()["settings"]:
        label = item["label"].lower()
        assert " beat" not in f" {label}" and "beats" not in label, item["key"]
        assert not label.startswith(("only embed", "passages to embed")), item["key"]
        assert "beat" not in item["help"].lower().split(), item["key"]


def test_an_advanced_setting_says_so_in_the_schema_and_a_plain_one_says_nothing(core):
    by_key = {x["key"]: x for x in core.settings.describe()["settings"]}
    assert by_key["memory.related_margin"]["advanced"] is True
    assert by_key["models.connect_timeout_s"]["advanced"] is True
    assert "advanced" not in by_key["appearance.theme"]
    assert "advanced" not in by_key["village.headcount_cap"]


def test_nothing_about_safety_permissions_privacy_or_money_is_hidden_as_advanced():
    in_sight = [
        "access.session_days", "access.allowed_hosts", "chat.local_only", "chat.on_record",
        "chat.memory_to_hosted", "chat.delete_archived_after_days", "chat.delete_grace_hours",
        "memory.embed_local_only", "memory.mine_local_only", "memory.auto_save_above",
        "memory.ignore_below", "memory.mining", "lookup.enabled", "lookup.allow_private_hosts",
        "lookup.fetch_any_url", "speech.hosted_callers", "bench.promotion_streak",
        "hands.calls_per_day", "hands.calls_per_month", "hands.requests_per_day",
        "hands.default_level_read", "hands.default_level_change", "hands.hidden_fields",
        "door.enabled", "door.allowed_hosts", "door.allowed_origins", "door.failed_tries",
        "door.calls_per_minute", "door.agent_replies_per_day", "door.limit.tasks_add",
        "apps.unreviewed_full_from_url", "apps.sandboxed_local_models_only",
        "apps.quota.model_calls_per_day", "apps.quota.model_tokens_per_day",
        "apps.quota.fetches_per_hour", "apps.quota.web_searches_per_day",
        "apps.quota.web_reads_per_day", "apps.quota.embed_calls_per_hour",
        "apps.roles.work_open", "apps.roles.work_per_day",
        "apps.roles.new_agent_local_only", "village.web.searches_per_month",
        "village.comms.chat_messages_per_day", "village.beat.effort", "models.currency",
    ]
    specs = {x.key: x for x in s.declared()}
    assert [k for k in in_sight if specs[k].advanced] == []
    # A switch or a choice is never fine tuning; only numbers, sizes and word lists are.
    assert [x.key for x in s.declared()
            if x.advanced and x.type in ("bool",)] == []
    hidden = sum(1 for x in s.declared() if x.advanced)
    assert 0 < hidden < len(s.declared())


def test_a_changed_advanced_setting_exports_and_imports_like_any_other(core):
    core.settings.set("memory.related_margin", 0.3, actor="owner")
    document = core.settings.export(changed_only=True)
    assert document["settings"] == {"memory.related_margin": 0.3}
    core.settings.reset("memory.related_margin", actor="owner")
    assert [e.key for e in core.settings.apply(document, actor="owner").changes] == [
        "memory.related_margin"]
    assert core.settings.get("memory.related_margin") == 0.3



def test_the_limits_on_files_and_secrets_an_app_is_given_are_under_apps(core):
    where = {x["key"]: (x["section"], x.get("advanced", False))
             for x in core.settings.describe()["settings"]}
    assert where["apps.upload_max_mb"] == ("apps_storage", False)
    assert where["apps.thumbnail_px"] == ("apps_storage", True)
    assert where["apps.picture_px"] == ("apps_storage", True)
    assert where["apps.picture_max_mb"] == ("apps_storage", True)
    assert where["apps.pictures_per_minute"] == ("apps_storage", True)
    assert where["apps.net_fetch_file_max_mb"] == ("apps_limits", True)
    assert where["apps.net_fetch_body_max_mb"] == ("apps_limits", True)
    assert where["apps.net_fetch_file_timeout_s"] == ("apps_limits", True)
    assert where["apps.app_key_max_chars"] == ("apps_limits", True)
    assert where["apps.local_addresses_max"] == ("apps_limits", True)
    assert where["apps.net_fetch_at_once"] == ("apps_limits", True)
