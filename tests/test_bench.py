"""The Bench's Python API: what other modules call."""

from __future__ import annotations

import subprocess
import sys

import pytest

from jimmyhq import bench
from jimmyhq.bench import cards
from jimmyhq.bench.cards import BenchLens
from jimmyhq.core import open_core
from jimmyhq.ids import now_ms

HOUR = 60 * 60 * 1000
DAY = 24 * HOUR


def file(core, **fields):
    fields = {"filed_by": "agent:scout", "kind": "village.finding", "source": "Village bench",
              "title": "A finding", **fields}
    return bench.file_card(core, **fields)


@pytest.fixture
def clock(monkeypatch):
    """A clock the test can move. It drives both event times and read times."""
    class Clock:
        now = now_ms()

        def forward(self, ms):
            self.now += ms

    c = Clock()
    monkeypatch.setattr("jimmyhq.tape.registry.now_ms", lambda: c.now)
    monkeypatch.setattr("jimmyhq.bench.cards.now_ms", lambda: c.now)
    return c


def test_a_filed_card_waits_in_needs_with_the_default_actions(core):
    card_id = file(core, summary="One line", body="The long story.")
    board = bench.board(core)
    assert [c["id"] for c in board["lanes"]["needs"]] == [card_id]
    assert board["counts"] == {"needs": 1, "done": 0, "waiting": 0}
    listed = board["lanes"]["needs"][0]
    assert "body" not in listed and listed["summary"] == "One line"
    assert [(a["id"], a["style"], a["closes"], a["needs_reason"]) for a in listed["actions"]] == [
        ("approve", "primary", True, False),
        ("not_yet", "default", False, False),
        ("no", "danger", True, True),
    ]
    assert listed["actions"][1]["moves_to"] == "waiting"
    assert bench.get_card(core, card_id)["body"] == "The long story."


def test_the_card_records_who_filed_it_and_what_it_is_about(core):
    card_id = file(core, subject="task:01JABCDEFGHJKMNPQRSTVWXYZ0", why="It blocks the release.",
                   details=[{"label": "Evidence", "text": "Three failing runs."}])
    card = bench.get_card(core, card_id)
    assert (card["filed_by"], card["subject"]) == ("agent:scout", "task:01JABCDEFGHJKMNPQRSTVWXYZ0")
    assert card["why"] == "It blocks the release."
    assert card["details"] == [{"label": "Evidence", "text": "Three failing runs."}]
    assert card["history"][0]["type"] == "filed"
    event = core.tape.after(0, kind="bench.filed")[0]
    assert (event.actor, event.subject) == ("agent:scout", f"bench:{card_id}")


def test_approving_closes_the_card_and_it_stays_readable_by_id(core):
    card_id = file(core)
    cards.decide(core, card_id, action="approve", note="Go ahead.")
    board = bench.board(core)
    assert board["counts"] == {"needs": 0, "done": 0, "waiting": 0}
    card = bench.get_card(core, card_id)
    assert card["status"] == "decided"
    assert (card["decision"]["action"], card["decision"]["note"]) == ("approve", "Go ahead.")
    assert [r["card_id"] for r in board["recent"]] == [card_id]
    assert core.tape.after(0, kind="bench.decided")[0].actor == "owner"


def test_not_yet_moves_the_card_to_waiting_and_an_update_brings_it_back(core):
    card_id = file(core)
    cards.decide(core, card_id, action="not_yet", note="After the holidays.")
    assert bench.board(core)["counts"] == {"needs": 0, "done": 0, "waiting": 1}
    bench.update_card(core, card_id, by="agent:scout", note="The holidays are over.")
    board = bench.board(core)
    assert board["counts"] == {"needs": 1, "done": 0, "waiting": 0}
    assert board["lanes"]["needs"][0]["updates"] == 1
    kinds = [h["type"] for h in bench.get_card(core, card_id)["history"]]
    assert kinds == ["filed", "decided", "updated"]


def test_a_no_without_a_reason_is_refused_and_says_a_reason_is_needed(core):
    card_id = file(core)
    for reason in (None, "", "   "):
        with pytest.raises(bench.BenchError, match="reason is needed") as caught:
            cards.decide(core, card_id, action="no", reason=reason)
        assert caught.value.status == 422
    assert core.tape.after(0, kind="bench.decided") == []
    cards.decide(core, card_id, action="no", reason="Not the right tone.")
    assert bench.get_card(core, card_id)["decision"]["reason"] == "Not the right tone."


