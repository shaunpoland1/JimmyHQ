"""The two limits an owner sets, tried directly with a service that counts its own calls:
the daily limit on outside requests (Practice never counted) and the space the gallery may
fill. Nothing reaches a network; the limits are small so a few pictures reach them."""

from __future__ import annotations

import random

import pytest
from imagery import png
from imagery.providers import Made, ProviderError
from imagery_rig import HOUR
from jimmyhq_app import CallError

MB = 1024 * 1024


def noise_png(seed: int, side: int) -> bytes:
    """A picture that does not squeeze, the same every time for a seed: about 3 bytes a pixel."""
    pixels = random.Random(seed)
    return png.write_png(side, side, [pixels.randbytes(side * 3) for _ in range(side)])


class CountedService:
    """An image service that only counts: how many times it was asked, and what it made.
    ``side`` sets the picture's size; ``fail`` is what to raise instead, in turn."""

    supports_reference = False
    label = "Counted"

    def __init__(self, *, outside: bool = True, side: int = 4, id: str = "gemini"):
        self.id, self.outside, self.side = id, outside, side
        self.calls = 0
        self.fail: list[ProviderError | None] = []

    def hosts(self, cfg):
        return ()

    def problem(self, cfg):
        return None

    def aspects(self, cfg):
        return ["1:1"]

    def model(self, cfg):
        return "counted-1"

    def make(self, request, cfg):
        self.calls += 1
        if self.fail:
            error = self.fail.pop(0)
            if error is not None:
                raise error
        return Made(noise_png(self.calls, self.side), "image/png")


@pytest.fixture
def counted(make_rig):
    """A rig whose chosen service is a counted one, with the limits the test sets."""
    def make(*, outside: bool = True, side: int = 4, **limits):
        rig = make_rig(provider="gemini", settings=limits)
        service = CountedService(outside=outside, side=side)
        rig.ctx.providers["gemini"] = service
        return rig, service
    return make


def used(rig) -> int:
    return rig.overview()["today"]["used"]


# --- the daily limit ----------------------------------------------------------------------


def test_the_service_is_asked_exactly_as_often_as_the_daily_limit_and_not_once_more(counted):
    rig, service = counted(daily_limit=3)
    for _ in range(3):
        rig.make()
    with pytest.raises(CallError) as refused:
        rig.make()
    assert "your limit of 3" in refused.value.message
    assert service.calls == 3 and used(rig) == 3 and len(rig.pictures()) == 3


def test_a_request_for_more_than_is_left_is_refused_whole_and_the_service_is_not_asked(counted):
    rig, service = counted(daily_limit=3)
    rig.make(count=2)
    with pytest.raises(CallError) as refused:
        rig.make(count=2)
    said = refused.value.message
    assert "2 of today's 3" in said and "Ask for 1 or fewer" in said
    assert service.calls == 2 and used(rig) == 2
    rig.make(count=1)
    assert service.calls == 3 and used(rig) == 3


def test_the_limit_is_the_days_not_the_runs_and_comes_back_at_the_next_midnight(counted):
    rig, service = counted(daily_limit=2)
    rig.make()
    rig.clock.advance(2 * HOUR)
    rig.make()
    with pytest.raises(CallError):
        rig.make()
    assert service.calls == 2
    rig.clock.advance(16 * HOUR)  # 08:00 UTC at the start, so this is 02:00 the next day
    assert used(rig) == 0
    rig.make()
    assert service.calls == 3 and used(rig) == 1


def test_practice_is_never_counted_whatever_the_limit_and_however_many_are_made(counted):
    rig, service = counted(outside=False, daily_limit=1)
    for _ in range(3):
        rig.make(count=2)
    assert service.calls == 6 and len(rig.pictures()) == 6
    assert used(rig) == 0 and rig.store.number("SELECT COUNT(*) FROM attempt") == 0
    assert rig.overview()["provider"]["outside"] is False


def test_practice_pictures_do_not_use_up_room_that_an_outside_service_may_have(make_rig):
    rig = make_rig(settings={"daily_limit": 1})
    outside = CountedService(outside=True)
    rig.ctx.providers["gemini"] = outside
    rig.make(count=3)  # Practice, which the rig starts on
    assert used(rig) == 0
    rig.core.change_setting("provider", "gemini")
    rig.ctx.forget_settings()
    rig.make()
    assert outside.calls == 1 and used(rig) == 1
    with pytest.raises(CallError):
        rig.make()
    assert outside.calls == 1


def test_a_request_the_service_failed_is_counted_since_it_may_be_charged(counted):
    rig, service = counted(daily_limit=5)
    service.fail = [ProviderError("busy", "Busy, so nothing was made. Try again.")]
    rig.make()
    assert rig.last_run()["state"] == "failed" and used(rig) == 1


