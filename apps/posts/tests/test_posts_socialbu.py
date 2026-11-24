"""Booking through SocialBu, against canned answers only: one request for every account,
copies matched by the ids that come back, pictures uploaded first to SocialBu's own host,
moves and edits checked on each copy first, deletes of what is gone counted as done, and
every answer sorted into yes, no or no answer. Nothing is ever tried again by itself."""

from __future__ import annotations

import pytest
from jimmyhq_app import CallError
from posts.schedulers import socialbu
from posts.schedulers.socialbu import Answer, classify
from posts_rig import START
from posts_service import (
    BODY,
    COLLISION,
    DAY,
    HOUR,
    KEY,
    MAIN,
    SECOND,
    SLOT,
    Service,
    booked,
    copies,
    created,
    no_answer,
    queued,
    reply,
    upstream_post,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 200
HEIC = b"\0\0\0\x18ftypheic" + b"\0" * 200


@pytest.fixture
def sb(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu"}, secrets={"socialbu_key": KEY})
    rig.service = Service(rig.core)
    return rig


# --- booking ------------------------------------------------------------------------------


def test_one_request_books_the_post_on_every_account_of_its_page(sb):
    post = queued(sb)
    assert post["slot_ms"] == SLOT and sb.service.calls == []
    sb.service.on("POST", "/posts", created((500, 1001), (501, 1002)))
    sb.core.call("post.book", {"id": post["id"]})
    [body] = sb.service.sent("POST", "/posts")
    assert body == {"accounts": [1001, 1002], "publish_at": "2027-01-16 08:00:00",
                    "content": BODY}
    [(_, _, args)] = sb.service.calls
    assert args["headers"]["Authorization"] == f"Bearer {KEY}"
    assert args["timeout_s"] == 30


def test_upstream_posts_are_matched_to_accounts_by_the_ids_that_come_back_in_any_order(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", created((777, 1002), (555, 1001)))
    sb.core.call("post.book", {"id": post["id"]})
    assert copies(sb, post["id"]) == {"socialbu:1001": "555", "socialbu:1002": "777"}


def test_a_booking_that_returns_fewer_posts_than_accounts_is_not_called_booked(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", created((555, 1001)))
    after = sb.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "unsure"
    assert "1 of 2 accounts (Main)" in after["note"] and "Second" in after["note"]
    [copy] = sb.store.upstream(post["id"])
    assert (copy["account"], copy["external_id"], copy["state"]) == (
        "socialbu:1001", "555", "unconfirmed")
    assert sb.store.attempts(post["id"])[-1]["outcome"] == "unsure"


def test_a_200_that_says_it_did_not_succeed_is_not_called_booked(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", reply(200, {"success": False,
                                                "message": "The account is disconnected."}))
    after = sb.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and "The account is disconnected." in after["note"]
    assert sb.store.upstream(post["id"]) == []


def test_a_yes_without_any_id_is_unsure(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", reply(200, {"message": "Successfully saved to queue"}))
    assert sb.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


def test_a_server_error_is_no_answer_and_makes_the_booking_unsure(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", reply(502, text="<html>Bad gateway</html>"))
    assert sb.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


def test_a_second_try_is_refused_while_the_first_got_no_answer(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", no_answer())
    assert sb.core.call("post.book", {"id": post["id"]})["state"] == "unsure"
    sb.service.on("POST", "/posts", created((500, 1001), (501, 1002)))
    with pytest.raises(CallError, match="only a queued post can be booked. Nothing was sent"):
        sb.core.call("post.book", {"id": post["id"]})
    assert sb.service.asked() == [("POST", "/posts")]  # asked once, never again


def test_a_time_in_the_past_is_refused_before_anything_is_sent(sb):
    post = queued(sb)
    with pytest.raises(CallError, match="That time has passed. Choose a time in the future. "
                                        "Nothing was sent."):
        sb.core.call("post.book", {"id": post["id"], "slot_ms": START - HOUR})
    assert sb.service.calls == [] and sb.store.attempts(post["id"]) == []


def test_a_time_that_passes_while_pictures_upload_is_never_sent(sb):
    post = queued(sb)
    picture = sb.core.upload(PNG, "roof.png")
    sb.ctx.pictures_for = lambda ctx, post_id: [picture]
    sb.service.on("POST", "/upload_media_by_url", reply(200, {"success": True,
                                                              "upload_token": "tok-1"}))
    answer_as_service = sb.core._net_fetch  # noqa: SLF001

    def slow(args):  # the upload takes two days
        if args["url"].endswith("/upload_media_by_url"):
            sb.clock.advance(2 * DAY)
        return answer_as_service(args)
    sb.core._net_fetch = slow  # noqa: SLF001
    after = sb.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and "would publish it at once" in after["note"]
    assert ("POST", "/posts") not in sb.service.asked()


def test_two_posts_cannot_be_booked_in_the_same_minute_on_one_account(sb):
    first = booked(sb)
    other_page = sb.page(name="Kitchen notes", accounts=[MAIN])
    second = queued(sb, page=other_page, url="https://news.example.com/2027/kitchens")
    with pytest.raises(CallError, match="Another post of the same account goes out in that "
                                        "minute. Choose another time. Nothing was sent."):
        sb.core.call("post.book", {"id": second["id"], "slot_ms": first["slot_ms"] + 30_000})
    assert sb.service.calls == []


def test_the_services_own_refusal_of_a_collision_is_a_sentence_the_owner_can_act_on(sb):
    post = queued(sb)
    sb.service.on("POST", "/posts", reply(400, {"message": COLLISION}))
    after = sb.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued"
    assert "SocialBu already has a post in that minute" in after["note"]
    assert "Choose another minute, then book again." in after["note"]


def test_with_no_key_the_scheduler_says_so_and_nothing_is_sent(make_rig):
    rig = make_rig(settings={"scheduler": "socialbu"})
    rig.service = Service(rig.core)
    post = queued(rig)
    with pytest.raises(CallError, match="Posts has no SocialBu key. Type it in on Posts' "
                                        "Settings page"):
        rig.core.call("post.book", {"id": post["id"]})
    assert rig.service.calls == []


@pytest.mark.parametrize("accounts, says", [
    ([], "This brand has no SocialBu account."),
    ([{"scheduler": "socialbu", "account_id": "main-page", "label": "Main"}],
     "is not a number"),
])
def test_a_page_without_a_usable_socialbu_account_sends_nothing(sb, accounts, says):
    post = queued(sb, accounts=accounts)
    with pytest.raises(CallError, match=says):
        sb.core.call("post.book", {"id": post["id"]})
    assert sb.service.calls == []


# --- pictures -----------------------------------------------------------------------------


def test_pictures_are_uploaded_first_inside_the_request_to_socialbus_own_host(sb):
    post = queued(sb)
    one, two = sb.core.upload(PNG, "roof.png"), sb.core.upload(PNG + b"2", "panel.png")
    sb.ctx.pictures_for = lambda ctx, post_id: [one, two]
    sb.service.on("POST", "/upload_media_by_url",
                  reply(200, {"success": True, "upload_token": "tok-1"}),
                  reply(200, {"success": True, "upload_token": "tok-2"}))
    sb.service.on("POST", "/posts", created((500, 1001), (501, 1002)))
    assert sb.core.call("post.book", {"id": post["id"]})["state"] == "booked"
    assert sb.service.asked() == [("POST", "/upload_media_by_url")] * 2 + [("POST", "/posts")]
    uploads = sb.service.sent("POST", "/upload_media_by_url")
    assert uploads[0] == {"url": {"$file": one["ref"], "as": "data_url"}, "name": "roof.png"}
    assert {f["url"] for f in sb.core.sent_files} == {f"{socialbu.BASE}/upload_media_by_url"}
    assert sb.service.sent("POST", "/posts")[0]["existing_attachments"] == [
        {"upload_token": "tok-1"}, {"upload_token": "tok-2"}]


def test_a_failed_picture_upload_books_nothing_and_names_the_picture(sb):
    post = queued(sb)
    one, two = sb.core.upload(PNG, "roof.png"), sb.core.upload(PNG + b"2", "panel.png")
    sb.ctx.pictures_for = lambda ctx, post_id: [one, two]
    sb.service.on("POST", "/upload_media_by_url",
                  reply(200, {"success": True, "upload_token": "tok-1"}),
                  reply(400, {"message": "Unsupported file type."}))
    after = sb.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued"
    assert "The picture \"panel.png\" did not upload (Unsupported file type.)" in after["note"]
    assert "Nothing was booked" in after["note"]
    assert ("POST", "/posts") not in sb.service.asked()


def test_a_picture_over_the_size_limit_is_refused_in_a_sentence_before_anything_is_sent(
        make_rig):
    rig = make_rig(settings={"scheduler": "socialbu", "upload_max_mb": 1},
                   secrets={"socialbu_key": KEY})
    rig.service = Service(rig.core)
    post = queued(rig)
    big = rig.core.upload(PNG + b"\0" * (2 * 1024 * 1024), "wide.png")
    rig.ctx.pictures_for = lambda ctx, post_id: [big]
    after = rig.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and rig.service.calls == []
    assert "\"wide.png\" is 2.0 MB, more than the 1 MB Posts uploads" in after["note"]


def test_a_picture_socialbu_does_not_take_is_refused_before_anything_is_sent(sb):
    post = queued(sb)
    odd = sb.core.upload(HEIC, "phone.heic")
    sb.ctx.pictures_for = lambda ctx, post_id: [odd]
    after = sb.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and sb.service.calls == []
    assert "Use a JPEG, PNG, GIF or WebP picture instead." in after["note"]


# --- moves --------------------------------------------------------------------------------


def test_a_move_checks_each_copy_first_then_changes_the_time_on_each(sb):
    post = booked(sb)
    for pid, aid in ((500, 1001), (501, 1002)):
        sb.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid))
        sb.service.on("PATCH", f"/posts/{pid}", reply(200, {"success": True, "post": {}}))
    moved = sb.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert moved["slot_ms"] == SLOT + DAY and moved["out_of_step"] == 0
    assert sb.service.asked() == [("GET", "/posts/500"), ("GET", "/posts/501"),
                                  ("PATCH", "/posts/500"), ("PATCH", "/posts/501")]
    assert sb.service.sent("PATCH", "/posts/500") == [{"publish_at": "2027-01-17 08:00:00"}]


def test_a_move_that_lands_on_one_account_of_two_says_so(sb):
    post = booked(sb)
    for pid, aid in ((500, 1001), (501, 1002)):
        sb.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid))
    sb.service.on("PATCH", "/posts/500", reply(200, {"success": True, "post": {}}))
    sb.service.on("PATCH", "/posts/501", reply(422, {"message": "The given data was invalid."}))
    moved = sb.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert moved["out_of_step"] == 1 and moved["state"] == "booked"
    assert "reached SocialBu for only some accounts" in moved["note"]
    assert "reached account 1001 but not account 1002" in moved["note"]
    held = {u["account"]: u["slot_ms"] for u in sb.store.upstream(post["id"])}
    assert held == {"socialbu:1001": SLOT + DAY, "socialbu:1002": SLOT}


def test_a_copy_left_at_its_old_minute_still_blocks_that_minute_for_its_account(sb):
    post = booked(sb)
    for pid, aid in ((500, 1001), (501, 1002)):
        sb.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid))
    sb.service.on("PATCH", "/posts/500", reply(200, {"success": True, "post": {}}))
    sb.service.on("PATCH", "/posts/501", reply(422, {"message": "No."}))
    sb.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    sb.service.calls.clear()
    other_page = sb.page(name="Kitchen notes", accounts=[SECOND])
    other = queued(sb, page=other_page, url="https://news.example.com/2027/kitchens")
    with pytest.raises(CallError, match="Another post of the same account goes out in that "
                                        "minute"):
        sb.core.call("post.book", {"id": other["id"], "slot_ms": SLOT})
    assert sb.service.calls == []