def test_deciding_a_card_twice_is_refused_as_already_closed(core):
    card_id = file(core)
    cards.decide(core, card_id, action="approve")
    with pytest.raises(bench.BenchError, match="already closed") as caught:
        cards.decide(core, card_id, action="approve")
    assert caught.value.status == 409
    assert len(core.tape.after(0, kind="bench.decided")) == 1


def test_an_action_the_card_does_not_have_is_refused_and_lists_the_real_ones(core):
    card_id = file(core)
    with pytest.raises(bench.BenchError, match="approve, not_yet, no") as caught:
        cards.decide(core, card_id, action="launch")
    assert caught.value.status == 422


def test_a_filer_can_declare_its_own_actions(core):
    card_id = file(core, actions=[
        {"id": "send", "label": "Send it", "style": "primary", "closes": True},
        {"id": "rework", "label": "Rework", "needs_reason": True, "moves_to": "waiting"},
    ])
    with pytest.raises(bench.BenchError, match="'Rework'"):
        cards.decide(core, card_id, action="rework")
    cards.decide(core, card_id, action="rework", reason="Shorter, please.")
    assert bench.board(core)["counts"]["waiting"] == 1
    cards.decide(core, card_id, action="send")
    assert bench.get_card(core, card_id)["status"] == "decided"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"kind": "Finding"}, "kind: must look like"),
        ({"title": "   "}, "title: cannot be empty"),
        ({"title": "x" * 201}, "title"),
        ({"source": ""}, "source: cannot be empty"),
        ({"lane": "waiting"}, "lane"),
        ({"body": "x" * 1_000_001}, "body"),
        ({"body": "half \ud83d"}, "cannot be saved"),
        ({"subject": "nothing"}, "subject: must look like"),
        ({"colour": "red"}, "colour"),
        ({"actions": [{"id": "a", "label": "A", "closes": True},
                      {"id": "a", "label": "B"}]}, "its own id"),
        ({"actions": [{"id": "a", "label": "A", "closes": True, "moves_to": "done"}]},
         "not both"),
        ({"actions": [{"id": "a", "label": "A", "moves_to": "waiting"}]}, "never leave"),
        ({"actions": [{"id": "Bad Id", "label": "A", "closes": True}]}, "id: must look like"),
        ({"files": [{"name": "a.txt", "ref": "/etc/passwd"}]}, "ref: must look like"),
        ({"files": [{"name": "a.txt", "ref": "sha256:" + "a" * 64}]}, "not in the Vault"),
        ({"filed_by": ""}, "filed_by cannot be empty"),
    ],
)
def test_a_card_that_breaks_the_rules_is_refused_with_a_sentence(core, fields, message):
    with pytest.raises(bench.BenchError, match=message) as caught:
        file(core, **fields)
    assert caught.value.status == 422
    assert core.tape.after(0, kind="bench.filed") == []


def test_a_200_kb_body_goes_to_the_vault_and_comes_back_whole(core):
    body = "Findings, line by line. é\n" * 8000
    assert len(body.encode()) > 200_000
    card_id = file(core, body=body)
    payload = core.tape.after(0, kind="bench.filed")[0].payload
    assert payload["body"] == "" and core.vault.get_text(payload["body_ref"]) == body
    assert bench.get_card(core, card_id)["body"] == body
    state = core.lenses.read(BenchLens())
    assert len(str(state)) < 2000


def test_a_revised_body_replaces_the_old_one_and_the_history_says_so(core):
    card_id = file(core, body="First draft.")
    bench.update_card(core, card_id, by="agent:scout", body="Second draft. " * 500,
                      summary="Now shorter")
    card = bench.get_card(core, card_id)
    assert card["body"].startswith("Second draft.") and card["summary"] == "Now shorter"
    assert card["history"][-1]["revised"] == ["summary", "body"]


def test_only_the_filer_or_the_owner_can_update_or_withdraw_a_card(core):
    card_id = file(core)
    for change in (bench.update_card, bench.withdraw_card):
        with pytest.raises(bench.BenchError) as caught:
            change(core, card_id, by="agent:other", note="Mine now.")
        assert caught.value.status == 403
    bench.update_card(core, card_id, by="owner", note="A note from the owner.")
    bench.withdraw_card(core, card_id, by="agent:scout", note="No longer needed.")
    assert bench.board(core)["counts"]["needs"] == 0
    card = bench.get_card(core, card_id)
    assert card["status"] == "withdrawn" and card["history"][-1]["note"] == "No longer needed."
    with pytest.raises(bench.BenchError, match="already closed"):
        bench.update_card(core, card_id, by="agent:scout", note="One more thing.")


