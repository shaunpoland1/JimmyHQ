"""Making: what a request must have, the runs the page polls, and what happens when a run is
stopped, queued too deep, or interrupted by a restart."""

from __future__ import annotations

import pytest
from imagery_rig import GEMINI_URL, Rig
from imagery_service import gemini_image
from jimmyhq_app import CallError


def refused(rig, **args) -> str:
    with pytest.raises(CallError) as error:
        rig.core.call("make", {"prompt": "A kite", "aspect": "1:1", "count": 1, **args})
    return error.value.message


def test_an_empty_description_is_refused_in_a_sentence_that_says_nothing_was_made(rig):
    said = refused(rig, prompt="   ")
    assert said.endswith("Say what the picture should show. Nothing was made.")


def test_a_description_over_the_limit_says_how_long_it_is_and_what_the_limit_is(make_rig):
    rig = make_rig(settings={"prompt_max_chars": 100})
    said = refused(rig, prompt="x" * 150)
    assert "150 characters" in said and "100 at most" in said


def test_how_many_is_one_to_the_most_the_owner_allows(make_rig):
    rig = make_rig(settings={"max_count": 2})
    assert "1 to 2" in refused(rig, count=3)
    assert "1 to 2" in refused(rig, count=0)
    assert "whole number" in refused(rig, count="many")
    assert rig.pictures() == []


def test_a_shape_the_service_cannot_make_is_refused_with_the_shapes_to_choose_from(rig):
    assert "cannot make pictures in the shape" in refused(rig, aspect="7:5")
    assert "none chosen" in refused(rig, aspect="")


def test_a_style_that_is_not_in_the_owners_list_is_refused(rig):
    assert "no style called \"Oil\"" in refused(rig, style="Oil")


def test_style_endings_come_from_the_setting_and_a_line_without_a_colon_is_ignored(make_rig):
    rig = make_rig(settings={"style_presets": ["Noir: moody black and white", "nonsense",
                                               "Empty:"]})
    assert [p["name"] for p in rig.overview()["presets"]] == ["Noir"]
    rig.make("A street", style="Noir")
    assert rig.last_run()["full_prompt"] == "A street. moody black and white"


def test_the_page_can_poll_a_run_from_queued_to_done(rig):
    started = rig.core.call("make", {"prompt": "A kite", "aspect": "1:1", "count": 2})
    run_id = started["run"]["id"]
    seen = rig.core.call("status", {})
    assert seen["busy"] is True and seen["runs"][0]["state"] == "queued"
    assert seen["runs"][0]["made"] == 0 and seen["runs"][0]["want"] == 2
    rig.ctx.maker.drain()
    seen = rig.core.call("status", {})
    assert seen["busy"] is False
    assert (seen["runs"][0]["state"], seen["runs"][0]["made"]) == ("done", 2)
    assert seen["runs"][0]["said"] == "Made 2 pictures."
    rig.core.call("run.dismiss", {"id": run_id})
    assert rig.core.call("status", {})["runs"] == []


def test_the_page_is_nudged_when_a_picture_arrives(rig):
    rig.make("A kite")
    assert any(e["event"] == "imagery.changed" for e in rig.core.emitted)


def test_a_run_can_be_stopped_before_it_starts_and_nothing_is_asked(make_rig):
    rig = make_rig(provider="gemini")
    rig.service.on(GEMINI_URL, gemini_image())
    started = rig.core.call("make", {"prompt": "A kite", "aspect": "1:1", "count": 3})
    rig.core.call("run.stop", {"id": started["run"]["id"]})
    rig.ctx.maker.drain()
    run = rig.last_run()
    assert run["state"] == "stopped" and run["made"] == 0
    assert rig.core.fetched == [] and rig.overview()["today"]["used"] == 0


def test_a_run_stopped_between_pictures_keeps_the_ones_made(make_rig):
    rig = make_rig(provider="gemini")
    rig.service.on(GEMINI_URL, gemini_image())
    started = rig.core.call("make", {"prompt": "A kite", "aspect": "1:1", "count": 3})
    run_id = started["run"]["id"]
    original = rig.ctx.providers["gemini"].make

    def make_then_stop(request, cfg):
        got = original(request, cfg)
        rig.core.call("run.stop", {"id": run_id})
        return got
    rig.ctx.providers["gemini"].make = make_then_stop
    rig.ctx.maker.drain()
    run = rig.last_run()
    assert run["state"] == "stopped" and run["made"] == 1 and len(rig.pictures()) == 1


def test_only_a_few_requests_may_wait_at_once(rig):
    for n in range(5):
        rig.core.call("make", {"prompt": f"Kite {n}", "aspect": "1:1", "count": 1})
    assert "5 requests are already waiting" in refused(rig)
    rig.ctx.maker.drain()
    rig.core.call("make", {"prompt": "Kite again", "aspect": "1:1", "count": 1})


def test_a_run_left_unfinished_by_a_restart_is_marked_failed_with_a_sentence(tmp_path):
    first = Rig(tmp_path)
    first.core.call("make", {"prompt": "A kite", "aspect": "1:1", "count": 2})
    first.ctx.close()
    second = Rig(tmp_path)
    try:
        run = second.core.call("status", {})["runs"][0]
        assert run["state"] == "failed" and "restarted" in run["said"]
    finally:
        second.ctx.close()


def test_the_worker_thread_makes_the_pictures_while_the_call_returns_at_once(tmp_path):
    from imagery import config
    from imagery.program import build
    from imagery_rig import START, Clock
    from jimmyhq_app.testing import FakeCore

    core = FakeCore("imagery", data_dir=tmp_path, settings=config.defaults(),
                    permissions=["agents.tools"])
    app = build(transport=core, clock=Clock(START), threaded=True)
    app.run()
    ctx = app.imagery
    try:
        started = core.call("make", {"prompt": "A kite", "aspect": "1:1", "count": 2})
        assert started["run"]["state"] in ("queued", "working")  # the worker may already have it
        ctx.maker.wait()
        assert core.call("status", {})["runs"][0]["state"] == "done"
        assert core.call("pictures.list", {})["total"] == 2
    finally:
        ctx.close()