def test_a_move_of_a_post_already_out_on_one_account_changes_nothing(sb):
    post = booked(sb)
    sb.service.on("GET", "/posts/500", upstream_post(500, 1001, published=True))
    sb.service.on("GET", "/posts/501", upstream_post(501, 1002))
    moved = sb.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert moved["slot_ms"] == SLOT and moved["out_of_step"] == 0
    assert "It already went out on account 1001. Nothing was changed." in moved["note"]
    assert all(m == "GET" for m, _ in sb.service.asked())


def test_a_move_to_a_minute_socialbu_already_holds_says_what_to_do(sb):
    post = booked(sb)
    for pid, aid in ((500, 1001), (501, 1002)):
        sb.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid))
        sb.service.on("PATCH", f"/posts/{pid}", reply(400, {"message": COLLISION}))
    moved = sb.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert moved["slot_ms"] == SLOT and "Choose another minute" in moved["note"]


# --- edits --------------------------------------------------------------------------------


def test_an_edit_sends_the_new_text_to_every_copy_and_keeps_its_pictures_exactly(sb):
    post = booked(sb)
    saved = [{"path": "media/abc/roof.png", "name": "roof.png", "type": "png",
              "mime": "image/png", "url": "https://cdn.example.com/roof.png", "size": 208}]
    for pid, aid in ((500, 1001), (501, 1002)):
        sb.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid, attachments=saved))
        sb.service.on("PATCH", f"/posts/{pid}", reply(200, {"success": True, "post": {}}))
    edited = sb.core.call("post.edit", {"id": post["id"], "body": BODY + " **Updated**."})
    assert edited["out_of_step"] == 0
    for pid in (500, 501):
        assert sb.service.sent("PATCH", f"/posts/{pid}") == [
            {"content": BODY + " Updated.", "existing_attachments": saved}]