def test_an_update_with_nothing_in_it_is_refused(core):
    card_id = file(core)
    with pytest.raises(bench.BenchError, match="needs a note"):
        bench.update_card(core, card_id, by="agent:scout", note="  ")


def test_a_snoozed_card_leaves_needs_and_returns_on_its_own(core, clock):
    card_id = file(core)
    other = file(core, kind="posts.draft", source="Posts")
    cards.snooze(core, card_id, clock.now + 2 * HOUR)
    board = bench.board(core)
    assert [c["id"] for c in board["lanes"]["needs"]] == [other]
    assert board["counts"] == {"needs": 1, "done": 0, "waiting": 1}
    assert board["needs_by_kind"] == {"posts.draft": 1}
    assert board["needs_by_source"] == {"Posts": 1}
    assert board["lanes"]["waiting"][0]["snoozed"] is True
    clock.forward(2 * HOUR + 1)
    board = bench.board(core)
    assert board["counts"] == {"needs": 2, "done": 0, "waiting": 0}
    assert all(c["snoozed"] is False and c["snoozed_until"] is None
               for c in board["lanes"]["needs"])
    assert len(core.tape.after(0, kind="bench.unsnoozed")) == 0


def test_unsnoozing_brings_a_card_straight_back(core, clock):
    card_id = file(core)
    cards.snooze(core, card_id, clock.now + DAY)
    cards.unsnooze(core, card_id)
    assert bench.board(core)["counts"]["needs"] == 1
    cards.unsnooze(core, card_id)  # already back: nothing more is recorded
    assert len(core.tape.after(0, kind="bench.unsnoozed")) == 1


def test_a_snooze_into_the_past_or_absurdly_far_ahead_is_refused(core, clock):
    card_id = file(core)
    for until, message in ((clock.now - 1, "in the future"), (clock.now, "in the future"),
                           (clock.now + 400 * DAY, "more than a year"), (True, "unix milli")):
        with pytest.raises(bench.BenchError, match=message) as caught:
            cards.snooze(core, card_id, until)
        assert caught.value.status == 422
    assert core.tape.after(0, kind="bench.snoozed") == []


def test_a_snooze_without_a_time_uses_the_default_length_setting(core, clock):
    core.settings.set("bench.snooze_hours", 3, actor="owner")
    card = cards.snooze(core, file(core))
    assert card["snoozed_until"] == clock.now + 3 * HOUR


def test_only_a_card_in_needs_can_be_snoozed(core, clock):
    card_id = file(core, lane="done")
    with pytest.raises(bench.BenchError, match="waiting on you") as caught:
        cards.snooze(core, card_id, clock.now + HOUR)
    assert caught.value.status == 409


def test_the_oldest_wait_counts_from_when_a_card_last_needed_the_owner(core, clock):
    assert bench.board(core)["oldest_needs_ms"] is None
    first = file(core)
    clock.forward(3 * HOUR)
    file(core)
    assert bench.board(core)["oldest_needs_ms"] == 3 * HOUR
    cards.snooze(core, first, clock.now + HOUR)
    assert bench.board(core)["oldest_needs_ms"] == 0
    clock.forward(2 * HOUR)
    assert bench.board(core)["oldest_needs_ms"] == 2 * HOUR  # the second card, filed 2h ago


def test_a_done_card_shows_in_done_with_an_undo_and_expires_from_the_lane(core, clock):
    card_id = file(core, lane="done", undo=True, title="Archived 3 newsletters")
    board = bench.board(core)
    assert board["counts"] == {"needs": 0, "done": 1, "waiting": 0}
    assert [a["id"] for a in board["lanes"]["done"][0]["actions"]] == ["ok", "undo"]
    clock.forward(8 * DAY)
    assert bench.board(core)["counts"]["done"] == 0
    assert bench.get_card(core, card_id)["status"] == "open"  # still there by id
    core.settings.set("bench.done_expire_days", 0, actor="owner")
    assert bench.board(core)["counts"]["done"] == 1
    cards.decide(core, card_id, action="undo")
    assert bench.get_card(core, card_id)["decision"]["action"] == "undo"


