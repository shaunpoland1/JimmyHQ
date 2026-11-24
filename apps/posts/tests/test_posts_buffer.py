"""Booking through Buffer, against canned answers only: one request for each channel, a text
post and nothing else, every answer sorted into yes, no or no answer (an ``errors`` array in
an HTTP 200 a no only for the codes the documentation calls a refusal), a booking that
reached some channels and not others kept as unsure, a booking with no answer found again
by reconcile, and one host ever asked. Nothing is ever tried again by itself.

The answers are written from Buffer's documentation, not from the service: nothing here
proves the real service answers this way."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import buffer_schema
import pytest
from jimmyhq_app import CallError
from posts.schedulers import Unavailable, buffer
from posts.schedulers.buffer import URL, Answer, classify, create_query, quoted
from posts_rig import APP_ROOT, START
from posts_service import BODY, DAY, HOUR, SLOT, queued, reply

KEY = "sk-test-SECRET-0123456789"
MINUTE = 60_000
MAIN = {"scheduler": "buffer", "account_id": "ch-1", "label": "Main"}
SECOND = {"scheduler": "buffer", "account_id": "ch-2", "label": "Second"}
THIRD = {"scheduler": "buffer", "account_id": "ch-3", "label": "Third"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 200
SLOT_TEXT = "2027-01-16T08:00:00.000Z"  # SLOT, as Buffer writes a time


# --- a stand-in for Buffer: one address, answers chosen by what is asked ---------------------


def operation(query: str) -> str:
    for name in ("createPost", "editPost", "deletePost"):
        if f"{name}(" in query:
            return name
    if "organizations" in query:
        return "organizations"
    return "posts" if "posts(" in query else "post"


class Buffer:
    """Canned answers by operation (``createPost``, ``editPost``, ``deletePost``, ``post``
    for one read, ``posts`` for the list, ``organizations``), in order; the last repeats. An
    operation with no answer gets no answer, as a timeout. Every query is first checked
    against the documented schema (``buffer_schema``): a name Buffer does not have fails."""

    def __init__(self, core: Any):
        self.core = core
        self.script: dict[str, list[Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.unscripted: list[str] = []
        self._as_core = core._net_fetch  # noqa: SLF001 - keeps the host and permission rules
        core._net_fetch = self._fetch  # noqa: SLF001

    def on(self, name: str, *answers: Any) -> None:
        self.script.setdefault(name, []).extend(answers)

    def _next(self, name: str, *, raising: bool = True) -> Any:
        queue = self.script.get(name)
        if not queue:
            self.unscripted.append(name)
            raise CallError("unavailable", "api.buffer.com took longer than 30 seconds to "
                                           "answer. Try again later.")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception) and raising:
            raise answer
        return answer

    def _fetch(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args["json"]["query"]) if args.get("json") else ""
        buffer_schema.check(query)  # only what Buffer's documentation names, or the test fails
        name = operation(query) if query else "?"
        self.calls.append((name, args))
        aliases = re.findall(r"\b(c\d+): post\(", query)
        answer = self._batch(aliases) if aliases else self._next(name)
        if isinstance(answer, Exception):
            raise answer
        self.core.responses[str(args["url"])] = answer
        return self._as_core(args)

    def _batch(self, aliases: list[str]) -> Any:
        """Several reads in one request: one scripted ``post`` answer for each alias, in
        order, joined. When any of them is not a plain post answer, the request fails as
        Buffer would fail it (an error beside the other posts, or that answer itself) and
        the scripted answers are put back, so the reads one at a time that follow get them
        in the same order."""
        queue = self.script.setdefault("post", [])
        before = list(queue)
        joined: dict[str, Any] = {}
        for alias in aliases:
            one = self._next("post", raising=False)
            body = {} if isinstance(one, Exception) or not one.get("text") else json.loads(
                one["text"])
            post = (body.get("data") or {}).get("post") if isinstance(body, dict) else None
            if isinstance(one, Exception) or one.get("status") != 200 or body.get("errors") \
                    or not isinstance(post, dict):
                queue[:] = before
                if isinstance(one, dict) and body.get("errors"):
                    return reply(200, {"data": {**joined, alias: None},
                                       "errors": [{**e, "path": [alias]}
                                                  for e in body["errors"]]})
                return one
            joined[alias] = post
        return data(**joined)

    def asked(self) -> list[str]:
        return [name for name, _ in self.calls]

    def queries(self, name: str) -> list[str]:
        return [a["json"]["query"] for n, a in self.calls if n == name]


def data(**fields: Any) -> dict[str, Any]:
    return reply(200, {"data": fields})


def made(post_id: str, channel: str = "ch-1", *, due: str = SLOT_TEXT,
         status: str = "scheduled") -> dict[str, Any]:
    return {"__typename": "PostActionSuccess",
            "post": {"id": post_id, "status": status, "dueAt": due, "channelId": channel}}


def created(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    """One create answer for each ``(post id, channel id)``, in order."""
    return [data(createPost=made(pid, channel)) for pid, channel in pairs]


def typed(field: str, kind: str, message: str) -> dict[str, Any]:
    return data(**{field: {"__typename": kind, "message": message}})


def errors(code: str | None, message: str, status: int = 200) -> dict[str, Any]:
    error: dict[str, Any] = {"message": message}
    if code:
        error["extensions"] = {"code": code}
    return reply(status, {"data": None, "errors": [error]})


def seen(post_id: str, *, status: str = "scheduled", due: str = SLOT_TEXT,
         error: str | None = None, metrics: list | None = None) -> dict[str, Any]:
    post: dict[str, Any] = {"id": post_id, "status": status, "dueAt": due, "channelId": "ch-1",
                            "error": {"message": error} if error else None}
    if metrics is not None:
        post["metrics"] = metrics
    return data(post=post)


def no_answer() -> CallError:
    return CallError("unavailable", "api.buffer.com took longer than 30 seconds to answer. "
                                    "Try again later.")


@pytest.fixture
def bf(make_rig):
    rig = make_rig(settings={"scheduler": "buffer"}, secrets={"buffer_key": KEY})
    rig.service = Buffer(rig.core)
    rig.accounts = [MAIN, SECOND]
    return rig


def post_for(rig, *accounts, url=None):
    return queued(rig, accounts=list(accounts or rig.accounts), url=url)


def booked(rig, *accounts):
    """A post booked on its channels as ``p-1``, ``p-2``…, with the record cleared."""
    post = post_for(rig, *accounts)
    channels = [a["account_id"] for a in (accounts or rig.accounts)]
    rig.service.on("createPost", *created(*((f"p-{n}", c) for n, c in enumerate(channels, 1))))
    done = rig.core.call("post.book", {"id": post["id"]})
    assert done["state"] == "booked"
    rig.service.script.clear()
    rig.service.calls.clear()
    return done


def copies(rig, post_id):
    return {u["account"]: u["external_id"] for u in rig.store.upstream(post_id)}


def run(rig, after_ms: int = 31 * MINUTE) -> str:
    rig.clock.advance(after_ms)
    return rig.core.run_job("reconcile")


# --- booking ---------------------------------------------------------------------------------


def test_one_request_for_each_channel_books_the_post_with_its_text_time_and_the_owners_key(bf):
    post = post_for(bf)
    bf.service.on("createPost", *created(("p-1", "ch-1"), ("p-2", "ch-2")))
    done = bf.core.call("post.book", {"id": post["id"]})
    assert done["state"] == "booked" and done["scheduler"] == "buffer"
    first, second = bf.service.queries("createPost")
    assert 'channelId: "ch-1"' in first and 'channelId: "ch-2"' in second
    for query in (first, second):
        assert f"text: {quoted(BODY)}" in query and f'dueAt: "{SLOT_TEXT}"' in query
        assert "mode: customScheduled" in query and "schedulingType: automatic" in query
    [(_, args), _] = bf.service.calls
    assert args["url"] == URL and args["method"] == "POST"
    assert args["headers"]["Authorization"] == f"Bearer {KEY}" and args["timeout_s"] == 30
    assert copies(bf, post["id"]) == {"buffer:ch-1": "p-1", "buffer:ch-2": "p-2"}
    assert [u["slot_ms"] for u in bf.store.upstream(post["id"])] == [SLOT, SLOT]


def test_a_posts_words_cannot_end_the_query_they_are_written_into(bf):
    nasty = 'He said "stop" \\ and then }\n) { deletePost(input: {id: "x"}) } 😀\ttab'
    query = create_query('ch-1" }) { x', nasty, SLOT)
    [literal] = re.findall(r"text: (\"(?:[^\"\\]|\\.)*\")", query)
    assert json.loads(literal) == nasty
    assert 'channelId: "ch-1\\" }) { x"' in query and query.count("createPost(") == 1


def test_a_time_is_written_as_iso_8601_in_utc_with_milliseconds():
    assert buffer.to_service_time(SLOT) == SLOT_TEXT
    assert buffer.from_service_time("2027-01-16T08:00:00.000Z") == SLOT
    assert buffer.from_service_time("2027-01-16T09:00:00+01:00") == SLOT
    assert buffer.from_service_time("tomorrow") is None and buffer.from_service_time(None) is None


def test_a_definite_no_for_the_first_channel_puts_the_post_back_in_the_queue(bf):
    post = post_for(bf)
    bf.service.on("createPost", typed("createPost", "InvalidInputError",
                                      "Instagram posts need a picture."))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued"
    assert "Buffer said no" in after["note"] and "Instagram posts need a picture." in after["note"]
    assert "not tried for Second" in after["note"] and bf.service.asked() == ["createPost"]
    assert bf.store.upstream(post["id"]) == []
    assert bf.store.attempts(post["id"])[-1]["outcome"] == "refused"


@pytest.mark.parametrize("kind", ["InvalidInputError", "LimitReachedError", "NotFoundError",
                                  "UnauthorizedError"])
def test_each_error_type_the_documentation_calls_user_fixable_is_a_definite_no(bf, kind):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", typed("createPost", kind, "Some words from Buffer."))
    assert bf.core.call("post.book", {"id": post["id"]})["state"] == "queued"


@pytest.mark.parametrize("kind", ["UnexpectedError", "RestProxyError", "VoidMutationError",
                                  "SomethingNew"])
def test_any_other_typed_error_is_no_answer_and_the_booking_is_unsure(bf, kind):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", typed("createPost", kind, "Something happened."))
    assert bf.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


@pytest.mark.parametrize("code", ["UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND",
                                  "RATE_LIMIT_EXCEEDED"])
def test_an_errors_array_in_an_http_200_is_a_no_only_for_the_codes_called_a_refusal(bf, code):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", errors(code, "Words from Buffer."))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and "Words from Buffer." in after["note"]


@pytest.mark.parametrize("code", ["UNEXPECTED", "SOMETHING_NEW", None])
def test_an_errors_array_with_any_other_code_or_none_is_no_answer(bf, code):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", errors(code, "Words from Buffer."))
    assert bf.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


def test_one_unlisted_code_among_the_listed_makes_the_answer_no_answer():
    reply_ = reply(200, {"data": None, "errors": [
        {"message": "a", "extensions": {"code": "FORBIDDEN"}},
        {"message": "b", "extensions": {"code": "UNEXPECTED"}}]})
    assert classify(reply_, None, KEY).kind == "none"


def test_a_post_made_with_an_error_beside_it_is_unsure_and_never_put_back_to_book_again(bf):
    post = post_for(bf, MAIN)
    both = reply(200, {"data": {"createPost": made("p-1")}, "errors": [
        {"message": "Not allowed.", "extensions": {"code": "FORBIDDEN"}}]})
    bf.service.on("createPost", both)
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "unsure" and "a result and an error together" in after["note"]
    assert classify(both, None, KEY).kind == "none"


def test_an_error_with_no_data_is_a_no_only_when_data_is_null_or_missing():
    for body in ({"data": None}, {}):
        answer = classify(reply(200, {**body, "errors": [
            {"message": "No.", "extensions": {"code": "FORBIDDEN"}}]}), None, KEY)
        assert answer.kind == "no", body
    empty = classify(reply(200, {"data": {"createPost": None}, "errors": [
        {"message": "No.", "extensions": {"code": "FORBIDDEN"}}]}), None, KEY)
    assert empty.kind == "no"  # every field null: nothing was made


@pytest.mark.parametrize("code, status", [("GRAPHQL_VALIDATION_FAILED", 200),
                                          ("GRAPHQL_PARSE_FAILED", 200),
                                          ("GRAPHQL_VALIDATION_FAILED", 400)])
def test_a_request_buffer_could_not_understand_is_a_loud_no_that_blames_posts(bf, caplog, code,
                                                                              status):
    caplog.set_level(logging.INFO)
    post = post_for(bf)
    bf.service.on("createPost", errors(code, 'Cannot query field "txt" on type "Post".',
                                       status))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued"
    assert after["note"] == f"Buffer said no: {buffer.NOT_UNDERSTOOD}"
    assert bf.service.asked() == ["createPost"]
    [loud] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "did not understand" in loud.getMessage() and "txt" in loud.getMessage()


def test_bad_user_input_is_not_taken_as_a_request_that_never_ran(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", errors("BAD_USER_INPUT", "That is not a channel."))
    assert bf.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


def test_a_rate_limit_is_a_no_that_says_how_long_buffer_asked_to_wait(bf):
    post = post_for(bf, MAIN)
    limited = errors("RATE_LIMIT_EXCEEDED", "Too many requests from this client.", 429)
    limited["headers"]["retry-after"] = "120"
    bf.service.on("createPost", limited)
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and "Buffer says to wait 120 seconds." in after["note"]


@pytest.mark.parametrize("answer", [
    reply(502, text="<html>Bad gateway</html>"), reply(500, {"errors": []}),
    reply(401, {"message": "no"}), reply(404, {}), reply(200, text="not json at all"),
    reply(200, {"data": None}), reply(200, {"data": {"createPost": None}}),
    reply(302, text=""), no_answer()])
def test_a_status_the_documentation_does_not_describe_or_an_unreadable_body_is_unsure(bf, answer):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", answer)
    assert bf.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


def test_a_yes_without_an_id_is_unsure(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", data(createPost={"__typename": "PostActionSuccess",
                                                 "post": {"status": "scheduled"}}))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "unsure" and "gave no id" in after["note"]


def test_a_yes_that_names_another_channel_is_unsure(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", *created(("p-1", "ch-9")))
    assert bf.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


def test_a_timeout_makes_the_booking_unsure_and_a_second_try_is_refused(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", no_answer())
    assert bf.core.call("post.book", {"id": post["id"]})["state"] == "unsure"
    bf.service.on("createPost", *created(("p-1", "ch-1")))
    with pytest.raises(CallError, match="only a queued post can be booked. Nothing was sent"):
        bf.core.call("post.book", {"id": post["id"]})
    assert bf.service.asked() == ["createPost"]  # asked once, never again


def test_nothing_is_tried_again_by_itself_when_time_passes(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", no_answer())
    bf.core.call("post.book", {"id": post["id"]})
    bf.clock.advance(DAY)
    bf.advance(5)
    assert bf.service.asked() == ["createPost"]


def test_a_booking_refused_on_the_second_channel_is_booked_on_the_first_and_lacks_the_rest(bf):
    post = post_for(bf, MAIN, SECOND, THIRD)
    bf.service.on("createPost", *created(("p-1", "ch-1")),
                  typed("createPost", "LimitReachedError", "That channel is full."))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "booked"
    assert "1 of 3 channels (Main)" in after["note"] and "That channel is full." in after["note"]
    assert "not tried for Third" in after["note"] and "Book the rest" in after["note"]
    assert bf.service.asked() == ["createPost", "createPost"]  # the third was never asked
    [copy] = bf.store.upstream(post["id"])
    assert (copy["account"], copy["external_id"], copy["state"]) == (
        "buffer:ch-1", "p-1", "booked")
    assert bf.store.attempts(post["id"])[-1]["outcome"] == "sent"
    from posts import booking  # noqa: PLC0415

    assert booking.lacking_accounts(bf.ctx, after) == ["buffer:ch-2", "buffer:ch-3"]


def test_a_booking_that_got_no_answer_for_the_second_channel_says_it_may_be_booked_there_too(bf):
    post = post_for(bf, MAIN, SECOND)
    bf.service.on("createPost", *created(("p-1", "ch-1")), no_answer())
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "unsure" and "It may be booked there too." in after["note"]
    assert [u["external_id"] for u in bf.store.upstream(post["id"])] == ["p-1"]


@pytest.mark.parametrize("status, words", [
    ("draft", "Buffer made it as a draft there: finish it on Buffer or remove it."),
    ("needs_approval", "Buffer made it as waiting for approval there: finish it on Buffer or "
                       "remove it."),
    (None, "look at it on Buffer")])
def test_a_post_buffer_made_as_a_draft_is_unsure_and_its_copy_is_kept(bf, status, words):
    post = post_for(bf, MAIN, SECOND)
    answer = made("p-1", status=status)
    if status is None:
        del answer["post"]["status"]
    bf.service.on("createPost", data(createPost=answer))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "unsure" and words in after["note"]
    assert bf.service.asked() == ["createPost"]  # the second channel was not tried
    [copy] = bf.store.upstream(post["id"])
    assert (copy["external_id"], copy["state"]) == ("p-1", "unconfirmed")


def test_a_draft_made_for_the_second_channel_is_never_called_booked(bf):
    post = post_for(bf, MAIN, SECOND)
    bf.service.on("createPost", data(createPost=made("p-1")),
                  data(createPost=made("p-2", "ch-2", status="draft")))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "unsure" and "as a draft there" in after["note"]
    assert "It may be booked there too." not in after["note"]
    assert {u["external_id"] for u in bf.store.upstream(post["id"])} == {"p-1", "p-2"}


def test_a_booking_with_a_picture_is_refused_before_anything_is_sent(bf):
    post = post_for(bf, MAIN)
    picture = bf.core.upload(PNG, "roof.png")
    bf.ctx.pictures_for = lambda ctx, post_id: [picture]
    after = bf.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and "Buffer bookings are text only for now" in after["note"]
    assert "Nothing was booked." in after["note"]
    assert bf.service.calls == [] and bf.core.sent_files == []
    assert bf.store.attempts(post["id"])[-1]["outcome"] == "refused"


def test_a_time_that_passes_before_it_is_sent_is_never_sent(bf):
    page = bf.page(accounts=[MAIN])
    post = queued(bf, page=page)
    scheduler = bf.ctx.schedulers["buffer"]
    outcome = scheduler.book(buffer.Booking(post["id"], BODY, START - HOUR,
                                            [{"account": "buffer:ch-1", "account_id": "ch-1",
                                              "label": "Main"}]))
    assert outcome.kind == "refused" and "Nothing was booked." in outcome.detail
    assert bf.service.calls == []


def test_without_a_key_nothing_is_sent_and_the_sentence_says_where_to_put_it(make_rig):
    rig = make_rig(settings={"scheduler": "buffer"})
    rig.service = Buffer(rig.core)
    post = queued(rig, accounts=[MAIN])
    with pytest.raises(CallError, match="Posts has no Buffer key. Type it in on Posts' "
                                        "Settings page under Apps, then book again. Nothing "
                                        "was sent."):
        rig.core.call("post.book", {"id": post["id"]})
    assert rig.service.calls == []
    assert buffer.NO_KEY == ("Posts has no Buffer key, so nothing was sent. Type it in on "
                             "Posts' Settings page under Apps, then try again.")


def test_a_page_with_no_buffer_channel_is_told_to_add_one(bf):
    post = queued(bf, accounts=[{"scheduler": "socialbu", "account_id": "5", "label": "Elsewhere"}])
    with pytest.raises(CallError, match="This brand has no Buffer channel. Add one under Brands"):
        bf.core.call("post.book", {"id": post["id"]})
    assert bf.service.calls == []


def test_the_channels_booked_are_the_pages_buffer_accounts_and_no_others(bf):
    post = queued(bf, accounts=[MAIN, {"scheduler": "socialbu", "account_id": "5", "label": "X"},
                                SECOND])
    bf.service.on("createPost", *created(("p-1", "ch-1"), ("p-2", "ch-2")))
    bf.core.call("post.book", {"id": post["id"]})
    assert len(bf.service.queries("createPost")) == 2


# --- the key stays out of everything ---------------------------------------------------------


def test_the_key_is_never_shown_even_when_buffer_writes_it_back(bf, caplog):
    caplog.set_level(logging.DEBUG)
    post = post_for(bf, MAIN)
    bf.service.on("createPost", typed("createPost", "InvalidInputError",
                                      f"Bad token {KEY} for this request."))
    after = bf.core.call("post.book", {"id": post["id"]})
    assert "[your key]" in after["note"]
    everything = json.dumps([bf.store.all("SELECT * FROM attempt"), bf.core.events,
                             after], default=str) + caplog.text
    assert KEY not in everything


# --- cancel ----------------------------------------------------------------------------------


def test_cancelling_a_booked_post_deletes_each_copy_and_declines_the_post(bf):
    post = booked(bf)
    bf.service.on("post", seen("p-1"), seen("p-2"))
    bf.service.on("deletePost", data(deletePost={"__typename": "DeletePostSuccess",
                                                 "id": "p-1"}),
                  data(deletePost={"__typename": "DeletePostSuccess", "id": "p-2"}))
    done = bf.core.call("post.cancel", {"id": post["id"]})
    assert done["state"] == "declined" and "Taken off Buffer." in done["note"]
    assert 'id: "p-1"' in bf.service.queries("deletePost")[0]
    assert {u["state"] for u in bf.store.upstream(post["id"])} == {"cancelled"}


def test_cancelling_a_post_buffer_no_longer_has_counts_as_done(bf):
    post = booked(bf)
    bf.service.on("post", errors("NOT_FOUND", "Post not found."))
    done = bf.core.call("post.cancel", {"id": post["id"]})
    assert done["state"] == "declined" and "deletePost" not in bf.service.asked()


def test_a_delete_buffer_says_found_nothing_counts_as_done(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1"))
    bf.service.on("deletePost", errors("NOT_FOUND", "Post not found."))
    assert bf.core.call("post.cancel", {"id": post["id"]})["state"] == "declined"


def test_a_copy_that_already_went_out_is_not_deleted_and_the_post_stays_booked(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1", status="sent"))
    with pytest.raises(CallError, match="already went out"):
        bf.core.call("post.cancel", {"id": post["id"]})
    assert "deletePost" not in bf.service.asked()
    assert bf.store.post(post["id"])["state"] == "booked"


def test_a_cancel_that_reached_one_copy_and_not_the_other_says_which(bf):
    post = booked(bf)
    bf.service.on("post", seen("p-1"), seen("p-2"))
    bf.service.on("deletePost", data(deletePost={"__typename": "DeletePostSuccess",
                                                 "id": "p-1"}),
                  typed("deletePost", "VoidMutationError", "Could not delete."))
    with pytest.raises(CallError, match="took it off only some accounts"):
        bf.core.call("post.cancel", {"id": post["id"]})
    after = bf.store.post(post["id"])
    assert after["state"] == "booked" and after["out_of_step"] == 1
    assert {u["account"]: u["state"] for u in bf.store.upstream(post["id"])} == {
        "buffer:ch-1": "cancelled", "buffer:ch-2": "booked"}


def test_a_delete_with_no_answer_leaves_the_post_booked_and_says_so(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1"))
    bf.service.on("deletePost", no_answer())
    with pytest.raises(CallError, match="did not take it off"):
        bf.core.call("post.cancel", {"id": post["id"]})
    assert bf.store.post(post["id"])["state"] == "booked"


# --- move and edit ---------------------------------------------------------------------------


def test_moving_a_booked_post_edits_each_copy_to_the_new_time_after_reading_it(bf):
    post = booked(bf)
    later = SLOT + DAY
    bf.service.on("post", seen("p-1"), seen("p-2"), seen("p-1"), seen("p-2"))
    bf.service.on("editPost", data(editPost=made("p-1", due="2027-01-17T08:00:00.000Z")),
                  data(editPost=made("p-2", "ch-2", due="2027-01-17T08:00:00.000Z")))
    done = bf.core.call("post.move", {"id": post["id"], "slot_ms": later})
    assert done["slot_ms"] == later and not done["out_of_step"]
    assert bf.service.asked()[:2] == ["post", "post"]  # every copy read before any change
    first = bf.service.queries("editPost")[0]
    assert 'id: "p-1"' in first and 'dueAt: "2027-01-17T08:00:00.000Z"' in first
    assert {u["slot_ms"] for u in bf.store.upstream(post["id"])} == {later}


def test_a_move_buffer_answers_with_another_time_is_not_called_done(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1"))
    bf.service.on("editPost", data(editPost=made("p-1", due=SLOT_TEXT)))  # unchanged
    done = bf.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert done["out_of_step"] == 1 and "no clear answer" in done["note"]


def test_a_move_of_a_copy_that_already_went_out_changes_nothing(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1", status="sent"))
    done = bf.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert "already went out" in done["note"] and "editPost" not in bf.service.asked()


def test_a_move_of_a_copy_buffer_is_sending_changes_nothing(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1", status="sending"))
    done = bf.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert "is sending the copy for" in done["note"] and "editPost" not in bf.service.asked()


def test_a_copy_buffer_is_sending_is_not_deleted_and_the_post_stays_booked(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1", status="sending"))
    with pytest.raises(CallError, match="sending it right now"):
        bf.core.call("post.cancel", {"id": post["id"]})
    assert "deletePost" not in bf.service.asked()
    assert bf.store.post(post["id"])["state"] == "booked"


def test_a_move_of_a_copy_turned_into_a_draft_changes_nothing(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1", status="draft"))
    done = bf.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert "no longer a scheduled post" in done["note"] and "editPost" not in bf.service.asked()


def test_editing_a_booked_posts_text_sends_only_the_text_to_each_copy(bf):
    post = booked(bf)
    bf.service.on("post", seen("p-1"), seen("p-2"))
    bf.service.on("editPost", data(editPost=made("p-1")), data(editPost=made("p-2", "ch-2")))
    done = bf.core.call("post.edit", {"id": post["id"], "body": "New words for the post."})
    assert done["state"] == "booked" and not done["out_of_step"]
    for query in bf.service.queries("editPost"):
        assert 'text: "New words for the post."' in query
        assert "dueAt:" not in query and "assets:" not in query


def test_an_edit_buffer_refuses_is_kept_here_and_says_it_did_not_reach_buffer(bf):
    post = booked(bf, MAIN)
    bf.service.on("post", seen("p-1"))
    bf.service.on("editPost", typed("editPost", "InvalidInputError",
                                    "That is longer than 280 characters."))
    done = bf.core.call("post.edit", {"id": post["id"], "body": "New words for the post."})
    assert done["out_of_step"] == 1 and "did not reach Buffer" in done["note"]
    assert "280 characters" in done["note"]


def test_moving_a_queued_post_asks_buffer_nothing(bf):
    post = post_for(bf, MAIN)
    bf.core.call("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    assert bf.service.calls == []


# --- what Buffer says about a booked post ----------------------------------------------------


def copy_of(account="buffer:ch-1", external_id="p-1"):
    return {"account": account, "external_id": external_id}


def test_each_status_buffer_lists_is_told_in_the_apps_own_words(bf):
    scheduler = bf.ctx.schedulers["buffer"]
    cases = [("scheduled", "scheduled"), ("sending", "scheduled"), ("sent", "published"),
             ("error", "failed"), ("draft", "gone"), ("needs_approval", "gone")]
    for given, state in cases:
        bf.service.script.clear()
        bf.service.on("post", seen("p-1", status=given, error="Channel disconnected."
                                   if given == "error" else None))
        bf.service.on("post", seen("p-1", status=given, metrics=[]))
        [one] = scheduler.status(1, [copy_of()]).copies
        assert one["state"] == state, given
        assert one["slot_ms"] == SLOT
    bf.service.script.clear()
    bf.service.on("post", seen("p-1", status="error", error="Channel disconnected."))
    [failed] = scheduler.status(1, [copy_of()]).copies
    assert failed["said"] == "Channel disconnected."


def test_a_post_buffer_does_not_know_before_its_time_is_gone(bf):
    bf.service.on("post", errors("NOT_FOUND", "Post not found."))
    [one] = bf.ctx.schedulers["buffer"].status(1, [{**copy_of(), "slot_ms": SLOT}]).copies
    assert one["state"] == "gone"


@pytest.mark.parametrize("slot", [START - HOUR, None])
def test_a_post_buffer_does_not_know_once_its_time_passed_is_lost_not_gone(bf, slot):
    bf.service.on("post", errors("NOT_FOUND", "Post not found."))
    [one] = bf.ctx.schedulers["buffer"].status(1, [{**copy_of(), "slot_ms": slot}]).copies
    assert one["state"] == "lost"


def test_a_booked_post_not_found_after_its_time_says_it_may_have_gone_out(bf):
    post = booked(bf)
    bf.clock.advance(DAY + HOUR)  # past its time
    bf.service.on("post", errors("NOT_FOUND", "Post not found."), seen("p-2", status="sent"))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "missing" and "may have gone out" in after["note"]
    assert "before you book it again" in after["note"]
    from posts import screen  # noqa: PLC0415

    trouble = screen.trouble(bf.ctx, after, "Buffer")
    assert trouble["kind"] == "lost"
    assert [a["call"] for a in trouble["actions"]] == ["post.book_again", "post.cancel"]
    assert trouble["actions"][0]["kind"] == "quiet"  # never the first choice
    assert "createPost" not in bf.service.asked()


@pytest.mark.parametrize("answer", [no_answer(), reply(502, text="oops"),
                                    errors("UNEXPECTED", "Server error."),
                                    seen("p-1", status="a status from the future")])
def test_a_status_that_cannot_be_told_changes_nothing(bf, answer):
    bf.service.on("post", answer)
    assert bf.ctx.schedulers["buffer"].status(1, [copy_of()]).known is False


def test_a_sent_posts_counts_are_kept_as_figures_and_percentages_are_left_out(bf):
    bf.service.on("post", seen("p-1", status="sent", metrics=[
                      {"type": "reactions", "value": 12.0, "unit": "count"},
                      {"type": "impressions", "value": 300, "unit": "count"},
                      {"type": "engagementRate", "value": 4.2, "unit": "percentage"}]))
    [one] = bf.ctx.schedulers["buffer"].status(1, [copy_of()]).copies
    assert one["figures"] == {"reactions": 12.0, "impressions": 300.0}


def test_a_post_that_went_out_is_marked_sent_by_reconcile(bf):
    post = booked(bf)
    bf.service.on("post", seen("p-1", status="sent"), seen("p-2", status="sent"),
                  seen("p-1", status="sent", metrics=[]))
    run(bf)
    assert bf.store.post(post["id"])["state"] == "went_out"


def test_a_time_changed_on_buffer_is_picked_up_by_reconcile(bf):
    post = booked(bf)
    moved = "2027-01-16T10:30:00.000Z"
    bf.service.on("post", seen("p-1", due=moved), seen("p-2", due=moved))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["slot_ms"] == SLOT + 2 * HOUR + 30 * MINUTE and "changed on Buffer" in after[
        "note"]


def test_a_network_blip_during_reconcile_changes_nothing(bf):
    post = booked(bf)
    before = bf.store.post(post["id"])
    bf.service.on("post", no_answer())
    run(bf)
    assert bf.store.post(post["id"])["state"] == before["state"] == "booked"


# --- the list, and finding a booking that got no answer ---------------------------------------


def listing(*nodes: tuple, more: bool = False, cursor: str = "c1") -> dict:
    """A list answer of ``(id, channel, due)`` nodes, each with the post's text (``BODY``
    unless a fourth item gives another)."""
    return data(posts={"edges": [{"node": {"id": n[0], "channelId": n[1], "dueAt": n[2],
                                           "status": "scheduled",
                                           "text": n[3] if len(n) > 3 else BODY}}
                                 for n in nodes],
                       "pageInfo": {"hasNextPage": more, "endCursor": cursor}})


ORGS = data(account={"organizations": [{"id": "org-1"}]})


def test_scheduled_lists_every_page_of_every_organization_by_channel_and_time(bf):
    bf.service.on("organizations", data(account={"organizations": [{"id": "org-1"},
                                                                    {"id": "org-2"}]}))
    bf.service.on("posts",
                  listing(("a", "ch-1", SLOT_TEXT), more=True, cursor="next-1"),
                  listing(("b", "ch-2", SLOT_TEXT)),
                  listing(("c", "ch-1", "2027-01-18T08:00:00.000Z")))
    held = [{k: h[k] for k in ("account", "external_id", "slot_ms")}
            for h in bf.ctx.schedulers["buffer"].scheduled()]
    assert held == [
        {"account": "buffer:ch-1", "external_id": "a", "slot_ms": SLOT},
        {"account": "buffer:ch-2", "external_id": "b", "slot_ms": SLOT},
        {"account": "buffer:ch-1", "external_id": "c", "slot_ms": SLOT + 2 * DAY}]
    queries = bf.service.queries("posts")
    assert 'organizationId: "org-1"' in queries[0] and 'after: "next-1"' in queries[1]
    assert 'organizationId: "org-2"' in queries[2]
    assert "status: [scheduled, sending]" in queries[0]


@pytest.mark.parametrize("failure", [no_answer(), reply(502, text="oops"),
                                     errors("UNEXPECTED", "Server error.")])
def test_a_list_that_fails_on_any_page_is_never_taken_as_the_whole(bf, failure):
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("a", "ch-1", SLOT_TEXT), more=True), failure)
    with pytest.raises(Unavailable):
        bf.ctx.schedulers["buffer"].scheduled()


def test_a_list_that_never_ends_is_not_trusted(bf):
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("a", "ch-1", SLOT_TEXT), more=True))
    with pytest.raises(Unavailable, match="ran past"):
        bf.ctx.schedulers["buffer"].scheduled()
    assert len(bf.service.queries("posts")) == buffer.LIST_MOST_PAGES


def test_a_key_that_lists_no_organization_is_not_taken_as_an_empty_list(bf):
    bf.service.on("organizations", data(account={"organizations": []}))
    with pytest.raises(Unavailable):
        bf.ctx.schedulers["buffer"].scheduled()


def test_scheduled_without_a_key_says_so(make_rig):
    rig = make_rig(settings={"scheduler": "buffer"})
    with pytest.raises(Unavailable, match="no Buffer key"):
        rig.ctx.schedulers["buffer"].scheduled()


def unsure_booking(bf):
    post = post_for(bf)
    bf.service.on("createPost", *created(("p-1", "ch-1")), no_answer())
    bf.core.call("post.book", {"id": post["id"]})
    bf.service.script.clear()
    bf.service.calls.clear()
    return post


def test_an_unsure_booking_found_on_buffer_for_every_channel_is_booked(bf):
    post = unsure_booking(bf)
    assert bf.store.post(post["id"])["state"] == "unsure"
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("p-1", "ch-1", SLOT_TEXT), ("p-2", "ch-2", SLOT_TEXT)))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "booked" and "found it on Buffer" in after["note"]
    assert copies(bf, post["id"]) == {"buffer:ch-1": "p-1", "buffer:ch-2": "p-2"}
    assert "createPost" not in bf.service.asked()  # looked for, never sent again


def test_an_unsure_booking_on_no_channel_while_its_time_is_ahead_may_be_booked_again(bf):
    post = post_for(bf)
    bf.service.on("createPost", no_answer())
    bf.core.call("post.book", {"id": post["id"]})
    bf.service.script.clear()
    bf.service.calls.clear()
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("other", "ch-1", "2027-01-20T08:00:00.000Z")))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "queued" and "it is not there" in after["note"]
    assert "createPost" not in bf.service.asked()


def test_a_partly_booked_post_not_found_for_the_rest_is_booked_on_the_channel_that_has_it(bf):
    post = unsure_booking(bf)  # ch-1 made for certain, ch-2 no answer
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("p-1", "ch-1", SLOT_TEXT)))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "booked" and "but not for Second" in after["note"]
    assert copies(bf, post["id"]) == {"buffer:ch-1": "p-1"}
    assert "createPost" not in bf.service.asked()


def trouble_of(rig, post_id):
    from posts import screen  # noqa: PLC0415

    return screen.trouble(rig.ctx, rig.store.post(post_id), "Buffer")


def created_for(rig) -> list[str]:
    """The channel of each createPost asked, in order."""
    return [re.search(r'channelId: "([^"]+)"', q).group(1)
            for q in rig.service.queries("createPost")]


def test_book_the_rest_books_only_the_channels_the_post_lacks(bf):
    post = post_for(bf, MAIN, SECOND, THIRD)
    bf.service.on("createPost", *created(("p-1", "ch-1")),
                  typed("createPost", "LimitReachedError", "That channel is full."))
    bf.core.call("post.book", {"id": post["id"]})
    trouble = trouble_of(bf, post["id"])
    assert trouble["kind"] == "partial"
    assert trouble["actions"][0] == {**trouble["actions"][0], "call": "post.book",
                                     "label": "Book the rest (Second and Third)"}
    bf.service.script.clear()
    bf.service.calls.clear()
    bf.service.on("createPost", *created(("p-2", "ch-2"), ("p-3", "ch-3")))
    done = bf.core.call("post.book", {"id": post["id"]})
    assert done["state"] == "booked" and created_for(bf) == ["ch-2", "ch-3"]
    assert copies(bf, post["id"]) == {"buffer:ch-1": "p-1", "buffer:ch-2": "p-2",
                                      "buffer:ch-3": "p-3"}
    assert trouble_of(bf, post["id"]) is None
    with pytest.raises(CallError, match="booked on every one of its accounts already"):
        bf.core.call("post.book", {"id": post["id"]})


def test_book_the_rest_is_not_offered_once_the_posts_time_has_passed(bf):
    post = post_for(bf, MAIN, SECOND)
    bf.service.on("createPost", *created(("p-1", "ch-1")),
                  typed("createPost", "LimitReachedError", "That channel is full."))
    bf.core.call("post.book", {"id": post["id"]})
    bf.clock.advance(2 * DAY)
    assert [a["call"] for a in trouble_of(bf, post["id"])["actions"]] == ["post.cancel"]


def test_the_rest_is_booked_at_the_posts_own_time_only(bf):
    post = post_for(bf, MAIN, SECOND)
    bf.service.on("createPost", *created(("p-1", "ch-1")),
                  typed("createPost", "LimitReachedError", "That channel is full."))
    bf.core.call("post.book", {"id": post["id"]})
    with pytest.raises(CallError, match="booked at the post's own time"):
        bf.core.call("post.book", {"id": post["id"], "slot_ms": SLOT + DAY})


def test_a_refusal_of_the_rest_leaves_the_post_booked_where_it_was(bf):
    post = post_for(bf, MAIN, SECOND)
    bf.service.on("createPost", *created(("p-1", "ch-1")),
                  typed("createPost", "LimitReachedError", "That channel is full."))
    bf.core.call("post.book", {"id": post["id"]})
    bf.service.script.clear()
    bf.service.on("createPost", typed("createPost", "LimitReachedError", "Still full."))
    done = bf.core.call("post.book", {"id": post["id"]})
    assert done["state"] == "booked" and "said no to the rest (Second)" in done["note"]
    assert copies(bf, post["id"]) == {"buffer:ch-1": "p-1"}


def test_with_a_copy_booked_for_certain_i_checked_is_about_the_rest_and_never_requeues(bf):
    post = unsure_booking(bf)  # ch-1 made for certain, ch-2 no answer
    trouble = trouble_of(bf, post["id"])
    assert trouble["kind"] == "partial"
    assert [a["label"] for a in trouble["actions"]] == ["I checked: it is not on Second"]
    assert "I checked, it isn't there" not in bf.store.post(post["id"])["note"]
    after = bf.core.call("post.clear", {"id": post["id"]})
    assert after["state"] == "booked" and "Book the rest" in after["note"]
    bf.service.on("createPost", *created(("p-2", "ch-2")))
    done = bf.core.call("post.book", {"id": post["id"]})
    assert done["state"] == "booked" and created_for(bf) == ["ch-2"]  # never ch-1 again


def test_with_no_copy_known_i_checked_says_it_means_on_none_of_its_accounts(bf):
    post = post_for(bf)
    bf.service.on("createPost", no_answer())
    after = bf.core.call("post.book", {"id": post["id"]})
    assert "it is on none of them" in after["note"]
    trouble = trouble_of(bf, post["id"])
    assert trouble["kind"] == "no_answer"
    assert [a["label"] for a in trouble["actions"]] == ["I checked: it is on none of them"]
    assert "every account" in trouble["actions"][0]["ask"]
    assert bf.core.call("post.clear", {"id": post["id"]})["state"] == "queued"


def test_a_draft_copy_offers_no_way_to_book_again_until_buffers_list_settles_it(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", data(createPost=made("p-1", status="draft")))
    bf.core.call("post.book", {"id": post["id"]})
    assert trouble_of(bf, post["id"])["actions"] == []
    with pytest.raises(CallError, match="named a copy of this post for Main"):
        bf.core.call("post.clear", {"id": post["id"]})
    assert bf.store.post(post["id"])["state"] == "unsure"


def unsure_nowhere(bf, *accounts):
    """A post whose booking got no answer at all: no copy known anywhere."""
    post = post_for(bf, *accounts)
    bf.service.on("createPost", no_answer())
    bf.core.call("post.book", {"id": post["id"]})
    bf.service.script.clear()
    bf.service.calls.clear()
    return post


def test_a_different_post_in_the_same_minute_is_not_taken_for_this_one(bf):
    post = unsure_nowhere(bf, MAIN)
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("mine", "ch-1", SLOT_TEXT, "Something the owner wrote.")))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "unsure" and "A different post sits in that slot on Main" in (
        after["note"])
    assert bf.store.upstream(post["id"]) == []  # never adopted


def test_a_text_that_differs_only_in_spaces_and_line_breaks_is_the_same_post(bf):
    post = unsure_nowhere(bf, MAIN)
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("p-1", "ch-1", SLOT_TEXT, "  " + BODY.replace(" ", "\n  "))))
    run(bf)
    assert bf.store.post(post["id"])["state"] == "booked"
    assert copies(bf, post["id"]) == {"buffer:ch-1": "p-1"}


def test_two_posts_in_the_same_minute_on_one_channel_keep_the_post_unsure(bf):
    post = unsure_nowhere(bf, MAIN)
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("a", "ch-1", SLOT_TEXT), ("b", "ch-1", SLOT_TEXT)))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "unsure" and "2 posts sit in that minute on Main" in after["note"]
    assert bf.store.upstream(post["id"]) == []


def test_the_copy_posts_already_holds_is_the_one_adopted_over_any_other(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", data(createPost=made("p-1", status="draft")))
    bf.core.call("post.book", {"id": post["id"]})  # p-1 kept unconfirmed
    bf.service.script.clear()
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing(("x", "ch-1", SLOT_TEXT), ("p-1", "ch-1", SLOT_TEXT)))
    run(bf)  # the owner finished the draft on Buffer; another post shares its minute
    assert bf.store.post(post["id"])["state"] == "booked"
    [copy] = bf.store.upstream(post["id"], live=True)
    assert (copy["external_id"], copy["state"]) == ("p-1", "booked")


def test_buffers_list_is_asked_only_for_the_unsure_channels_and_the_minutes_around_them(bf):
    unsure_nowhere(bf, MAIN)
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing())
    run(bf)
    [query] = bf.service.queries("posts")
    assert 'channelIds: ["ch-1", "ch-2"]' not in query and 'channelIds: ["ch-1"]' in query
    start, end = buffer.to_service_time(SLOT - MINUTE), buffer.to_service_time(SLOT + 2 * MINUTE)
    assert f'dueAt: {{start: "{start}", end: "{end}"}}' in query
    assert "text" in query


def test_with_several_organizations_the_channels_are_kept_out_of_the_query_and_cut_here(bf):
    bf.service.on("organizations", data(account={"organizations": [{"id": "o-1"},
                                                                    {"id": "o-2"}]}))
    bf.service.on("posts", listing(("a", "ch-1", SLOT_TEXT), ("b", "ch-9", SLOT_TEXT)))
    held = bf.ctx.schedulers["buffer"].scheduled(accounts=["buffer:ch-1"], around_ms=[SLOT])
    assert [h["external_id"] for h in held] == ["a", "a"]  # one page for each organization
    assert all("channelIds" not in q for q in bf.service.queries("posts"))


def test_a_named_copy_the_narrowed_list_does_not_show_is_asked_about_by_itself(bf):
    post = post_for(bf, MAIN)
    bf.service.on("createPost", data(createPost=made("p-1", status="draft")))
    bf.core.call("post.book", {"id": post["id"]})
    bf.service.script.clear()
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing())
    bf.service.on("post", seen("p-1", status="draft"))
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "unsure" and "as a draft or waiting for approval" in after["note"]
    bf.service.script["post"] = [errors("NOT_FOUND", "Post not found.")]  # removed there
    run(bf)
    after = bf.store.post(post["id"])
    assert after["state"] == "queued" and "createPost" not in bf.service.asked()[1:]


def test_an_unsure_booking_stays_unsure_when_buffers_list_cannot_be_read(bf):
    post = unsure_booking(bf)
    bf.service.on("organizations", no_answer())
    run(bf)
    assert bf.store.post(post["id"])["state"] == "unsure"


# --- the request budget ---------------------------------------------------------------------


@pytest.fixture
def busy(make_rig):
    """Buffer with no daily limit, for a queue of many posts on one brand."""
    rig = make_rig(settings={"scheduler": "buffer", "posts_per_page_per_day": 0},
                   secrets={"buffer_key": KEY})
    rig.service = Buffer(rig.core)
    rig.accounts = [MAIN, SECOND]
    return rig


def book_many(rig, page, count: int, start: int = 0) -> list[dict]:
    """``count`` posts booked on both channels an hour apart: the first through the app,
    the rest copied from it in the store (a second piece from the same article would be
    held as too close to the first)."""
    first = queued(rig, page=page)
    rig.service.on("createPost", *created((f"p{start}-1", "ch-1"), (f"p{start}-2", "ch-2")))
    out = [rig.core.call("post.book", {"id": first["id"], "slot_ms": SLOT + start * HOUR})]
    columns = [c["name"] for c in rig.store.all("PRAGMA table_info(post)") if c["name"] != "id"]
    for n in range(start + 1, start + count):
        new = rig.store.insert("post", {**{c: out[0][c] for c in columns},
                                        "slot_ms": SLOT + n * HOUR})
        rig.store.insert("attempt", {"post_id": new, "action": "book", "scheduler": "buffer",
                                     "started": START, "finished": START, "outcome": "sent",
                                     "sent": {"slot_ms": SLOT + n * HOUR,
                                              "accounts": ["buffer:ch-1", "buffer:ch-2"]}})
        for c in (1, 2):
            rig.store.insert("upstream", {"post_id": new, "account": f"buffer:ch-{c}",
                                          "external_id": f"p{n}-{c}", "state": "booked",
                                          "seen": START, "slot_ms": SLOT + n * HOUR})
        out.append(rig.store.post(new))
    return out


def sent_figures(likes: float = 3.0) -> dict:
    return seen("x", status="sent", metrics=[{"type": "likes", "value": likes,
                                              "unit": "count"}])


def test_a_sent_copy_is_read_once_with_its_figures(bf):
    bf.service.on("post", sent_figures())
    [one] = bf.ctx.schedulers["buffer"].status(1, [copy_of()]).copies
    assert one["state"] == "published" and one["figures"] == {"likes": 3.0}
    assert bf.service.asked() == ["post"]
    assert "metrics { type value unit }" in bf.service.queries("post")[0]


def test_one_reconcile_run_over_a_realistic_queue_makes_four_requests(busy):
    page = busy.page(accounts=[MAIN, SECOND])
    booked_ = book_many(busy, page, 11)
    went = booked_[5:]
    for post in went:  # gone out yesterday, figures still being gathered
        busy.store.move_post(post["id"], "went_out", slot_ms=START - HOUR)
        busy.store.conn.execute("UPDATE upstream SET state='published' WHERE post_id=?",
                                (post["id"],))
    busy.store.conn.commit()
    unsure = book_many(busy, page, 1, start=20)[0]
    busy.store.conn.execute("UPDATE attempt SET outcome='unsure' WHERE post_id=?",
                            (unsure["id"],))
    busy.store.conn.execute("DELETE FROM upstream WHERE post_id=?", (unsure["id"],))
    busy.store.conn.execute("UPDATE post SET state='unsure' WHERE id=?", (unsure["id"],))
    busy.store.conn.commit()
    busy.service.script.clear()
    busy.service.calls.clear()
    busy.service.on("post", *[seen("x") for _ in range(16)], *[sent_figures()] * 6)
    busy.service.on("organizations", ORGS)
    busy.service.on("posts", listing())
    run(busy)
    # 16 booked copies in one read, the unsure booking's list (2), 6 sent copies in one
    assert busy.service.asked() == ["post", "organizations", "posts", "post"]
    assert len(booked_) == 11 and busy.store.all("SELECT COUNT(*) AS n FROM figure")[0]["n"] == 6
    busy.service.calls.clear()
    run(busy)  # half an hour later: figures are not asked for again the same day, and the
    assert busy.service.asked() == ["post"]  # unsure one was found on no channel: queued


def test_reads_are_batched_at_most_twenty_five_to_a_request(bf):
    bf.service.on("post", seen("x"))
    asked = [(n, [{"account": "buffer:ch-1", "external_id": f"p-{n}", "slot_ms": SLOT}])
             for n in range(30)]
    seen_ = bf.ctx.schedulers["buffer"].status_many(asked)
    assert all(s.known for s in seen_.values()) and len(seen_) == 30
    assert bf.service.asked() == ["post", "post"]
    assert bf.service.queries("post")[0].count(": post(") == buffer.BATCH_MOST == 25


def test_a_batch_with_an_error_in_it_is_read_again_one_post_at_a_time(bf):
    bf.service.on("post", seen("p-1"), errors("NOT_FOUND", "Post not found."))
    copies_ = [{"account": "buffer:ch-1", "external_id": "p-1", "slot_ms": SLOT},
               {"account": "buffer:ch-2", "external_id": "p-2", "slot_ms": SLOT}]
    one = bf.ctx.schedulers["buffer"].status(1, copies_)
    assert [c["state"] for c in one.copies] == ["scheduled", "gone"]
    assert bf.service.asked() == ["post", "post", "post"]  # the batch, then each alone


def test_a_rate_limit_stops_every_request_until_its_retry_after_and_the_screen_says_so(busy):
    bf = busy
    post = booked(bf)
    limited = errors("RATE_LIMIT_EXCEEDED", "Too many requests.", 429)
    limited["headers"]["Retry-After"] = "3600"
    bf.service.on("post", limited)
    run(bf)
    assert bf.store.post(post["id"])["state"] == "booked"
    from posts import screen  # noqa: PLC0415

    [wait] = screen.overview(bf.ctx, {})["waits"]
    assert wait == {"scheduler": "buffer", "label": "Buffer",
                    "until_ms": bf.clock.now + 3_600_000}
    asked = len(bf.service.calls)
    run(bf)  # due again, but Buffer said to wait
    other = queued(bf, page=bf.store.page(post["page_id"]))
    after = bf.core.call("post.book", {"id": other["id"], "slot_ms": SLOT + 3 * HOUR})
    assert after["state"] == "queued" and "waits 29 minutes more" in after["note"]
    assert len(bf.service.calls) == asked  # nothing at all was sent while waiting
    bf.service.script.clear()
    bf.service.on("post", seen("p-1"), seen("p-2"))
    run(bf)
    assert len(bf.service.calls) > asked and screen.overview(bf.ctx, {})["waits"] == []


def test_a_rate_limit_with_no_retry_after_waits_fifteen_minutes(bf):
    booked(bf)
    bf.service.on("post", errors("RATE_LIMIT_EXCEEDED", "Too many requests.", 429))
    run(bf)
    assert bf.ctx.schedulers["buffer"].wait_until() == bf.clock.now + 15 * MINUTE


def test_figures_are_asked_for_once_a_day_and_not_once_they_stop_changing(busy):
    page = busy.page(accounts=[MAIN, SECOND])
    [post] = book_many(busy, page, 1)
    busy.store.move_post(post["id"], "went_out", slot_ms=START - HOUR)
    busy.store.conn.execute("UPDATE upstream SET state='published' WHERE post_id=?",
                            (post["id"],))
    busy.store.conn.commit()
    busy.service.script.clear()
    busy.service.on("post", sent_figures(3))
    reads = []
    for _ in range(4):
        busy.service.calls.clear()
        run(busy, DAY)
        reads.append(busy.service.asked().count("post"))
    assert reads == [1, 1, 0, 0]  # day 2 gave the same figures as day 1: no more asked


# --- the schema check the stand-in runs -------------------------------------------------------


def test_every_query_posts_writes_uses_only_what_buffers_documentation_names():
    for query in [
        create_query("ch-1", BODY, SLOT), buffer.edit_query("p-1", text="x", slot_ms=SLOT),
        buffer.delete_query("p-1"), buffer.read_query("p-1"),
        buffer.batch_query(["p-1", "p-2"]), buffer.ORGANIZATIONS,
        buffer.list_query("o-1", "c1", channel_ids=["ch-1"], window=(SLOT, SLOT + HOUR)),
        buffer.list_query("o-1", None), buffer.channels_query("o-1"),
        buffer.sent_query("o-1", ["ch-1"], None)]:
        buffer_schema.check(query)


@pytest.mark.parametrize("query, says", [
    ("query { post(input: {id: \"p\"}) { id stauts } }", "has no field 'stauts'"),
    ("query { post(input: {postId: \"p\"}) { id } }", "PostInput has no field 'postId'"),
    ("query { posts(first: 20, input: {organizationId: \"o\", filter: {status: "
     "[scheduled, queued]}}) { edges { node { id } } } }", "'queued' is not a PostStatus"),
    ("mutation { createPost(input: {text: \"x\", channelId: \"c\", mode: scheduled, "
     "schedulingType: automatic, assets: [], needsApproval: false}) { __typename } }",
     "'scheduled' is not a ShareMode"),
    ("query { post(input: {id: \"p\"}) }", "say which of its fields"),
    ("mutation { createPost(input: {text: \"x\"}) { post { id } } }", "is a union"),
    ("query { post(input: {id: \"p\"}) { id status { name } } }", "it has no fields"),
    ("mutation { deletePost(input: {id: \"p\"}) { ... on PostActionSuccess { post { id } } } }",
     "is not a type of DeletePostPayload"),
    ("query { posts(first: \"20\", input: {organizationId: \"o\"}) { pageInfo { endCursor } } }",
     "written as a number"),
])
def test_the_stand_in_refuses_a_name_buffers_documentation_does_not_have(query, says):
    with pytest.raises(buffer_schema.SchemaError, match=re.escape(says)):
        buffer_schema.check(query)


# --- one host ---------------------------------------------------------------------------------


def test_only_buffers_own_host_is_ever_asked_through_a_whole_life_of_a_post(bf):
    post = post_for(bf)
    bf.service.on("createPost", *created(("p-1", "ch-1"), ("p-2", "ch-2")))
    bf.core.call("post.book", {"id": post["id"]})
    bf.service.on("post", seen("p-1"), seen("p-2"))
    bf.service.on("editPost", data(editPost=made("p-1")), data(editPost=made("p-2", "ch-2")))
    bf.core.call("post.edit", {"id": post["id"], "body": "New words for the post."})
    bf.service.on("post", seen("p-1"), seen("p-2"))
    bf.service.on("deletePost", data(deletePost={"__typename": "DeletePostSuccess", "id": "p"}))
    bf.core.call("post.cancel", {"id": post["id"]})
    bf.service.on("organizations", ORGS)
    bf.service.on("posts", listing())
    bf.ctx.schedulers["buffer"].scheduled()
    assert {args["url"] for _, args in bf.service.calls} == {URL}
    assert {args["method"] for _, args in bf.service.calls} == {"POST"}
    assert buffer.HOST == "api.buffer.com" and URL == f"https://{buffer.HOST}"


def test_the_manifest_asks_for_exactly_that_host_and_a_key_of_its_own():
    from jimmyhq.apps import manifest as mf  # noqa: PLC0415

    m = mf.load(APP_ROOT)
    assert f"net:{buffer.HOST}" in m.permissions
    assert [s.name for s in m.secrets if s.name == buffer.SECRET] == ["buffer_key"]
    setting = next(s for s in m.settings if s.key == "scheduler")
    assert "buffer" in [o.value for o in setting.options]


def test_buffer_is_one_of_the_schedulers_posts_offers(rig):
    scheduler = rig.ctx.schedulers["buffer"]
    assert (scheduler.id, scheduler.label, scheduler.sends_out, scheduler.can_edit) == (
        "buffer", "Buffer", True, True)


def test_classify_sorts_a_plain_yes(bf):
    answer = classify(data(createPost=made("p-1")), None, KEY)
    assert isinstance(answer, Answer) and answer.kind == "yes"
