"""The gallery: newest first, search, star, delete for good, and the storage limits."""

from __future__ import annotations

import os

import pytest
from imagery import png
from imagery_rig import DAY, HOUR
from imagery_service import GEMINI_HOST, gemini_image
from jimmyhq_app import CallError


def noise_png(seed: int, side: int = 260) -> bytes:
    """About 200 KB that does not squeeze, so a few of them fill a small limit."""
    rows = [os.urandom(side * 3) for _ in range(side)]
    return png.write_png(side, side, rows)


def test_pictures_are_listed_newest_first_with_prompt_service_model_and_time(rig):
    for words in ("A red kite", "A blue boat", "A green hill"):
        rig.clock.advance(HOUR)
        rig.make(words)
    rows = rig.pictures()
    assert [r["prompt"] for r in rows] == ["A green hill", "A blue boat", "A red kite"]
    first = rows[0]
    assert first["provider"] == "practice" and first["model"] == "practice"
    assert first["created"] == rig.clock.now and first["aspect"] == "1:1"
    assert first["bytes"] > 0 and first["width"] == 512


def test_search_finds_pictures_whose_description_has_every_word_in_any_case(rig):
    rig.make("A red kite over a hill")
    rig.make("A blue boat in a bay")
    rig.make("A red boat at the quay")
    assert len(rig.pictures(query="red")) == 2
    assert [r["prompt"] for r in rig.pictures(query="RED boat")] == ["A red boat at the quay"]
    assert rig.pictures(query="zebra") == []
    assert rig.core.call("pictures.list", {"query": "red"})["total"] == 2


def test_a_percent_or_underscore_in_a_search_is_text_not_a_wildcard(rig):
    rig.make("A sale of 50% off")
    rig.make("A sale of 500 off")
    rig.make("A snake_case sign")
    assert [r["prompt"] for r in rig.pictures(query="50%")] == ["A sale of 50% off"]
    assert [r["prompt"] for r in rig.pictures(query="snake_case")] == ["A snake_case sign"]
    assert rig.pictures(query="snake_c%e") == []


def test_the_gallery_shows_a_page_at_a_time_with_the_total(rig):
    for n in range(5):
        rig.make(f"Picture number {n}")
    page = rig.core.call("pictures.list", {"limit": 2, "offset": 2})
    assert len(page["rows"]) == 2 and page["total"] == 5


def test_a_star_is_kept_and_can_be_taken_off_and_starred_ones_can_be_listed(rig):
    rig.make("A red kite")
    rig.make("A blue boat")
    first = rig.pictures()[1]
    assert rig.core.call("picture.star", {"id": first["id"]})["starred"] is True
    assert [r["prompt"] for r in rig.pictures(starred=True)] == ["A red kite"]
    assert rig.core.call("picture.star", {"id": first["id"]})["starred"] is False
    assert rig.core.call("picture.star", {"id": first["id"], "on": True})["starred"] is True


def test_deleting_a_picture_removes_its_file_and_leaves_no_trace_in_the_database(rig):
    rig.make("A zebra-unique-phrase in a field")
    [row] = rig.pictures()
    path = rig.core.data_dir / row["ref"]
    assert path.is_file()
    said = rig.core.call("picture.delete", {"id": row["id"]})["said"]
    assert "gone from this computer" in said
    assert not path.exists() and rig.files() == [] and rig.pictures() == []
    for db in rig.core.data_dir.glob("imagery.db*"):
        assert b"zebra-unique-phrase" not in db.read_bytes(), db.name


def test_deleting_a_picture_that_is_not_there_says_nothing_changed(rig):
    with pytest.raises(CallError) as refused:
        rig.core.call("picture.delete", {"id": 999})
    assert "no such picture" in refused.value.message