def test_an_edit_of_a_post_that_shows_no_attachments_leaves_them_as_they_are(sb):
    post = booked(sb)
    for pid, aid in ((500, 1001), (501, 1002)):
        sb.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid))
        sb.service.on("PATCH", f"/posts/{pid}", reply(200, {"success": True, "post": {}}))
    sb.core.call("post.edit", {"id": post["id"], "body": BODY + " Again."})
    assert sb.service.sent("PATCH", "/posts/500") == [{"content": BODY + " Again."}]


def test_an_edit_that_the_service_refuses_leaves_the_post_saying_it_is_out_of_step(sb):
    post = booked(sb)
    for pid, aid in ((500, 1001), (501, 1002)):
        sb.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid))
        sb.service.on("PATCH", f"/posts/{pid}", reply(422, {"message": "Content too long."}))
    edited = sb.core.call("post.edit", {"id": post["id"], "body": BODY + " Longer."})
    assert edited["out_of_step"] == 1 and edited["state"] == "booked"
    assert edited["note"].startswith("This change did not reach SocialBu. Book again with "
                                     "this text, or change it there.")
    assert "Content too long." in edited["note"]


# --- cancelling ---------------------------------------------------------------------------


def test_deleting_something_already_gone_counts_as_done(sb):
    post = booked(sb)
    sb.service.on("DELETE", "/posts/500", reply(200, text=""))
    sb.service.on("DELETE", "/posts/501", reply(404, {"message": "Not found."}))
    after = sb.core.call("post.cancel", {"id": post["id"]})
    assert after["state"] == "declined" and after["note"] == "Taken off SocialBu."


