from __future__ import annotations

import pytest

from jimmyhq import config
from jimmyhq.apps import quotas
from jimmyhq.apps.quotas import QuotaExceeded
from jimmyhq.core import open_core
from jimmyhq.settings import SettingError

NOW = 1_800_000_000_000  # a fixed moment, unix ms; also the start of a second and a minute


def test_every_limit_is_a_setting_in_the_apps_section_with_the_designs_default(core):
    described = {s["key"]: s["section"] for s in core.settings.describe()["settings"]}
    defaults = {q.name: q.default for q in quotas.QUOTAS}
    assert defaults["storage_mb"] == 50 and defaults["value_kb"] == 256
    assert defaults["calls_per_second"] == 20 and defaults["model_calls_per_hour"] == 30
    assert defaults["fetches_per_hour"] == 60 and defaults["open_cards"] == 10
    assert defaults["tasks_per_day"] == 50
    for quota in quotas.QUOTAS:
        assert core.settings.get(quota.key) == quota.default
        assert quotas.limit(core, "water", quota.name) == quota.default
        assert described[quota.key] in ("apps_storage", "apps_limits")  # parts of the Apps page


def test_the_owner_changes_a_limit_for_everyone_or_for_one_app(core):
    core.settings.set("apps.quota.model_calls_per_hour", 5, actor="owner")
    core.settings.set(quotas.OVERRIDES, ["water.model_calls_per_hour=100",
                                         "water.storage_mb=1"], actor="owner")
    assert quotas.limit(core, "water", "model_calls_per_hour") == 100
    assert quotas.limit(core, "water", "storage_mb") == 1
    assert quotas.limit(core, "other", "model_calls_per_hour") == 5
    assert quotas.limits(core, "other")["storage_mb"] == 50


@pytest.mark.parametrize("line, word", [
    ("water", "such as water.model_calls_per_hour=100"),
    ("water.model_calls_per_hour=many", "whole number"),
    ("water.model_calls_per_hour=-1", "whole number"),
    ("Water.records=5", "app's id"),
    ("water.speed=5", "no limit called \"speed\""),
    ("water.storage_mb=99999999", "cannot be more than"),
])
def test_an_override_written_wrongly_is_refused_with_a_sentence(core, line, word):
    with pytest.raises(SettingError) as caught:
        core.settings.set(quotas.OVERRIDES, [line], actor="owner")
    assert word in str(caught.value) and "othing was saved" in str(caught.value)
    assert core.settings.get(quotas.OVERRIDES) == []


def test_the_same_override_twice_is_refused(core):
    with pytest.raises(SettingError):
        core.settings.set(quotas.OVERRIDES, ["water.records=5", "water.records=6"],
                          actor="owner")


def test_a_rate_allows_its_number_then_refuses_saying_the_number_and_the_wait(core):
    core.settings.set("apps.quota.model_calls_per_hour", 3, actor="owner")
    hour = NOW - NOW % 3_600_000
    for _ in range(3):
        quotas.take(core, "water", "model_calls_per_hour", now=hour + 60_000)
    with pytest.raises(QuotaExceeded) as caught:
        quotas.take(core, "water", "model_calls_per_hour", now=hour + 48 * 60_000)
    error = caught.value
    assert error.retry_after_s == 12 * 60
    assert "all 3 model calls" in error.message and "in an hour" in error.message
    assert "Nothing was done" in error.message and "12 minutes" in error.message
    assert "\"Model calls an hour\"" in error.message
    assert quotas.used(core, "water", "model_calls_per_hour", now=hour + 48 * 60_000) == 3


def test_a_refused_call_is_not_counted_and_the_next_window_starts_fresh(core):
    core.settings.set("apps.quota.calls_per_second", 2, actor="owner")
    quotas.take(core, "water", "calls_per_second", now=NOW)
    quotas.take(core, "water", "calls_per_second", now=NOW + 10)
    for _ in range(5):
        with pytest.raises(QuotaExceeded) as caught:
            quotas.take(core, "water", "calls_per_second", now=NOW + 500)
        assert caught.value.retry_after_s == 1 and "1 second" in caught.value.message
    assert quotas.used(core, "water", "calls_per_second", now=NOW + 999) == 2
    quotas.take(core, "water", "calls_per_second", now=NOW + 1000)
    assert quotas.used(core, "water", "calls_per_second", now=NOW + 1000) == 1