def test_a_done_card_nobody_dismissed_for_a_year_leaves_the_lens(core, clock):
    card_id = file(core, lane="done")
    clock.forward(366 * DAY)
    file(core)
    assert card_id not in core.lenses.read(BenchLens())["open"]
    assert bench.get_card(core, card_id)["status"] == "expired"
    with pytest.raises(bench.BenchError, match="already closed"):
        cards.decide(core, card_id, action="ok")


def test_the_recent_decisions_list_is_bounded_by_the_setting_and_the_cap(core):
    for n in range(5):
        cards.decide(core, file(core, title=f"Card {n}"), action="approve")
    assert [r["title"] for r in bench.board(core)["recent"]][:2] == ["Card 4", "Card 3"]
    core.settings.set("bench.recent_decisions", 2, actor="owner")
    assert len(bench.board(core)["recent"]) == 2
    lens = BenchLens()
    state = lens.initial()
    filed = core.tape.after(0, kind="bench.filed")[0]
    decided = core.tape.after(0, kind="bench.decided")[0]
    for n in range(cards.RECENT_CAP + 50):
        card_id = f"{n:026d}"
        state = lens.fold(state, filed.model_copy(
            update={"payload": {**filed.payload, "card_id": card_id}}))
        state = lens.fold(state, decided.model_copy(
            update={"payload": {**decided.payload, "card_id": card_id}}))
    assert len(state["recent"]) == cards.RECENT_CAP and state["open"] == {}


def test_a_decision_handler_runs_for_matching_kinds_and_a_failing_one_does_no_harm(core):
    seen = []
    remove = bench.on_decision("posts.", lambda c, d: seen.append((d.card_kind, d.action)))
    broken = bench.on_decision("posts.", lambda c, d: 1 / 0)
    try:
        cards.decide(core, file(core, kind="posts.draft"), action="approve")
        cards.decide(core, file(core, kind="village.finding"), action="approve")
    finally:
        remove()
        broken()
    assert seen == [("posts.draft", "approve")]
    cards.decide(core, file(core, kind="posts.draft"), action="approve")
    assert len(seen) == 1


def test_a_decision_event_carries_what_another_process_needs_to_react(core):
    card_id = file(core, kind="posts.draft", subject="post:42")
    cards.decide(core, card_id, action="no", reason="Too long.")
    filed = core.tape.after(0, kind="bench.filed")[0]
    event = core.tape.after(0, kind="bench.decided")[0]
    assert event.cause == filed.id and event.subject == f"bench:{card_id}"
    assert event.payload["card_kind"] == "posts.draft" and event.payload["subject"] == "post:42"
    assert event.payload["closes"] is True and event.payload["reason"] == "Too long."


def test_a_second_process_sees_cards_filed_by_the_first(core, cfg):
    other = open_core(cfg)
    try:
        card_id = file(other)
        assert [c["id"] for c in bench.board(core)["lanes"]["needs"]] == [card_id]
        cards.decide(core, card_id, action="approve")
        assert bench.board(other)["counts"]["needs"] == 0
        with pytest.raises(bench.BenchError, match="already closed"):
            cards.decide(other, card_id, action="approve")
    finally:
        other.close()


_FILER = """
import sys
from pathlib import Path
from jimmyhq import bench, config
from jimmyhq.core import open_core
core = open_core(config.Config(data=Path(sys.argv[1]), host="127.0.0.1", port=0))
for n in range(25):
    bench.file_card(core, filed_by="agent:" + sys.argv[2], kind="village.finding",
                    source="Village bench", title=f"Finding {n}", body="x" * 6000)
core.close()
"""


def test_two_processes_filing_at_once_lose_nothing(core, cfg):
    procs = [subprocess.Popen([sys.executable, "-c", _FILER, str(cfg.data), name])  # noqa: S603
             for name in ("one", "two")]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    board = bench.board(core)
    assert board["counts"]["needs"] == 50
    ids = [c["id"] for c in board["lanes"]["needs"]]
    assert len(set(ids)) == 50
    by_filer = {}
    for card in board["lanes"]["needs"]:
        by_filer[card["filed_by"]] = by_filer.get(card["filed_by"], 0) + 1
    assert by_filer == {"agent:one": 25, "agent:two": 25}


