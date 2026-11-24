"""The one booking path: pre-checks, the attempt recorded before anything is sent, a
definite no clearing itself, no answer blocking another try until the owner clears it,
and nothing ever retried by itself. Then slots, By hand, moves, edits and cancels."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from posts import booking
from posts.schedulers import Outcome
from posts_rig import START

HOUR = 3_600_000
DAY = 24 * HOUR
BODY = "Some 1,240 homes in the valley added solar panels last year."


class Fake:
    """A scheduler that sends out, whose answers the test chooses."""

    id = "fake"
    label = "Fake scheduler"
    sends_out = True

    def __init__(self, rig_box):
        self.box = rig_box
        self.answers: list = []
        self.calls: list = []
        self.can_edit = True
        self.attempts_seen: list = []

    def needs(self, page):
        return None if page["accounts"] else "This page has no account on Fake scheduler."

    def _next(self, *call):
        self.calls.append(call)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def book(self, b):
        rig = self.box[0]
        self.attempts_seen.append([a["outcome"] for a in rig.store.attempts(b.post_id)])
        return self._next("book", b)

    def status(self, post_id, upstream):
        return "booked"

    def move(self, post_id, upstream, slot_ms):
        return self._next("move", slot_ms)

    def edit_text(self, post_id, upstream, text):
        return self._next("edit", text)

    def cancel(self, post_id, upstream):
        return self._next("cancel")

    def scheduled(self):
        return []


ACCOUNTS = [{"scheduler": "fake", "account_id": "a1", "label": "Main"},
            {"scheduler": "fake", "account_id": "a2", "label": "Second"}]


def sent(*accounts):
    return Outcome("sent", [{"account": f"fake:{a}", "external_id": f"up-{a}"}
                            for a in accounts])


@pytest.fixture
def fake_rig(make_rig):
    box: list = []
    fake = Fake(box)
    rig = make_rig(settings={"scheduler": "fake"}, schedulers={"fake": fake})
    box.append(rig)
    rig.fake = fake
    return rig


def queued(rig, *, accounts=ACCOUNTS, page=None, body=BODY, url=None):
    """A post approved and queued, with a proposed slot."""
    page = page or rig.page(accounts=accounts)
    more = {"url": url} if url else {}
    piece = rig.through(page, body, **more)
    [card] = [c for c in rig.cards_of("draft") if c["subject"].endswith(
        f"post:{rig.store.post_of(piece['id'])['id']}")]
    rig.core.decide(card["card_id"], "approve")
    return rig.store.post_of(piece["id"])


def test_a_scheduler_that_sends_out_waits_for_the_owners_ladder_after_approval(fake_rig):
    post = queued(fake_rig)
    assert post["state"] == "queued" and post["slot_ms"] and fake_rig.fake.calls == []


def test_the_ladder_set_to_alone_books_at_approval(fake_rig):
    fake_rig.core.ladder[("posts", "book")] = "alone"
    fake_rig.fake.answers.append(sent("a1", "a2"))
    post = queued(fake_rig)
    assert post["state"] == "booked"


def test_the_attempt_is_recorded_before_anything_is_sent(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers.append(sent("a1", "a2"))
    booked = fake_rig.core.call("post.book", {"id": post["id"]})
    assert fake_rig.fake.attempts_seen == [["pending"]]
    assert booked["state"] == "booked"
    kinds = [e["kind"].rsplit(".", 2)[-2:] for e in fake_rig.core.events]
    assert kinds[-2:] == [["booking", "attempted"], ["booking", "result"]]
    assert sorted(u["external_id"] for u in fake_rig.store.upstream(post["id"])) == [
        "up-a1", "up-a2"]


def test_the_second_booking_of_a_post_whose_first_attempt_got_no_answer_is_refused(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers.append(TimeoutError("timed out"))
    first = fake_rig.core.call("post.book", {"id": post["id"]})
    assert first["state"] == "unsure" and "may or may not be booked" in first["note"]
    with pytest.raises(Exception, match="only a queued post can be booked"):
        fake_rig.core.call("post.book", {"id": post["id"]})
    assert len(fake_rig.fake.calls) == 1


def test_an_unsure_booking_is_never_tried_again_by_itself(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers.append(TimeoutError("timed out"))
    fake_rig.core.call("post.book", {"id": post["id"]})
    fake_rig.clock.advance(DAY)
    fake_rig.advance(5)
    assert len(fake_rig.fake.calls) == 1
    assert fake_rig.store.post(post["id"])["state"] == "unsure"


def test_once_the_owner_clears_an_unsure_booking_it_can_be_booked_again(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers += [TimeoutError("timed out"), sent("a1", "a2")]
    fake_rig.core.call("post.book", {"id": post["id"]})
    assert fake_rig.core.call("post.clear", {"id": post["id"]})["state"] == "queued"
    assert fake_rig.core.call("post.book", {"id": post["id"]})["state"] == "booked"
    outcomes = [a["outcome"] for a in fake_rig.store.attempts(post["id"])]
    assert outcomes == ["cleared", "sent"]


def test_a_definite_refusal_clears_itself_and_says_why(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers += [Outcome("refused", detail="The account needs reconnecting."),
                              sent("a1", "a2")]
    refused = fake_rig.core.call("post.book", {"id": post["id"]})
    assert refused["state"] == "queued" and "needs reconnecting" in refused["note"]
    assert fake_rig.core.call("post.book", {"id": post["id"]})["state"] == "booked"


def test_a_yes_that_does_not_say_where_each_copy_went_is_unsure(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers.append(sent("a1"))  # nothing for the second account
    assert fake_rig.core.call("post.book", {"id": post["id"]})["state"] == "unsure"


def test_upstream_copies_are_matched_to_accounts_by_what_came_back_not_by_order(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers.append(Outcome("sent", [
        {"account": "fake:a2", "external_id": "second"},
        {"account": "fake:a1", "external_id": "first"}]))
    fake_rig.core.call("post.book", {"id": post["id"]})
    found = {u["account"]: u["external_id"] for u in fake_rig.store.upstream(post["id"])}
    assert found == {"fake:a1": "first", "fake:a2": "second"}


def test_a_waiting_attempt_older_than_the_setting_becomes_unsure(fake_rig):
    post = queued(fake_rig)
    store = fake_rig.store
    store.insert("attempt", {"post_id": post["id"], "action": "book", "scheduler": "fake",
                             "started": fake_rig.clock.now, "outcome": "pending"})
    store.move_post(post["id"], "booking")
    fake_rig.clock.advance(121 * 60_000)
    fake_rig.advance()
    assert store.post(post["id"])["state"] == "unsure"
    assert fake_rig.fake.calls == []


def test_a_page_without_what_the_scheduler_needs_is_refused(fake_rig):
    post = queued(fake_rig, accounts=[])
    with pytest.raises(Exception, match="no account on Fake scheduler"):
        fake_rig.core.call("post.book", {"id": post["id"]})
    assert fake_rig.store.attempts(post["id"]) == []


def test_a_time_in_the_past_is_refused(fake_rig):
    post = queued(fake_rig)
    with pytest.raises(Exception, match="That time has passed"):
        fake_rig.core.call("post.book", {"id": post["id"], "slot_ms": START - HOUR})
    with pytest.raises(Exception, match="That time has passed"):
        fake_rig.core.call("post.move", {"id": post["id"], "slot_ms": START - HOUR})
    assert fake_rig.fake.calls == []


def test_a_time_closer_than_the_lead_is_refused(fake_rig):
    post = queued(fake_rig)
    with pytest.raises(Exception, match="less than 60 minutes away"):
        fake_rig.core.call("post.book", {"id": post["id"], "slot_ms": fake_rig.clock.now
                                         + 10 * 60_000})


def test_a_post_is_never_booked_twice_in_one_minute_on_one_account(fake_rig):
    page = fake_rig.page(accounts=ACCOUNTS)
    other = fake_rig.page(name="Workshop", accounts=[ACCOUNTS[0]])
    first = queued(fake_rig, page=page)
    second = queued(fake_rig, page=other, body="A different post about bread and seeds.",
                    url="https://bread.example.com/a")
    slot = first["slot_ms"]
    fake_rig.fake.answers.append(sent("a1", "a2"))
    fake_rig.core.call("post.book", {"id": first["id"], "slot_ms": slot})
    with pytest.raises(Exception, match="same account goes out in that minute"):
        fake_rig.core.call("post.book", {"id": second["id"], "slot_ms": slot + 30_000})
    assert len(fake_rig.fake.calls) == 1


def test_a_page_already_at_its_posts_a_day_is_refused(fake_rig):
    page = fake_rig.page(accounts=ACCOUNTS, hours=["08:00", "12:00"])
    first = queued(fake_rig, page=page)
    second = queued(fake_rig, page=page, body="A different post about bread and seeds.",
                    url="https://bread.example.com/a")
    assert local(first["slot_ms"], "UTC") == "2027-01-15 12:00"
    assert local(second["slot_ms"], "UTC") == "2027-01-16 08:00"  # the next free day
    fake_rig.fake.answers.append(sent("a1", "a2"))
    fake_rig.core.call("post.book", {"id": first["id"]})
    with pytest.raises(Exception, match="its limit for a day"):
        fake_rig.core.call("post.book", {"id": second["id"],
                                         "slot_ms": first["slot_ms"] + 4 * HOUR})


def test_a_booked_post_edited_while_the_scheduler_cannot_take_edits_says_so(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers.append(sent("a1", "a2"))
    fake_rig.core.call("post.book", {"id": post["id"]})
    fake_rig.fake.can_edit = False
    edited = fake_rig.core.call("post.edit", {"id": post["id"], "body": BODY + " Updated."})
    assert edited["out_of_step"] == 1
    assert edited["note"] == ("This change did not reach Fake scheduler. Book again with this "
                              "text, or change it there.")
    assert edited["body"].endswith("Updated.") and edited["state"] == "booked"
    assert [c[0] for c in fake_rig.fake.calls] == ["book"]


def test_a_booked_post_edit_the_scheduler_takes_goes_through_edit_text(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers += [sent("a1", "a2"), sent("a1", "a2")]
    fake_rig.core.call("post.book", {"id": post["id"]})
    edited = fake_rig.core.call("post.edit", {"id": post["id"], "body": BODY + " **New**."})
    assert edited["out_of_step"] == 0
    assert fake_rig.fake.calls[-1] == ("edit", BODY + " New.")  # booked as plain text


def test_a_failed_edit_says_plainly_it_did_not_reach_the_scheduler(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers += [sent("a1", "a2"), Outcome("refused", detail="Too late.")]
    fake_rig.core.call("post.book", {"id": post["id"]})
    edited = fake_rig.core.call("post.edit", {"id": post["id"], "body": BODY + " Again."})
    assert edited["out_of_step"] == 1 and "did not reach Fake scheduler" in edited["note"]


def test_cancelling_a_booked_post_fails_closed(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers += [sent("a1", "a2"), Outcome("refused", detail="Not found."),
                              sent("a1", "a2")]
    fake_rig.core.call("post.book", {"id": post["id"]})
    with pytest.raises(Exception, match="It is still booked"):
        fake_rig.core.call("post.cancel", {"id": post["id"]})
    assert fake_rig.store.post(post["id"])["state"] == "booked"
    assert fake_rig.core.call("post.cancel", {"id": post["id"]})["state"] == "declined"


def test_moving_a_booked_post_goes_through_the_scheduler(fake_rig):
    post = queued(fake_rig)
    fake_rig.fake.answers += [sent("a1", "a2"), sent("a1", "a2")]
    fake_rig.core.call("post.book", {"id": post["id"]})
    moved = fake_rig.core.call("post.move", {"id": post["id"], "slot_ms": post["slot_ms"] + DAY})
    assert moved["slot_ms"] == post["slot_ms"] + DAY and fake_rig.fake.calls[-1][0] == "move"


# --- slots, Practice and By hand ---------------------------------------------------------------


def local(ms: int, zone: str) -> str:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    return datetime.fromtimestamp(ms / 1000, ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")


def test_a_slot_is_the_pages_next_posting_hour_in_its_own_time_zone(rig):
    page = rig.page(timezone="America/New_York", hours=["09:30"])
    slot = booking.propose_slot(rig.ctx, page)
    assert local(slot, "America/New_York") == "2027-01-15 09:30"


def test_a_page_with_no_time_zone_posts_on_the_owners(make_rig):
    rig = make_rig(timezone="Asia/Tokyo")
    page = rig.page(hours=["08:00"])
    assert local(booking.propose_slot(rig.ctx, page), "Asia/Tokyo") == "2027-01-16 08:00"


def test_a_slot_keeps_its_local_hour_across_a_change_of_clocks(make_rig):
    rig = make_rig()
    rig.clock.now = int(datetime(2027, 3, 13, 20, 0, tzinfo=UTC).timestamp() * 1000)
    page = rig.page(timezone="America/New_York", hours=["08:00"])
    slot = booking.propose_slot(rig.ctx, page)
    assert local(slot, "America/New_York") == "2027-03-14 08:00"  # the day clocks go forward


def test_practice_books_nothing_and_records_what_it_would_send(rig):
    page = rig.page()
    piece = rig.through(page, BODY, sponsored=True)
    [card] = rig.cards_of("draft")
    rig.core.decide(card["card_id"], "approve")
    post = rig.store.post_of(piece["id"])
    assert post["state"] == "booked" and post["note"] == "Practice: nothing was sent."
    [logged] = rig.store.all("SELECT * FROM practice_log")
    assert logged["would_send"]["text"] == BODY + "\n\n#ad"
    assert rig.core.fetched == []


def test_by_hand_lists_the_post_with_its_time_and_the_owner_presses_posted(make_rig):
    rig = make_rig(settings={"scheduler": "by_hand"})
    piece = rig.through(rig.page(), BODY)
    [card] = rig.cards_of("draft")
    rig.core.decide(card["card_id"], "approve")
    post = rig.store.post_of(piece["id"])
    assert post["state"] == "booked" and "Posted" in post["note"]
    done = rig.core.call("post.posted", {"id": post["id"]})
    assert done["state"] == "went_out"
    assert [e["kind"] for e in rig.core.events][-2:] == ["app.posts.post.went_out",
                                                        "app.posts.posted"]


def test_a_post_that_went_out_cannot_be_booked_again(make_rig):
    rig = make_rig(settings={"scheduler": "by_hand"})
    piece = rig.through(rig.page(), BODY)
    [card] = rig.cards_of("draft")
    rig.core.decide(card["card_id"], "approve")
    post = rig.store.post_of(piece["id"])
    rig.core.call("post.posted", {"id": post["id"]})
    with pytest.raises(Exception, match="This post is already out"):
        rig.core.call("post.book", {"id": post["id"]})