def test_a_delete_socialbu_refuses_fails_closed_and_keeps_the_post_booked(sb):
    post = booked(sb)
    sb.service.on("DELETE", "/posts/500", reply(403, {"message": "The post is published."}))
    sb.service.on("DELETE", "/posts/501", reply(403, {"message": "The post is published."}))
    with pytest.raises(CallError, match="did not take it off"):
        sb.core.call("post.cancel", {"id": post["id"]})
    assert sb.store.post(post["id"])["state"] == "booked"


def test_a_delete_that_reaches_only_one_account_says_so_and_keeps_the_rest_booked(sb):
    post = booked(sb)
    sb.service.on("DELETE", "/posts/500", reply(200, text=""))
    sb.service.on("DELETE", "/posts/501", no_answer())
    with pytest.raises(CallError, match="took it off only some accounts"):
        sb.core.call("post.cancel", {"id": post["id"]})
    after = sb.store.post(post["id"])
    assert after["state"] == "booked" and after["out_of_step"] == 1
    states = {u["account"]: u["state"] for u in sb.store.upstream(post["id"])}
    assert states == {"socialbu:1001": "cancelled", "socialbu:1002": "booked"}


# --- the rules ----------------------------------------------------------------------------


@pytest.mark.parametrize("reply_, error, kind", [
    (reply(200, {"posts": []}), None, "yes"),
    (reply(200, {"success": False, "message": "No."}), None, "no"),
    (reply(400, {"message": "Bad."}), None, "no"),
    (reply(404, {"message": "Gone."}), None, "no"),
    (reply(429, text=""), None, "no"),
    (reply(408, text=""), None, "none"),
    (reply(500, text="oops"), None, "none"),
    (reply(503, text=""), None, "none"),
    (reply(302, text=""), None, "none"),
    (reply(200, text="<html>not json</html>"), None, "none"),
    (reply(200, text=""), None, "none"),
    (None, CallError("unavailable", "Timed out."), "none"),
    (None, CallError("too_large", "Answer too large."), "none"),
    (None, CallError("internal", "Broke."), "none"),
    (None, CallError("not_allowed", "Host not granted."), "no"),
    (None, CallError("quota", "Too many."), "no"),
    (None, OSError("Connection reset."), "none"),
])
def test_every_answer_is_a_definite_yes_a_definite_no_or_no_answer(reply_, error, kind):
    assert classify(reply_, error, KEY).kind == kind


def test_an_empty_yes_is_a_yes_only_where_an_empty_body_is_expected():
    assert classify(reply(200, text=""), None, KEY, empty_ok=True).kind == "yes"