def test_a_decision_that_loses_a_race_with_another_process_is_refused_and_not_announced(
        core, cfg, monkeypatch):
    """The other process withdraws the card after ours has found it open and
    before ours prints. The first event on the Tape wins; ours must say so."""
    other = open_core(cfg)
    seen = []
    remove = bench.on_decision("village.", lambda c, d: seen.append(d.action))
    try:
        card_id = file(core)
        real = core.tape.print

        def slow_print(kind, payload, **kw):
            if kind == "bench.decided":
                bench.withdraw_card(other, card_id, by="agent:scout")
            return real(kind, payload, **kw)

        monkeypatch.setattr(core.tape, "print", slow_print)
        with pytest.raises(bench.BenchError, match="already closed") as refused:
            cards.decide(core, card_id, action="approve")
        assert refused.value.status == 409 and seen == []
        card = bench.get_card(core, card_id)
        assert card["status"] == "withdrawn" and card["decision"] is None
        assert bench.board(core)["recent"] == []
        # The losing click is not on the record at all, so nothing reading the
        # Tape can take it for an approval.
        assert core.tape.after(0, kind="bench.decided") == []
    finally:
        remove()
        other.close()


def test_a_withdrawal_that_loses_a_race_with_a_decision_is_refused(core, cfg, monkeypatch):
    other = open_core(cfg)
    try:
        card_id = file(core)
        real = core.tape.print

        def slow_print(kind, payload, **kw):
            if kind == "bench.withdrawn":
                cards.decide(other, card_id, action="approve")
            return real(kind, payload, **kw)

        monkeypatch.setattr(core.tape, "print", slow_print)
        with pytest.raises(bench.BenchError, match="already closed"):
            bench.withdraw_card(core, card_id, by="agent:scout")
        assert bench.get_card(core, card_id)["status"] == "decided"
        assert core.tape.after(0, kind="bench.withdrawn") == []
    finally:
        other.close()


def test_an_update_that_loses_a_race_with_a_decision_is_refused_and_not_written(
        core, cfg, monkeypatch):
    other = open_core(cfg)
    try:
        card_id = file(core)
        real = core.tape.print

        def slow_print(kind, payload, **kw):
            if kind == "bench.updated":
                cards.decide(other, card_id, action="approve")
            return real(kind, payload, **kw)

        monkeypatch.setattr(core.tape, "print", slow_print)
        with pytest.raises(bench.BenchError, match="already closed"):
            bench.update_card(core, card_id, by="agent:scout", title="Something else")
        assert core.tape.after(0, kind="bench.updated") == []
        assert bench.get_card(core, card_id)["title"] == "A finding"
    finally:
        other.close()


def test_a_decision_is_refused_when_the_card_was_updated_after_the_owner_read_it(core):
    card_id = file(core, title="Post the lunch menu", body="Soup.")
    seen = bench.get_card(core, card_id)["updates"]
    bench.update_card(core, card_id, by="agent:scout", title="Post the staff list",
                      body="Everyone.")
    with pytest.raises(bench.BenchError, match="changed since you read it") as refused:
        cards.decide(core, card_id, action="approve", seen_updates=seen)
    assert refused.value.status == 409
    assert core.tape.after(0, kind="bench.decided") == []
    assert bench.get_card(core, card_id)["status"] == "open"
    cards.decide(core, card_id, action="approve", seen_updates=seen + 1)
    assert core.tape.after(0, kind="bench.decided")[0].payload["updates"] == 1


def test_a_decision_says_who_filed_the_card_and_which_revision_was_decided(core):
    seen = []
    remove = bench.on_decision("posts.", lambda c, d: seen.append(d))
    try:
        card_id = file(core, filed_by="token:scout", kind="posts.draft", subject="post:42")
        bench.update_card(core, card_id, by="token:scout", note="Shortened.")
        cards.decide(core, card_id, action="approve")
    finally:
        remove()
    assert [(d.filed_by, d.updates, d.subject) for d in seen] == [("token:scout", 1, "post:42")]
    payload = core.tape.after(0, kind="bench.decided")[0].payload
    assert payload["filed_by"] == "token:scout" and payload["updates"] == 1