def test_a_request_that_never_left_this_computer_is_not_counted(counted):
    rig, service = counted(daily_limit=5)
    service.fail = [ProviderError("setup", "Not sent.", sent=False)]
    rig.make()
    assert rig.last_run()["state"] == "failed" and used(rig) == 0
    assert service.calls == 1


def test_a_run_that_meets_the_limit_part_way_keeps_what_was_made_and_asks_no_more(counted):
    rig, service = counted(daily_limit=3)
    started = rig.core.call("make", {"prompt": "First", "aspect": "1:1", "count": 3})
    rig.store.insert("attempt", {"ts": rig.clock.now, "provider": "gemini", "run_id": None})
    rig.ctx.maker.drain()
    run = rig.store.run(started["run"]["id"])
    assert service.calls == 2 and run["made"] == 2 and "your limit of 3" in run["said"]
    assert used(rig) == 3


def test_the_pages_count_says_how_many_are_left_as_the_limit_is_used(counted):
    rig, _ = counted(daily_limit=4)
    rig.make(count=3)
    assert rig.overview()["today"] == {"used": 3, "limit": 4, "left": 1}
    assert rig.core.call("status", {})["today"]["left"] == 1


# --- the space the gallery may fill ---------------------------------------------------------


def total(rig) -> int:
    return rig.store.number("SELECT COALESCE(SUM(bytes),0) FROM picture")


def test_over_the_space_limit_the_oldest_pictures_not_starred_are_deleted_and_the_new_one_stays(
        counted):
    rig, service = counted(max_total_mb=1, side=330)  # each picture is about 0.33 MB
    for n in range(5):
        rig.clock.advance(HOUR)
        rig.make(f"Noise {n}")
    kept = [r["prompt"] for r in rig.pictures()]
    assert kept[0] == "Noise 4" and len(kept) in (2, 3) and "Noise 0" not in kept
    assert total(rig) <= MB and len(rig.files()) == len(kept)
    assert service.calls == 5
    assert rig.overview()["pruned"]["count"] == 5 - len(kept)


def test_a_deleted_picture_leaves_neither_its_file_nor_its_words(counted):
    rig, _ = counted(max_total_mb=1, side=330)
    rig.make("A unique-zebra-words picture")
    for n in range(4):
        rig.clock.advance(HOUR)
        rig.make(f"Other {n}")
    assert "A unique-zebra-words picture" not in [r["prompt"] for r in rig.pictures()]
    for db in rig.core.data_dir.glob("imagery.db*"):
        assert b"unique-zebra-words" not in db.read_bytes(), db.name


def test_a_starred_picture_is_kept_when_the_space_is_over_and_others_go_instead(counted):
    rig, _ = counted(max_total_mb=1, side=330)
    rig.make("Keep me")
    rig.core.call("picture.star", {"id": rig.pictures()[0]["id"], "on": True})
    for n in range(4):
        rig.clock.advance(HOUR)
        rig.make(f"Other {n}")
    kept = [r["prompt"] for r in rig.pictures()]
    assert "Keep me" in kept and "Other 3" in kept and "Other 0" not in kept
    assert total(rig) <= MB


def test_when_starred_pictures_fill_the_space_a_new_request_is_refused_and_the_service_not_asked(
        counted):
    rig, service = counted(max_total_mb=1, side=450)  # two of them are about 1.2 MB
    rig.make()
    rig.core.call("picture.star", {"id": rig.pictures()[0]["id"], "on": True})
    rig.make()
    for row in rig.pictures():
        rig.core.call("picture.star", {"id": row["id"], "on": True})
    assert total(rig) > MB and len(rig.pictures()) == 2
    before = service.calls
    with pytest.raises(CallError) as refused:
        rig.make("Three")
    said = refused.value.message
    assert "starred pictures fill the gallery" in said and "Most space for pictures" in said
    assert "Nothing was made" in said
    assert service.calls == before and used(rig) == before
    assert len(rig.pictures()) == 2


def test_one_picture_larger_than_the_whole_space_is_kept_rather_than_deleted_at_once(counted):
    rig, _ = counted(max_total_mb=1, side=600)  # about 1.1 MB
    rig.make("Only one")
    [row] = rig.pictures()
    assert row["prompt"] == "Only one" and row["bytes"] > MB
    assert rig.overview()["gallery"]["bytes"] == row["bytes"]


def test_the_picture_count_limit_works_the_same_way_with_this_service(counted):
    rig, service = counted(max_pictures=3)
    for n in range(5):
        rig.clock.advance(HOUR)
        rig.make(f"Picture {n}")
    assert [r["prompt"] for r in rig.pictures()] == ["Picture 4", "Picture 3", "Picture 2"]
    assert service.calls == 5 and len(rig.files()) == 3


def test_the_pages_fullness_numbers_follow_what_is_kept(counted):
    rig, _ = counted(max_total_mb=1, side=330)
    rig.make()
    shown = rig.overview()["gallery"]
    assert shown["pictures"] == 1 and shown["max_bytes"] == MB and 0 < shown["bytes"] < MB
