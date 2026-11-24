"""The manifest, the settings, the state tables and the owner's configuration records."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from posts import config, pages, states
from posts.states import StateError
from posts_rig import APP_ROOT

# --- the manifest and the settings ------------------------------------------------------------


def test_the_manifest_passes_the_platforms_own_checks():
    from jimmyhq.apps import manifest as mf  # noqa: PLC0415

    m = mf.load(APP_ROOT)
    assert m.tier == "full" and m.server.entry == "posts.main:app"
    assert {c.kind for c in m.cards} == {"draft", "overlap", "notice"}
    assert [s.name for s in m.secrets] == ["socialbu_key", "buffer_key"]


def test_the_owners_own_choices_stay_in_plain_sight_and_tuning_numbers_are_advanced():
    from jimmyhq.apps import manifest as mf  # noqa: PLC0415

    m = mf.load(APP_ROOT)
    advanced = {s.key for s in m.settings if s.advanced}
    plain_sight = {"scheduler", "posts_per_page_per_day", "sponsor_tag", "role_draft",
                   "role_caption", "role_check", "agent_steps_per_piece", "on_stall",
                   "dup_warn", "dup_strong", "picture_keep_days", "decline_reasons"}
    assert plain_sight & advanced == set()
    assert {"request_timeout_s", "tool_page_chars", "recycled_words"} <= advanced
    # The ones in plain sight come first, so the page opens on what matters.
    flags = [s.advanced for s in m.settings]
    assert flags == sorted(flags)


def test_every_setting_the_code_reads_is_declared_and_every_declared_one_is_read():
    pattern = re.compile(r"\bcfg(?:\(\))?\.([a-z_]+)")
    read: set[str] = set()
    for path in (APP_ROOT / "posts").rglob("*.py"):
        read |= set(pattern.findall(path.read_text(encoding="utf-8")))
    read -= {"values"}
    declared = set(config.defaults())
    assert read - declared == set()
    # The thresholds are read in pairs by name; watch_minutes and intake_minutes are how
    # often JimmyHQ runs the watch and intake jobs (``every_setting``).
    assert declared - read - {"dup_warn", "dup_strong", "words_warn", "words_strong",
                              "watch_minutes", "intake_minutes"} == set()


def test_no_module_but_config_holds_a_threshold_from_the_settings():
    for path in (APP_ROOT / "posts").rglob("*.py"):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "settings.get" not in text and "settings.all" not in text, path.name


def test_saving_the_duplicate_thresholds_the_wrong_way_round_is_refused(rig):
    rig.core.change_setting("dup_warn", 0.9)
    [card] = rig.cards_of("notice")
    assert "not below" in card["body"] and "did not take the change" in card["body"]
    assert "0.80 and 0.88" in card["body"]
    limits = config.thresholds(rig.ctx.cfg(), rig.store, "embeddings")
    assert (limits.low, limits.high) == (0.80, 0.88) and limits.refused


def test_a_good_pair_of_thresholds_is_kept_and_used_after_a_wrong_one(rig):
    rig.core.change_setting("dup_warn", 0.70)
    rig.core.change_setting("dup_strong", 0.60)
    limits = config.thresholds(rig.ctx.cfg(), rig.store, "embeddings")
    assert (limits.low, limits.high) == (0.70, 0.88)


def test_the_word_thresholds_are_held_to_the_same_rule():
    assert config.check_thresholds("words", 0.5, 0.5) is not None
    assert config.check_thresholds("words", 0.3, 0.5) is None


# --- the state tables -------------------------------------------------------------------------


@pytest.mark.parametrize("table", [states.PIECE, states.POST])
def test_every_state_leads_only_to_states_the_table_knows(table):
    for old, new in table.items():
        assert new <= set(table), old


def test_a_finished_piece_or_post_has_no_way_out():
    for done in ("closed", "dropped"):
        assert states.PIECE[done] == frozenset()
    for done in ("went_out", "declined"):
        assert states.POST[done] == frozenset()


def test_a_change_the_table_does_not_allow_is_refused_with_a_sentence(rig):
    page = rig.page()
    piece = rig.link(page)
    with pytest.raises(StateError, match="reading the source, so it cannot be waiting for you"):
        rig.store.move_piece(piece["id"], "ready")
    with pytest.raises(StateError, match="no such state"):
        rig.store.move_piece(piece["id"], "posted")


def test_only_the_store_changes_a_state():
    pattern = re.compile(r"UPDATE\s+(piece|post)\s+SET\s+state\b", re.I)
    for path in (APP_ROOT / "posts").rglob("*.py"):
        if path.name != "store.py":
            assert not pattern.search(path.read_text(encoding="utf-8")), path.name


# --- pages, rules and lessons -----------------------------------------------------------------


def test_a_first_run_has_no_pages_and_says_how_to_add_one(rig):
    found = rig.core.call("pages.list")
    assert found == {"pages": [], "hint": pages.NO_PAGES}
    with pytest.raises(Exception, match="no brands yet"):
        rig.core.call("new.link", {"page_id": 1, "url": "https://news.example.com/a"})


def test_a_page_keeps_its_voice_hours_accounts_and_tags(rig):
    page = rig.page(timezone="Europe/Paris", hours=["17:30", "08:00"], tags=["#garden"],
                    accounts=[{"scheduler": "practice", "account_id": "a1", "label": "Main"}])
    assert page["hours"] == ["08:00", "17:30"] and page["timezone"] == "Europe/Paris"
    assert page["tags"] == ["#garden"] and page["accounts"][0]["label"] == "Main"


@pytest.mark.parametrize("given, said", [
    ({"timezone": "Mars/Olympus"}, "not a time zone"),
    ({"hours": ["8am"]}, "not a time of day"),
    ({"hours": []}, "at least one posting time"),
    ({"name": ""}, "needs a name"),
])
def test_a_page_that_cannot_work_is_refused_with_what_to_change(rig, given, said):
    with pytest.raises(Exception, match=said):
        rig.page(**given)


def test_two_pages_cannot_share_a_name(rig):
    rig.page()
    with pytest.raises(Exception, match="already have a brand"):
        rig.page()


def test_a_quoted_span_in_a_send_back_note_becomes_a_phrase_to_flag(rig):
    page = rig.page()
    lesson = pages.add_lesson(rig.store, page_id=page["id"], post_id=1, kind="send_back",
                              note="Drop “a game changer” and \"in today's world\".")
    row = rig.store.one("SELECT * FROM lesson WHERE id=?", (lesson,))
    assert row["quotes"] == ["a game changer", "in today's world"]


def test_the_app_ships_no_personal_sample_data():
    for path in APP_ROOT.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".json", ".js", ".html", ".md"):
            text = path.read_text(encoding="utf-8")
            for url in re.findall(r"https?://([a-z0-9-]+(?:\.[a-z0-9-]+)+)", text):
                assert url.endswith("example.com") or url.endswith("example.net"), \
                    f"{Path(path).name}: {url}"