@pytest.mark.parametrize(
    "actions",
    [
        # The labels swapped between the two ids: a click on "No" would approve.
        [{"id": "no", "label": "Approve", "style": "primary", "closes": True},
         {"id": "approve", "label": "No", "style": "danger", "closes": True}],
        # "no" without its reason.
        [{"id": "no", "label": "No", "style": "danger", "closes": True}],
        [{"id": "approve", "label": "Publish", "style": "primary", "closes": True}],
        [{"id": "undo", "label": "Undo", "moves_to": "needs"},
         {"id": "fine", "label": "Fine", "closes": True}],
        # A button that reads "No" under an id the ladder does not count.
        [{"id": "decline", "label": " no ", "style": "danger", "closes": True}],
        [{"id": "fine", "label": "GOT  IT", "closes": True}],
    ],
)
def test_the_benchs_own_buttons_cannot_be_reworded_or_imitated(core, actions):
    with pytest.raises(bench.BenchError, match="Bench's own buttons") as caught:
        file(core, actions=actions)
    assert caught.value.status == 422
    assert core.tape.after(0, kind="bench.filed") == []


def test_a_filer_may_offer_the_benchs_own_buttons_as_they_are(core):
    card_id = file(core, actions=[
        {"id": "approve", "label": "Approve", "style": "primary", "closes": True},
        {"id": "no", "label": "No", "style": "danger", "needs_reason": True, "closes": True},
        {"id": "rework", "label": "Rework", "moves_to": "waiting"},
    ])
    assert [a["id"] for a in bench.get_card(core, card_id)["actions"]] == [
        "approve", "no", "rework"]


def test_an_event_printed_by_anyone_but_the_owner_never_decides_or_snoozes_a_card(core):
    """The functions always print as the owner. The folds do not believe a
    decision, a snooze or another filer's update that arrives any other way."""
    card_id = file(core)
    subject = f"bench:{card_id}"
    core.tape.print("bench.decided", {"card_id": card_id, "card_kind": "village.finding",
                                      "action": "approve", "label": "Approve", "closes": True},
                    actor="agent:scout", subject=subject)
    core.tape.print("bench.snoozed", {"card_id": card_id, "until": now_ms() + HOUR},
                    actor="token:other", subject=subject)
    core.tape.print("bench.updated", {"card_id": card_id, "title": "Changed"},
                    actor="token:other", subject=subject)
    core.tape.print("bench.withdrawn", {"card_id": card_id}, actor="token:other",
                    subject=subject)
    board = bench.board(core)
    assert [c["id"] for c in board["lanes"]["needs"]] == [card_id] and board["recent"] == []
    card = bench.get_card(core, card_id)
    assert card["status"] == "open" and card["title"] == "A finding"
    assert not card["snoozed"] and [h["type"] for h in card["history"]] == ["filed"]


def test_a_card_cannot_be_updated_without_end(core, monkeypatch):
    """One card's story is read whole, so it must stay a bounded read."""
    monkeypatch.setattr("jimmyhq.bench.rules.MAX_UPDATES", 3)
    card_id = file(core)
    for n in range(3):
        bench.update_card(core, card_id, by="agent:scout", note=f"Note {n}")
    with pytest.raises(bench.BenchError, match="too many times") as refused:
        bench.update_card(core, card_id, by="agent:scout", note="One more")
    assert refused.value.status == 409
    assert bench.get_card(core, card_id)["updates"] == 3
    bench.withdraw_card(core, card_id, by="agent:scout")


def test_a_subject_that_cannot_be_stored_cleanly_is_refused(core):
    for subject in ("task:a\x00b", "task:a\ud800"):
        with pytest.raises(bench.BenchError, match="subject") as refused:
            file(core, subject=subject)
        assert refused.value.status == 422


def test_every_change_to_a_card_survives_a_restart(core, cfg):
    waiting = file(core, title="Waiting")
    cards.decide(core, waiting, action="not_yet", note="After lunch")
    snoozed = file(core, title="Snoozed")
    cards.snooze(core, snoozed, now_ms() + HOUR)
    updated = file(core, title="First title")
    bench.update_card(core, updated, by="agent:scout", title="Second title", body="y" * 9000)
    withdrawn = file(core)
    bench.withdraw_card(core, withdrawn, by="agent:scout", note="Not needed")
    declined = file(core)
    cards.decide(core, declined, action="no", reason="Not this one.")
    before = bench.board(core)
    again = open_core(cfg)
    try:
        after = bench.board(again)
        for key in ("lanes", "counts", "needs_by_kind", "needs_by_source", "recent"):
            assert after[key] == before[key], key
        assert {c["id"] for c in after["lanes"]["waiting"]} == {waiting, snoozed}
        assert [c["title"] for c in after["lanes"]["needs"]] == ["Second title"]
        assert bench.get_card(again, updated)["body"] == "y" * 9000
        assert bench.get_card(again, withdrawn)["status"] == "withdrawn"
        assert bench.get_card(again, declined)["decision"]["reason"] == "Not this one."
    finally:
        again.close()


