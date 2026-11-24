"""Practice, end to end: the default service draws a labelled picture on this computer, the
picture is kept and listed, and nothing is sent anywhere."""

from __future__ import annotations

from imagery import png
from imagery.providers.practice import draw, wrap


def test_a_practice_picture_is_made_kept_and_listed_with_no_outside_call(rig):
    started = rig.make("A red kite over a hill", count=2)
    assert started["said"].startswith("Making 2 pictures")
    rows = rig.pictures()
    assert len(rows) == 2
    assert {r["provider"] for r in rows} == {"practice"}
    assert {r["prompt"] for r in rows} == {"A red kite over a hill"}
    assert rig.core.fetched == []
    assert rig.last_run()["state"] == "done" and rig.last_run()["made"] == 2
    assert len(rig.files()) == 2 and all(png.kind_of(p.read_bytes()[:16]) == "png"
                                         for p in rig.files())


def test_a_practice_picture_is_a_real_png_in_the_shape_asked_for():
    data = draw("A harbour at dawn", "16:9")
    assert png.kind_of(data[:16]) == "png"
    assert png.size_of(data) == (512, 288)
    assert png.size_of(draw("x", "9:16")) == (288, 512)
    assert png.size_of(draw("x", "1:1")) == (512, 512)


def test_the_same_description_and_shape_always_draw_the_same_picture():
    assert draw("A harbour at dawn", "3:2") == draw("A harbour at dawn", "3:2")
    assert draw("A harbour at dawn", "3:2") != draw("A harbour at night", "3:2")


def test_a_long_description_is_cut_to_fit_and_says_so_with_a_dot():
    lines = wrap("one two three four five six seven eight nine ten", 10, 2)
    assert len(lines) == 2 and lines[-1].endswith(".")
    assert all(len(line) <= 10 for line in lines)


def test_practice_needs_no_key_and_offers_the_shapes_in_the_settings(rig):
    shown = rig.overview()
    assert shown["provider"]["id"] == "practice" and shown["provider"]["problem"] is None
    assert shown["provider"]["outside"] is False
    assert "16:9" in shown["provider"]["aspects"]
    assert shown["today"]["used"] == 0


def test_practice_never_counts_against_the_daily_limit(make_rig):
    rig = make_rig(settings={"daily_limit": 1})
    rig.make(count=3)
    assert len(rig.pictures()) == 3
    assert rig.overview()["today"]["used"] == 0
