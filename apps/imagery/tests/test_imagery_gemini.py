"""Gemini: the request it builds, the answers it reads, and the plain sentences it gives
when there is no picture. Every answer is a recorded shape; nothing reaches the network."""

from __future__ import annotations

import pytest
from imagery import png
from imagery.providers import ProviderError
from imagery.providers import gemini as g
from imagery_rig import GEMINI_URL, MODEL
from imagery_service import (
    GEMINI_HOST,
    KEY,
    b64,
    gemini_blocked_prompt,
    gemini_error,
    gemini_finished,
    gemini_image,
    no_answer,
    own_wait_ended,
    owner_size_ceiling,
    owner_wait_ceiling,
    reply,
    tiny_jpeg,
    tiny_png,
)
from jimmyhq_app import CallError


@pytest.fixture
def gem(make_rig):
    return make_rig(provider="gemini")


def failed(rig) -> str:
    run = rig.last_run()
    assert run["state"] == "failed" and run["made"] == 0, run
    return run["said"]


# --- the request ------------------------------------------------------------------------


def test_gemini_is_asked_with_the_documented_endpoint_header_and_body(gem):
    gem.service.on(GEMINI_URL, gemini_image())
    gem.make("A red kite over a hill", aspect="16:9")
    [sent] = gem.service.sent
    assert sent["url"] == f"https://{GEMINI_HOST}/v1beta/models/{MODEL}:generateContent"
    assert sent["method"] == "POST"
    assert sent["headers"]["x-goog-api-key"] == KEY
    assert sent["json"] == {
        "contents": [{"parts": [{"text": "A red kite over a hill"}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                             "imageConfig": {"aspectRatio": "16:9"}}}
    assert sent["timeout_s"] == 120


def test_a_picture_size_the_owner_chose_is_sent_in_capital_k(make_rig):
    rig = make_rig(provider="gemini", settings={"gemini_image_size": "2K"})
    rig.service.on(GEMINI_URL, gemini_image())
    rig.make()
    assert rig.service.sent[0]["json"]["generationConfig"]["imageConfig"] == {
        "aspectRatio": "1:1", "imageSize": "2K"}


def test_the_style_ending_is_added_to_the_end_of_the_description_that_is_sent(gem):
    gem.service.on(GEMINI_URL, gemini_image())
    gem.make("A harbour at dawn", style="Watercolour")
    text = gem.service.sent[0]["json"]["contents"][0]["parts"][0]["text"]
    assert text.startswith("A harbour at dawn. a soft watercolour painting")
    [row] = gem.pictures()
    assert row["prompt"] == "A harbour at dawn" and row["style"] == "Watercolour"


def test_the_model_name_is_a_setting_and_goes_into_the_address(make_rig):
    rig = make_rig(provider="gemini", settings={"gemini_model": "gemini-3-pro-image"})
    url = f"https://{GEMINI_HOST}/v1beta/models/gemini-3-pro-image:generateContent"
    rig.service.on(url, gemini_image())
    rig.make()
    assert rig.service.sent[0]["url"] == url
    assert rig.pictures()[0]["model"] == "gemini-3-pro-image"


def test_a_starting_picture_is_sent_as_inline_data_after_the_text_by_marker_not_bytes(gem):
    gem.service.on(GEMINI_URL, gemini_image())
    given = gem.core.upload(tiny_png(33), "mine.png")
    added = gem.core.call("reference.add", {"ref": given["ref"], "name": "mine.png"})
    gem.make("Add a red scarf", reference=added["id"])
    parts = gem.service.sent[0]["json"]["contents"][0]["parts"]
    assert parts[0] == {"text": "Add a red scarf"}
    assert parts[1] == {"inline_data": {"mime_type": "image/png",
                                        "data": {"$file": given["ref"], "as": "base64"}}}
    assert [f["ref"] for f in gem.core.sent_files] == [given["ref"]]
    assert gem.core.sent_files[0]["data"] == tiny_png(33)


def test_the_owners_own_picture_joins_the_gallery_and_is_marked_as_given(gem):
    given = gem.core.upload(tiny_png(34), "mine.png")
    added = gem.core.call("reference.add", {"ref": given["ref"], "name": "mine.png"})
    assert added["kind"] == "given" and added["prompt"] == "mine.png"
    assert [r["id"] for r in gem.pictures()] == [added["id"]]


# --- the answers ------------------------------------------------------------------------


def test_a_picture_in_inline_data_is_kept_with_its_prompt_model_and_service(gem):
    gem.service.on(GEMINI_URL, gemini_image(tiny_png(7), words="Here is your kite."))
    gem.make("A red kite over a hill")
    [row] = gem.pictures()
    assert (row["provider"], row["model"], row["aspect"]) == ("gemini", MODEL, "1:1")
    assert row["said"] == "Here is your kite."
    kept = next(p for p in gem.files() if p.name.endswith(".png"))
    assert kept.read_bytes() == tiny_png(7)


def test_both_spellings_of_inline_data_and_a_jpeg_are_read():
    for snake in (False, True):
        answer = gemini_image(tiny_jpeg(), mime="image/jpeg", snake=snake)
        import json
        made = g.read_answer(json.loads(answer["text"]))
        assert made.mime == "image/jpeg" and made.data == tiny_jpeg()


def test_the_type_comes_from_the_bytes_not_from_what_google_says():
    import json
    answer = gemini_image(tiny_png(), mime="image/jpeg")
    made = g.read_answer(json.loads(answer["text"]))
    assert made.mime == "image/png"


def test_bytes_that_are_not_a_picture_are_refused_and_nothing_is_kept(gem):
    gem.service.on(GEMINI_URL, gemini_image(b"<html>not a picture</html>"))
    gem.make()
    assert "not a PNG, JPEG or WebP" in failed(gem)
    assert gem.pictures() == [] and gem.files() == []


def test_a_prompt_blocked_by_googles_safety_filter_says_so_and_to_change_the_words(gem):
    gem.service.on(GEMINI_URL, gemini_blocked_prompt("PROHIBITED_CONTENT"))
    gem.make()
    said = failed(gem)
    assert "would not make this picture" in said and "PROHIBITED_CONTENT" in said
    assert "nothing was made" in said and "Change the words" in said


@pytest.mark.parametrize("reason", ["IMAGE_SAFETY", "SAFETY", "IMAGE_PROHIBITED_CONTENT",
                                    "BLOCKLIST", "IMAGE_RECITATION"])
def test_a_finish_reason_that_means_a_refusal_is_told_as_one(gem, reason):
    gem.service.on(GEMINI_URL, gemini_finished(reason))
    gem.make()
    assert "would not make this picture" in failed(gem)


def test_words_with_no_picture_are_told_with_what_was_said(gem):
    gem.service.on(GEMINI_URL, gemini_finished("STOP", "Which kind of kite do you mean?"))
    gem.make()
    said = failed(gem)
    assert "answered with words and no picture" in said
    assert "Which kind of kite do you mean?" in said


def test_an_answer_with_no_candidates_and_no_reason_is_told_plainly(gem):
    gem.service.on(GEMINI_URL, reply(200, {"usageMetadata": {}}))
    gem.make()
    assert "no picture and no reason" in failed(gem)


# --- the errors -------------------------------------------------------------------------


def test_a_bad_key_is_told_as_a_bad_key(gem):
    gem.service.on(GEMINI_URL, gemini_error(
        400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.",
        [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]))
    gem.make()
    said = failed(gem)
    assert "did not accept the key" in said and "Settings page" in said


def test_permission_denied_is_told_as_a_key_problem(gem):
    gem.service.on(GEMINI_URL, gemini_error(403, "PERMISSION_DENIED", "Project denied."))
    gem.make()
    assert "did not accept the key" in failed(gem)


def test_over_quota_is_told_as_over_quota_and_points_at_billing(gem):
    gem.service.on(GEMINI_URL, gemini_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded."))
    gem.make()
    said = failed(gem)
    assert "over its quota" in said and "billing" in said and "nothing was made" in said


def test_an_unknown_model_names_the_model_and_the_setting_to_change(gem):
    gem.service.on(GEMINI_URL, gemini_error(404, "NOT_FOUND", "models/x is not found"))
    gem.make()
    said = failed(gem)
    assert MODEL in said and "Gemini model" in said


@pytest.mark.parametrize("status,word", [(500, "INTERNAL"), (503, "UNAVAILABLE")])
def test_a_busy_service_is_told_as_busy_and_to_try_again(gem, status, word):
    gem.service.on(GEMINI_URL, gemini_error(status, word, "Overloaded."))
    gem.make()
    assert "busy or having trouble" in failed(gem)


def test_a_deadline_error_is_told_as_taking_too_long(gem):
    gem.service.on(GEMINI_URL, gemini_error(504, "DEADLINE_EXCEEDED", "Deadline."))
    gem.make()
    assert "took too long" in failed(gem)


def test_no_answer_at_all_is_told_as_one_that_could_not_be_reached(gem):
    gem.service.on(GEMINI_URL, no_answer())
    gem.make()
    said = failed(gem)
    assert "did not answer in time, or could not be reached" in said
    assert "check that it is running" in said


def test_a_wait_the_owners_limit_ended_names_the_setting_and_what_gemini_may_need(gem):
    gem.service.on(GEMINI_URL, owner_wait_ceiling(20))
    gem.make()
    said = failed(gem)
    assert "after 20 seconds" in said and "the most it lets an app wait" in said
    assert "\"Longest wait for an app's network request (seconds)\"" in said
    assert "Settings, Apps, advanced" in said and "to 180 or more" in said
    assert "may still have been charged" in said and "Wait for the service" not in said


def test_a_wait_imagerys_own_setting_ended_names_that_setting_instead(gem):
    gem.service.on(GEMINI_URL, own_wait_ended(60))
    gem.make()
    said = failed(gem)
    assert "longer than the 60 seconds Imagery waits" in said
    assert "\"Wait for the service for (seconds)\" in Imagery's advanced settings" in said
    assert "Longest wait" not in said


def test_an_answer_too_big_for_an_app_names_the_owners_limit_and_what_a_picture_may_need(gem):
    gem.service.on(GEMINI_URL, owner_size_ceiling(1024))
    gem.make()
    said = failed(gem)
    assert "larger than the 1 MB JimmyHQ lets an app be given" in said
    assert "none of it was kept" in said and "may still have been charged" in said
    assert "\"Largest network answer for an app (KB)\"" in said
    assert "Settings, Apps, advanced" in said and "20480 or more (20 MB)" in said


def test_a_request_that_was_charged_even_though_its_answer_was_too_big_still_counts(gem):
    gem.service.on(GEMINI_URL, owner_size_ceiling(1024))
    gem.make()
    assert gem.overview()["today"]["used"] == 1


def test_a_refusal_by_jimmyhq_to_reach_the_host_says_nothing_was_sent(gem):
    gem.service.on(GEMINI_URL, CallError("not_allowed", "Not granted."))
    gem.make()
    assert "did not let Imagery reach" in failed(gem)


def test_a_request_jimmyhq_refused_before_sending_is_not_counted_against_the_day(gem):
    gem.service.on(GEMINI_URL, CallError("not_allowed", "Not granted."))
    gem.make()
    assert gem.overview()["today"]["used"] == 0
    assert gem.store.number("SELECT COUNT(*) FROM attempt") == 0


def test_a_key_echoed_back_in_an_error_is_taken_out_of_every_sentence(gem):
    gem.service.on(GEMINI_URL, gemini_error(
        400, "INVALID_ARGUMENT", f"Bad request for key {KEY} in the request."))
    gem.make()
    run = gem.last_run()
    assert KEY not in run["said"] and KEY not in repr(run)
    assert KEY not in gem.wherever_it_could_be_written()


def test_the_key_is_in_no_stored_row_and_no_file_after_a_whole_run(gem):
    gem.service.on(GEMINI_URL, gemini_image(), gemini_error(429, "RESOURCE_EXHAUSTED", KEY))
    gem.make(count=2)
    blob = repr(gem.store.all("SELECT * FROM run")) + repr(gem.store.all("SELECT * FROM picture"))
    assert KEY not in blob
    assert KEY not in gem.wherever_it_could_be_written()
    for path in gem.files():
        assert KEY.encode() not in path.read_bytes()


def test_a_run_that_fails_part_way_keeps_what_was_made_and_says_how_many(gem):
    gem.service.on(GEMINI_URL, gemini_image(tiny_png(1)), gemini_image(tiny_png(2)),
                   gemini_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded."))
    gem.make(count=3)
    run = gem.last_run()
    assert run["state"] == "failed" and run["made"] == 2
    assert run["said"].startswith("Made 2 of 3.") and "over its quota" in run["said"]
    assert len(gem.pictures()) == 2


# --- before anything is sent ------------------------------------------------------------


def test_with_no_key_nothing_is_sent_and_the_owner_is_told_where_to_type_it(make_rig):
    rig = make_rig(provider="gemini", secrets={})
    with pytest.raises(CallError) as refused:
        rig.make()
    assert "no Gemini key" in refused.value.message and "nothing was sent" in refused.value.message
    assert rig.core.fetched == [] and rig.overview()["provider"]["problem"]


def test_a_shape_gemini_does_not_make_is_refused_before_anything_is_sent(make_rig):
    rig = make_rig(provider="gemini", settings={"aspects": ["1:1", "7:5"]})
    assert rig.overview()["provider"]["aspects"] == ["1:1"]
    with pytest.raises(CallError):
        rig.make(aspect="7:5")
    assert rig.core.fetched == []


def test_a_model_name_that_could_change_the_address_is_refused(make_rig):
    rig = make_rig(provider="gemini", settings={"gemini_model": "x/../../v1/other"})
    with pytest.raises(CallError) as refused:
        rig.make()
    assert "model name" in refused.value.message and rig.core.fetched == []


def test_a_starting_picture_is_refused_for_a_service_that_cannot_use_one(rig):
    given = rig.core.upload(tiny_png(35), "mine.png")
    added = rig.core.call("reference.add", {"ref": given["ref"], "name": "mine.png"})
    with pytest.raises(CallError) as refused:
        rig.make(reference=added["id"])
    assert "cannot start from a picture" in refused.value.message


def test_a_starting_picture_that_is_too_big_or_not_a_picture_is_refused_and_removed(gem):
    gem.core.settings["reference_max_mb"] = 0
    given = gem.core.upload(tiny_png(36), "mine.png")
    with pytest.raises(CallError) as refused:
        gem.core.call("reference.add", {"ref": given["ref"], "name": "mine.png"})
    assert "Nothing was added" in refused.value.message
    assert not (gem.core.data_dir / given["ref"]).exists() and gem.pictures() == []


def test_the_documented_error_words_are_each_handled_by_one_function():
    assert g.read_failure(401, None, "m", None).kind == "key"
    assert g.read_failure(402, None, "m", None).kind == "quota"
    assert g.read_failure(499, None, "m", None).kind == "timeout"
    assert g.read_failure(400, {"error": {"status": "FAILED_PRECONDITION", "message": "x"}},
                          "m", None).kind == "setup"
    assert g.read_failure(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad"}},
                          "m", None).kind == "other"
    assert isinstance(g.read_failure(418, "text", "m", None), ProviderError)


def test_the_body_never_holds_the_bytes_of_a_starting_picture():
    from imagery.providers import Reference, Request

    class Cfg:
        gemini_image_size = "default"

    body = g.build_body(Request("hi", "1:1", Reference("uploads/" + "a" * 32 + ".png",
                                                       "image/png")), Cfg())
    assert b64(tiny_png()) not in repr(body)
    assert png.kind_of(tiny_png()) == "png"