def test_the_lens_never_holds_a_body_a_why_or_details(core):
    file(core, body="SECRET-BODY " * 10, why="SECRET-WHY",
         details=[{"label": "Evidence", "text": "SECRET-DETAIL"}])
    big = file(core, body="SECRET-BIG " * 1000)
    bench.update_card(core, big, by="agent:scout", body="SECRET-REVISED " * 1000)
    cards.decide(core, big, action="no", reason="r" * 2000, note="n" * 2000)
    state = core.lenses.read(BenchLens())
    text = str(state)
    assert "SECRET" not in text
    assert len(state["recent"][0]["reason"]) == 200 and len(state["recent"][0]["note"]) == 200


# 2030-05-08 is a Wednesday. Times below are UTC.
def _utc(day, hour, minute=0, month=5):
    from datetime import UTC, datetime
    return int(datetime(2030, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _offered(core, now):
    from jimmyhq.bench import snooze
    return [(c["id"], c["until"]) for c in snooze.choices(core, now)]


def test_the_quick_snooze_choices_follow_the_owners_time_zone_not_the_servers(core):
    core.settings.set("owner.timezone", "Asia/Tokyo", actor="owner")
    # 01:20 UTC on the 8th is 10:20 on the 8th in Tokyo.
    assert _offered(core, _utc(8, 1, 20)) == [
        ("later", _utc(8, 5)),      # 14:00 in Tokyo
        ("tomorrow", _utc(9, 0)),   # 09:00 on the 9th in Tokyo
        ("week", _utc(13, 0)),      # 09:00 on Monday the 13th in Tokyo
    ]
    # 16:00 UTC on the 8th is already 01:00 on Thursday the 9th in Tokyo.
    assert _offered(core, _utc(8, 16)) == [
        ("later", _utc(8, 19)), ("tomorrow", _utc(10, 0)), ("week", _utc(13, 0))]


def test_the_quick_snooze_choices_follow_the_owners_settings(core):
    core.settings.set("owner.timezone", "Asia/Tokyo", actor="owner")
    core.settings.set("bench.snooze_later_hours", 1, actor="owner")
    core.settings.set("bench.snooze_morning_hour", 6, actor="owner")
    core.settings.set("owner.week_starts", "sunday", actor="owner")
    assert _offered(core, _utc(8, 1, 20)) == [
        ("later", _utc(8, 3)),       # 12:00 in Tokyo: an hour on, rounded up
        ("tomorrow", _utc(8, 21)),   # 06:00 on the 9th in Tokyo
        ("week", _utc(11, 21)),      # 06:00 on Sunday the 12th in Tokyo
    ]
    core.settings.set("owner.week_starts", "saturday", actor="owner")
    assert _offered(core, _utc(8, 1, 20))[-1] == ("week", _utc(10, 21))


def test_later_today_is_not_offered_once_it_would_be_tomorrow_for_the_owner(core):
    core.settings.set("owner.timezone", "Asia/Tokyo", actor="owner")
    assert [c for c, _ in _offered(core, _utc(8, 13))] == ["tomorrow", "week"]  # 22:00 there


def test_next_week_on_the_day_the_week_starts_means_the_week_after(core):
    core.settings.set("owner.timezone", "Asia/Tokyo", actor="owner")
    assert _offered(core, _utc(13, 1))[-1] == ("week", _utc(20, 0))  # asked on a Monday


def test_the_board_offers_the_snooze_choices_and_names_the_owners_zone(core, clock):
    assert bench.board(core)["timezone"] == "local"
    core.settings.set("owner.timezone", "Asia/Tokyo", actor="owner")
    clock.now = _utc(8, 1, 20)
    board = bench.board(core)
    assert board["timezone"] == "Asia/Tokyo"
    assert board["snooze_choices"] == [
        {"id": "later", "label": "Later today", "until": _utc(8, 5)},
        {"id": "tomorrow", "label": "Tomorrow morning", "until": _utc(9, 0)},
        {"id": "week", "label": "Next week", "until": _utc(13, 0)},
    ]