def test_a_definite_no_carries_the_services_own_words():
    assert classify(reply(422, {"message": "The given data was invalid.", "errors": {}}),
                    None, KEY).said == "The given data was invalid."
    assert classify(reply(422, {"errors": {"content": ["Content is required."]}}), None,
                    KEY).said == "Content is required."


def test_times_are_sent_in_utc_as_the_service_writes_them():
    assert socialbu.to_service_time(SLOT) == "2027-01-16 08:00:00"
    for written in ("2027-01-16 08:00:00", "2027-01-16T08:00:00Z", "2027-01-16T08:00:00+00:00",
                    "2027-01-16T09:00:00+01:00"):
        assert socialbu.from_service_time(written) == SLOT
    assert socialbu.from_service_time("soon") is None and socialbu.from_service_time(None) is None


def test_copies_are_matched_by_account_and_strangers_or_copies_without_ids_are_left_out():
    accounts = [{"account": "socialbu:1001", "account_id": "1001", "label": "Main"},
                {"account": "socialbu:1002", "account_id": "1002", "label": "Second"}]
    got, missing = socialbu.match_copies([{"id": 9, "account_id": 1002},
                                          {"id": 8, "account_id": 4444},
                                          {"account_id": 1001}], accounts)
    assert got == [{"account": "socialbu:1002", "external_id": "9"}] and missing == ["Main"]


def test_a_collision_is_recognised_only_from_the_services_own_words():
    assert socialbu.collision_sentence(COLLISION)
    assert socialbu.collision_sentence("The given data was invalid.") is None


def test_an_account_id_is_a_number_or_nothing():
    assert socialbu.account_number(" 1001 ") == 1001
    assert socialbu.account_number("main") is None and socialbu.account_number("") is None


def test_the_attachments_sent_back_are_exactly_those_the_service_gave():
    saved = [{"path": "p/1.png", "secureKey": "abc"}]
    assert socialbu.kept_attachments({"attachments": saved}) == saved
    assert socialbu.kept_attachments({}) is None


def test_a_change_that_reaches_every_copy_some_or_none_is_summed_up_plainly():
    one = {"account": "socialbu:1001", "external_id": "500"}
    two = {"account": "socialbu:1002", "external_id": "501"}
    yes, no, none = Answer("yes"), Answer("no", said="No."), Answer("none", said="Timed out.")
    assert socialbu.summed([(one, yes), (two, yes)], "It").kind == "sent"
    assert socialbu.summed([(one, yes), (two, no)], "It").kind == "partial"
    assert socialbu.summed([(one, no), (two, no)], "It").kind == "refused"
    assert socialbu.summed([(one, no), (two, none)], "It").kind == "unsure"


def test_a_picture_problem_is_a_sentence_and_none_when_it_can_go():
    picture = {"ref": "uploads/abc.png", "name": "roof.png"}
    assert socialbu.picture_problem(picture, 100, 50) is None
    assert "no longer in Posts' storage" in socialbu.picture_problem(picture, None, 50)
    assert "more than the 50 MB" in socialbu.picture_problem(picture, 51 * socialbu.MB, 50)


def test_a_post_has_no_pictures_until_the_pictures_part_names_them(sb):
    from posts import booking  # noqa: PLC0415

    post = queued(sb)
    assert booking.pictures_of(sb.ctx, post) == ()
    sb.ctx.pictures_for = lambda ctx, post_id: [{"ref": "uploads/a.png", "name": "a.png"}]
    assert booking.pictures_of(sb.ctx, post) == ({"ref": "uploads/a.png", "name": "a.png"},)


def test_a_listed_posts_text_and_time_are_read_from_the_first_name_that_holds_them():
    items = [
        {"account_id": 1001, "content": "Plain words.", "publish_at": "2027-01-14 08:00:00"},
        {"account_id": 1001, "content": "", "text": "Under text.",
         "published_at": "2027-01-13 08:00:00"},
        {"account_id": 1001, "message": "Under message.", "created_at": "2027-01-12T08:00:00Z"},
        {"account_id": 1001, "caption": "   "},
        {"account_id": 9999, "content": "Someone else's."}]
    found, unreadable = socialbu.posts_read(items, {"1001"})
    assert [p["text"] for p in found] == ["Plain words.", "Under text.", "Under message."]
    assert [p["when_ms"] for p in found] == [
        socialbu.from_service_time("2027-01-14 08:00:00"),
        socialbu.from_service_time("2027-01-13 08:00:00"),
        socialbu.from_service_time("2027-01-12 08:00:00")]
    assert unreadable == 1