def test_one_apps_use_never_counts_against_another(core):
    core.settings.set("apps.quota.tasks_per_day", 1, actor="owner")
    quotas.take(core, "water", "tasks_per_day", now=NOW)
    quotas.take(core, "other", "tasks_per_day", now=NOW)
    with pytest.raises(QuotaExceeded):
        quotas.take(core, "water", "tasks_per_day", now=NOW)


def test_a_limit_of_nothing_refuses_everything_and_says_who_can_change_it(core):
    core.settings.set(quotas.OVERRIDES, ["water.fetches_per_hour=0"], actor="owner")
    with pytest.raises(QuotaExceeded) as caught:
        quotas.take(core, "water", "fetches_per_hour", now=NOW)
    assert "set \"Network requests an hour\" to 0" in caught.value.message
    quotas.take(core, "other", "fetches_per_hour", now=NOW)


def test_tokens_are_added_after_the_fact_and_close_the_door_once_spent(core):
    core.settings.set("apps.quota.model_tokens_per_day", 1000, actor="owner")
    quotas.ensure_room(core, "water", "model_tokens_per_day", now=NOW)
    assert quotas.add(core, "water", "model_tokens_per_day", 600, now=NOW) == 600
    quotas.ensure_room(core, "water", "model_tokens_per_day", now=NOW)
    assert quotas.add(core, "water", "model_tokens_per_day", 600, now=NOW) == 1200
    with pytest.raises(QuotaExceeded) as caught:
        quotas.ensure_room(core, "water", "model_tokens_per_day", now=NOW)
    assert "all 1000 model tokens" in caught.value.message and "a day" in caught.value.message
    quotas.ensure_room(core, "water", "model_tokens_per_day", now=NOW + 86_400_000)


def test_counts_survive_a_restart(tmp_path):
    cfg = config.Config(data=tmp_path, host="127.0.0.1", port=0,
                        allowed_hosts=("testserver",), remote_setup=True)
    first = open_core(cfg)
    first.settings.set("apps.quota.model_calls_per_hour", 2, actor="owner")
    quotas.take(first, "water", "model_calls_per_hour", now=NOW)
    quotas.take(first, "water", "model_calls_per_hour", now=NOW)
    quotas.add(first, "water", "model_tokens_per_day", 123, now=NOW)
    first.close()
    second = open_core(cfg)
    try:
        assert quotas.used(second, "water", "model_calls_per_hour", now=NOW) == 2
        assert quotas.used(second, "water", "model_tokens_per_day", now=NOW) == 123
        with pytest.raises(QuotaExceeded):
            quotas.take(second, "water", "model_calls_per_hour", now=NOW + 1)
    finally:
        second.close()


def test_the_counts_live_in_the_apps_working_database(core):
    quotas.take(core, "water", "calls_per_day", now=NOW)
    assert quotas.state_db(core) == core.config.data / "apps" / "apps.db"
    assert quotas.state_db(core).is_file()


@pytest.mark.parametrize("app_id", ["", "../water", "Water", None, "water' OR '1'='1"])
def test_a_count_is_never_kept_for_something_that_is_not_an_app_id(core, app_id):
    with pytest.raises(ValueError):
        quotas.take(core, app_id, "calls_per_day", now=NOW)


def test_a_level_cannot_be_counted_like_a_rate(core):
    with pytest.raises(ValueError):
        quotas.take(core, "water", "storage_mb")


def test_usage_reports_each_rate_with_its_limit(core):
    quotas.take(core, "water", "tasks_per_day")
    shown = quotas.usage(core, "water")
    assert shown["tasks_per_day"] == {"used": 1, "limit": 50}
    assert "storage_mb" not in shown


@pytest.mark.parametrize("seconds, words", [(1, "1 second"), (59, "59 seconds"),
                                            (60, "1 minute"), (61, "2 minutes"),
                                            (3600, "1 hour"), (86_400, "24 hours")])
def test_a_wait_is_said_in_the_unit_a_person_would_use(seconds, words):
    assert quotas.wait_words(seconds) == words