def test_over_the_picture_count_the_oldest_unstarred_are_deleted_and_the_owner_is_told(make_rig):
    rig = make_rig(settings={"max_pictures": 3})
    for n in range(5):
        rig.clock.advance(HOUR)
        rig.make(f"Picture number {n}")
    assert [r["prompt"] for r in rig.pictures()] == [
        "Picture number 4", "Picture number 3", "Picture number 2"]
    assert len(rig.files()) == 3
    assert rig.overview()["pruned"]["count"] == 2
    rig.core.call("pruned.dismiss", {})
    assert rig.overview()["pruned"] is None


def test_a_starred_picture_is_never_deleted_to_make_room(make_rig):
    rig = make_rig(settings={"max_pictures": 3})
    rig.make("Keep this one")
    rig.core.call("picture.star", {"id": rig.pictures()[0]["id"]})
    for n in range(4):
        rig.clock.advance(HOUR)
        rig.make(f"Other {n}")
    prompts = [r["prompt"] for r in rig.pictures()]
    assert "Keep this one" in prompts and len(prompts) == 3


def test_a_gallery_full_of_starred_pictures_refuses_a_new_one_before_anything_is_sent(make_rig):
    rig = make_rig(provider="gemini")
    rig.service.on(f"https://{GEMINI_HOST}/v1beta/models/{rig.ctx.provider().model(rig.ctx.cfg())}"
                   ":generateContent", gemini_image())
    rig.make("One")
    rig.make("Two")
    for row in rig.pictures():
        rig.core.call("picture.star", {"id": row["id"]})
    rig.core.settings["max_pictures"] = 2
    rig.ctx.forget_settings()
    with pytest.raises(CallError) as refused:
        rig.make("Three")
    assert "starred pictures fill the gallery" in refused.value.message
    assert "Nothing was made" in refused.value.message and len(rig.core.fetched) == 2


def test_over_the_space_limit_the_oldest_unstarred_go_first_and_the_new_one_stays(make_rig):
    rig = make_rig(provider="gemini", settings={"max_total_mb": 1})
    rig.service.on(f"https://{GEMINI_HOST}/v1beta/models/{rig.ctx.provider().model(rig.ctx.cfg())}"
                   ":generateContent", *[gemini_image(noise_png(n, 420)) for n in range(4)])
    for n in range(4):
        rig.clock.advance(HOUR)
        rig.make(f"Noise {n}")
    rows = rig.pictures()
    assert [r["prompt"] for r in rows] == ["Noise 3"]
    assert sum(r["bytes"] for r in rows) <= 1024 * 1024 and len(rig.files()) == 1


def test_one_picture_over_the_whole_space_limit_is_kept_not_deleted_at_once(make_rig):
    rig = make_rig(provider="gemini", settings={"max_total_mb": 1})
    rig.service.on(f"https://{GEMINI_HOST}/v1beta/models/{rig.ctx.provider().model(rig.ctx.cfg())}"
                   ":generateContent", gemini_image(noise_png(1, 600)))
    rig.make("Only one")
    [row] = rig.pictures()
    assert row["prompt"] == "Only one" and row["bytes"] > 1024 * 1024


def test_the_gallery_reports_how_full_it_is(rig):
    rig.make(count=2)
    shown = rig.overview()["gallery"]
    assert shown["pictures"] == 2 and shown["max_pictures"] == 300
    assert shown["bytes"] > 0 and shown["max_bytes"] == 400 * 1024 * 1024
    assert shown["starred"] == 0


def test_make_more_like_this_has_everything_it_needs_in_the_row(rig):
    rig.make("A red kite over a hill", aspect="3:2", style="Photo")
    [row] = rig.pictures()
    assert (row["prompt"], row["aspect"], row["style"]) == (
        "A red kite over a hill", "3:2", "Photo")


def test_old_days_do_not_matter_to_the_listing(rig):
    rig.make("Long ago")
    rig.clock.advance(400 * DAY)
    rig.make("Today")
    assert [r["prompt"] for r in rig.pictures()] == ["Today", "Long ago"]
