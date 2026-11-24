"""The spend guard: a per-day limit on requests to outside services, counted before they are
sent, and refused with a plain sentence."""

from __future__ import annotations

import pytest
from imagery_rig import GEMINI_URL, HOUR
from imagery_service import gemini_error, gemini_image
from jimmyhq_app import CallError


@pytest.fixture
def gem(make_rig):
    def make(**settings):
        rig = make_rig(provider="gemini", settings=settings)
        rig.service.on(GEMINI_URL, gemini_image())
        return rig
    return make


def test_a_request_that_would_go_over_the_daily_limit_is_refused_and_nothing_is_sent(gem):
    rig = gem(daily_limit=3)
    rig.make(count=2)
    with pytest.raises(CallError) as refused:
        rig.make(count=2)
    said = refused.value.message
    assert "2 of today's 3" in said and "Nothing was made" in said
    assert "Ask for 1 or fewer" in said and "Pictures a day with an outside service" in said
    assert len(rig.core.fetched) == 2 and len(rig.pictures()) == 2


def test_the_page_is_told_how_many_are_used_and_left_today(gem):
    rig = gem(daily_limit=5)
    rig.make(count=2)
    assert rig.overview()["today"] == {"used": 2, "limit": 5, "left": 3}


def test_when_the_limit_is_used_up_the_sentence_says_to_try_tomorrow_or_use_practice(gem):
    rig = gem(daily_limit=1)
    rig.make()
    with pytest.raises(CallError) as refused:
        rig.make()
    assert "your limit of 1" in refused.value.message
    assert "tomorrow" in refused.value.message and "Practice" in refused.value.message


def test_a_request_the_service_refused_still_counts_since_it_may_be_charged(gem):
    rig = gem(daily_limit=5)
    rig.service.on(GEMINI_URL, gemini_error(429, "RESOURCE_EXHAUSTED", "Over."))
    rig.make()
    assert rig.overview()["today"]["used"] == 1


def test_a_run_stops_part_way_when_another_run_used_the_rooms_meanwhile(gem):
    rig = gem(daily_limit=3)
    started = rig.core.call("make", {"prompt": "First", "aspect": "1:1", "count": 3})
    rig.store.insert("attempt", {"ts": rig.clock.now, "provider": "gemini", "run_id": None})
    rig.ctx.maker.drain()
    run = rig.store.run(started["run"]["id"])
    assert run["made"] == 2 and run["state"] == "failed"
    assert run["said"].startswith("Made 2 of 3.") and "your limit of 3" in run["said"]


def test_a_new_day_in_the_owners_time_zone_starts_the_count_again(gem, make_rig):
    rig = make_rig(provider="gemini", timezone="America/New_York", settings={"daily_limit": 2})
    rig.service.on(GEMINI_URL, gemini_image())
    rig.make()  # 08:00 UTC is 03:00 in New York
    rig.clock.advance(15 * HOUR)  # 23:00 UTC is 18:00 the same day there
    assert rig.overview()["today"]["used"] == 1
    rig.clock.advance(6 * HOUR)  # 05:00 UTC the next day is midnight there
    assert rig.overview()["today"]["used"] == 0


def test_the_day_starts_at_midnight_utc_for_an_owner_in_utc(gem):
    rig = gem(daily_limit=2)
    rig.make()
    rig.clock.advance(15 * HOUR)
    assert rig.overview()["today"]["used"] == 1
    rig.clock.advance(1 * HOUR)
    assert rig.overview()["today"]["used"] == 0


def test_a_week_old_request_is_dropped_from_the_books(gem):
    rig = gem(daily_limit=50)
    rig.make()
    rig.clock.advance(8 * 24 * HOUR)
    rig.make()
    assert rig.store.number("SELECT COUNT(*) FROM attempt") == 1


def test_practice_shows_that_it_uses_none_of_the_limit(rig):
    assert rig.overview()["provider"]["outside"] is False
