"""The permission ladder: what may happen without asking."""

from __future__ import annotations

import pytest

from jimmyhq import bench
from jimmyhq.bench import cards, ladder

ROW = ("agent:scout", "posts", "publish")


def ask(core, category="publish"):
    """File the card an "approve" level action waits on."""
    return bench.file_card(
        core, filed_by=ROW[0], kind="posts.draft", source="Posts", title="Publish the draft?",
        ladder={"actor": ROW[0], "tool": ROW[1], "action": ROW[2], "category": category},
    )


def test_a_row_nobody_has_set_is_approve(core):
    assert bench.check(core, *ROW, "publish") == "approve"
    assert ladder.table(core)["rows"] == []


def test_only_the_owner_can_raise_a_level(core):
    for by in ("agent:scout", "token:wall", "system", "app:notes"):
        with pytest.raises(bench.LadderError, match="Only the owner") as caught:
            bench.set_level(core, *ROW, "alone", by=by)
        assert caught.value.status == 403
    assert core.tape.after(0, kind="ladder.set") == []
    assert bench.set_level(core, *ROW, "alone", by="owner")["level"] == "alone"
    assert bench.check(core, *ROW, "publish") == "alone"


def test_anything_can_lower_a_level_but_not_raise_it_back(core):
    bench.set_level(core, *ROW, "alone", by="owner")
    assert bench.set_level(core, *ROW, "read", by="agent:scout")["level"] == "read"
    assert bench.check(core, *ROW, "publish") == "read"
    with pytest.raises(bench.LadderError, match="Only the owner"):
        bench.set_level(core, *ROW, "approve", by="agent:scout")


CAUTIOUS = ("send", "delete", "spend", "access")
OLD_ROW = {"actor": ROW[0], "tool": ROW[1], "action": ROW[2]}


@pytest.mark.parametrize("category", CAUTIOUS)
def test_the_owner_can_set_a_cautious_action_to_alone_in_one_step(core, category):
    """No second question and no limit first: the list only picks where a row starts."""
    assert bench.check(core, *ROW, category) == "approve"  # where it starts
    row = bench.set_level(core, *ROW, "alone", by="owner", category=category)
    assert (row["level"], row["cautious"], row["set_by"]) == ("alone", True, "owner")
    assert bench.check(core, *ROW, category) == "alone"
    assert len(core.tape.after(0, kind="ladder.set")) == 1
    card_id = bench.record_alone_action(core, actor=ROW[0], tool=ROW[1], action=ROW[2],
                                        category=category, title="Did it")
    assert bench.get_card(core, card_id)["lane"] == "done"


@pytest.mark.parametrize("category", CAUTIOUS)
def test_nobody_but_the_owner_can_set_a_cautious_action_to_alone(core, category):
    for by in ("agent:scout", "token:wall", "bench", "app:notes"):
        with pytest.raises(bench.LadderError, match="Only the owner"):
            bench.set_level(core, *ROW, "alone", by=by, category=category)
    core.tape.print("ladder.set", {**OLD_ROW, "level": "alone", "category": category,
                                   "unbarred": True}, actor="token:wall")
    assert bench.check(core, *ROW, category) == "approve"


def test_the_cautious_kinds_are_a_setting_that_marks_rows_and_bars_nothing(core):
    assert core.settings.get("bench.caution_categories") == list(CAUTIOUS)
    assert ladder.table(core)["caution_categories"] == list(CAUTIOUS)
    assert bench.cautious(core, "send") and not bench.cautious(core, "create")
    bench.set_level(core, *ROW, "approve", by="owner", category="create")
    assert ladder.row_of(core, *ROW)["cautious"] is False
    core.settings.set("bench.caution_categories", ["create"], actor="owner")  # the owner's list
    assert ladder.row_of(core, *ROW)["cautious"] is True
    assert not bench.cautious(core, "send")
    assert bench.set_level(core, *ROW, "alone", by="owner")["level"] == "alone"
    assert ladder.owner_may_choose_any_level(core, level="alone", effect="create") is None


def test_events_that_break_the_rules_are_ignored_by_the_fold(core):
    """The service refuses these, and the lens does not believe them either."""
    core.tape.print("ladder.set", {**OLD_ROW, "level": "alone"}, actor="agent:scout")
    assert bench.check(core, *ROW, "publish") == "approve"
    core.tape.print("ladder.set", {**OLD_ROW, "level": "alone", "category": "delete"},
                    actor="owner")
    assert bench.check(core, *ROW, "publish") == "approve"


def test_a_replay_never_grants_what_was_once_refused(core, cfg):
    """Events from when four categories could never be "alone". Whatever was refused
    then is refused each time the Tape is read again, by this process or the next."""
    from jimmyhq.core import open_core

    pay = {"actor": "agent:scout", "tool": "bank", "action": "pay"}
    tidy = {"actor": "agent:scout", "tool": "files", "action": "tidy"}
    for category in ("money", "message_person", "delete", "access"):
        row = {**OLD_ROW, "action": category}
        core.tape.print("ladder.set", {**row, "level": "alone", "category": category},
                        actor="owner")
    # A row first known to move money, then asked for under a gentler name.
    core.tape.print("ladder.set", {**pay, "level": "approve", "category": "money"},
                    actor="owner")
    core.tape.print("ladder.set", {**pay, "level": "alone", "category": "publish"},
                    actor="owner")
    # A row raised before anyone said what it was, that then turned out to delete.
    core.tape.print("ladder.set", {**tidy, "level": "alone"}, actor="owner")
    cards.decide(core, bench.file_card(
        core, filed_by="agent:scout", kind="files.tidy", source="Files", title="Tidy up?",
        ladder={**tidy, "category": "delete"}), action="approve")

    def all_ask(c):
        assert {r["level"] for r in ladder.table(c)["rows"]} == {"approve"}
        for category in ("money", "message_person", "delete", "access"):
            assert bench.check(c, ROW[0], ROW[1], category, category) == "approve"
        assert bench.check(c, "agent:scout", "bank", "pay", "publish") == "approve"
        assert bench.check(c, "agent:scout", "files", "tidy", "tidy") == "approve"
        with pytest.raises(bench.LadderError, match="without asking"):
            bench.record_alone_action(c, actor="agent:scout", tool="bank", action="pay",
                                      category="money", title="Paid anyway")

    all_ask(core)
    again = open_core(cfg)  # a new process folds the whole Tape from the start
    try:
        all_ask(again)
    finally:
        again.close()
    # An old "alone" on a row nobody had named answers "approve" for a barred kind.
    plain = {**OLD_ROW, "action": "plain"}
    core.tape.print("ladder.set", {**plain, "level": "alone"}, actor="owner")
    assert bench.check(core, ROW[0], ROW[1], "plain", "publish") == "alone"
    assert bench.check(core, ROW[0], ROW[1], "plain", "delete") == "approve"
    # Only the owner saying so now changes any of it, and it takes one step.
    assert bench.set_level(core, "agent:scout", "bank", "pay", "alone",
                           by="owner")["level"] == "alone"
    assert bench.check(core, "agent:scout", "bank", "pay", "money") == "alone"
    assert bench.set_level(core, ROW[0], ROW[1], "plain", "alone", by="owner",
                           category="delete")["level"] == "alone"
    assert bench.check(core, ROW[0], ROW[1], "plain", "delete") == "alone"


def test_acting_alone_files_a_done_card_with_an_undo(core):
    bench.set_level(core, *ROW, "alone", by="owner")
    card_id = bench.record_alone_action(
        core, actor=ROW[0], tool=ROW[1], action=ROW[2], category="publish",
        title="Published the spring post", summary="It is live.", undo=True,
    )
    board = bench.board(core)
    assert board["counts"] == {"needs": 0, "done": 1, "waiting": 0}
    card = board["lanes"]["done"][0]
    assert (card["id"], card["kind"], card["source"]) == (card_id, "posts.publish", "posts")
    assert [a["id"] for a in card["actions"]] == ["ok", "undo"]
    acted = core.tape.after(0, kind="ladder.acted")[0]
    assert acted.payload["card_id"] == card_id and acted.actor == ROW[0]
    assert ladder.row_of(core, *ROW)["acted"] == 1


def test_acting_alone_without_that_level_is_refused_and_files_nothing(core):
    with pytest.raises(bench.LadderError, match="without asking") as caught:
        bench.record_alone_action(core, actor=ROW[0], tool=ROW[1], action=ROW[2],
                                  category="publish", title="Published")
    assert caught.value.status == 403
    assert core.tape.after(0, kind="bench.filed") == []


def test_an_undo_on_an_alone_action_drops_the_row_back_to_approve(core):
    bench.set_level(core, *ROW, "alone", by="owner")
    card_id = bench.record_alone_action(core, actor=ROW[0], tool=ROW[1], action=ROW[2],
                                        category="publish", title="Published", undo=True)
    cards.decide(core, card_id, action="undo")
    row = ladder.row_of(core, *ROW)
    assert (row["level"], row["undos"], row["set_by"]) == ("approve", 1, "bench")
    assert bench.check(core, *ROW, "publish") == "approve"


def test_a_decline_on_an_alone_row_drops_it_back_to_approve(core):
    card_id = ask(core)
    bench.set_level(core, *ROW, "alone", by="owner")
    cards.decide(core, card_id, action="no", reason="Not this one.")
    row = ladder.row_of(core, *ROW)
    assert (row["level"], row["declines"], row["streak"]) == ("approve", 1, 0)


def test_acknowledging_a_done_card_leaves_the_level_alone(core):
    bench.set_level(core, *ROW, "alone", by="owner")
    card_id = bench.record_alone_action(core, actor=ROW[0], tool=ROW[1], action=ROW[2],
                                        category="publish", title="Published", undo=True)
    cards.decide(core, card_id, action="ok")
    assert bench.check(core, *ROW, "publish") == "alone"


def test_approvals_are_tallied_and_a_streak_only_suggests_a_promotion(core):
    core.settings.set("bench.promotion_streak", 3, actor="owner")
    for _ in range(2):
        cards.decide(core, ask(core), action="approve")
    assert ladder.row_of(core, *ROW)["could_promote"] is False
    cards.decide(core, ask(core), action="approve")
    row = ladder.row_of(core, *ROW)
    assert (row["approvals"], row["streak"], row["could_promote"]) == (3, 3, True)
    assert row["level"] == "approve"  # suggesting is all it does
    cards.decide(core, ask(core), action="no", reason="Not today.")
    row = ladder.row_of(core, *ROW)
    assert (row["approvals"], row["declines"], row["could_promote"]) == (3, 1, False)


def test_the_suggestion_appears_after_ten_approvals_by_default_and_changes_nothing(core):
    assert core.settings.get("bench.promotion_streak") == 10
    for _ in range(9):
        cards.decide(core, ask(core, category="send"), action="approve")
    assert ladder.row_of(core, *ROW)["could_promote"] is False
    cards.decide(core, ask(core, category="send"), action="approve")
    row = ladder.row_of(core, *ROW)
    assert (row["streak"], row["cautious"], row["could_promote"]) == (10, True, True)
    assert row["level"] == "approve" and bench.check(core, *ROW, "send") == "approve"
    assert core.tape.after(0, kind="ladder.set") == []  # nothing set itself
    assert ladder.suggests_promotion({"level": "alone", "streak": 99}, 10) is False


def test_a_no_or_an_undo_keeps_the_level_when_the_owner_switched_that_off(core, cfg):
    from jimmyhq.core import open_core

    core.settings.set("bench.undo_drops_alone", False, actor="owner")
    bench.set_level(core, *ROW, "alone", by="owner", category="send")
    card_id = bench.record_alone_action(core, actor=ROW[0], tool=ROW[1], action=ROW[2],
                                        category="send", title="Sent", undo=True)
    cards.decide(core, card_id, action="undo")
    row = ladder.row_of(core, *ROW)
    assert (row["level"], row["undos"], row["streak"]) == ("alone", 1, 0)
    # The answer of that moment is on the record: switching the safety back on later
    # does not reach into the past, and it does apply to the next one.
    core.settings.set("bench.undo_drops_alone", True, actor="owner")
    again = open_core(cfg)
    try:
        assert bench.check(again, *ROW, "send") == "alone"
    finally:
        again.close()
    cards.decide(core, ask(core, category="send"), action="no", reason="Not this one.")
    assert bench.check(core, *ROW, "send") == "approve"


def test_a_not_yet_and_cards_without_a_row_do_not_touch_the_tally(core):
    cards.decide(core, ask(core), action="not_yet")
    plain = bench.file_card(core, filed_by="agent:scout", kind="village.finding",
                            source="Village bench", title="A finding")
    cards.decide(core, plain, action="approve")
    assert ladder.table(core)["rows"] == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("", "posts", "publish", "publish"), "actor cannot be empty"),
        (("agent:scout", "Posts!", "publish", "publish"), "tool must be"),
        (("agent:scout", "posts", "", "publish"), "action must be"),
        (("agent:scout", "posts", "publish", None), "category must be"),
        (("agent:scout", "posts", "publish", "Send Money"), "category must be"),
    ],
)
def test_checking_with_a_malformed_row_is_refused_not_guessed(core, args, message):
    with pytest.raises(bench.BenchError, match=message):
        bench.check(core, *args)


def test_an_unknown_level_is_refused(core):
    with pytest.raises(bench.LadderError, match="level must be one of"):
        bench.set_level(core, *ROW, "root", by="owner")


def test_an_alone_row_keeps_its_level_whatever_kind_it_turns_out_to_be(core):
    other = ("agent:scout", "files", "tidy")
    bench.set_level(core, *other, "alone", by="owner")  # nobody has said what it is yet
    cards.decide(core, bench.file_card(
        core, filed_by=other[0], kind="files.tidy", source="Files", title="Tidy up?",
        ladder={"actor": other[0], "tool": other[1], "action": other[2], "category": "delete"},
    ), action="approve")
    row = ladder.row_of(core, *other)
    assert (row["level"], row["set_by"], row["cautious"]) == ("alone", "owner", True)
    assert bench.check(core, *other, "delete") == "alone"


def test_the_ladder_survives_a_restart(core, cfg):
    from jimmyhq.core import open_core

    bench.set_level(core, *ROW, "alone", by="owner", category="publish")
    cards.decide(core, ask(core), action="no", reason="Not like this.")
    before = ladder.table(core)
    again = open_core(cfg)
    try:
        assert ladder.table(again) == before
        assert bench.check(again, *ROW, "publish") == "approve"
    finally:
        again.close()


def test_a_click_that_loses_a_race_with_another_process_is_not_tallied(core, cfg, monkeypatch):
    """The worker withdraws the card after the web process found it open. The
    owner is told the click did not count, so the ladder must not count it."""
    from jimmyhq.core import open_core

    other = open_core(cfg)
    try:
        card_id = ask(core)
        real = core.tape.print

        def slow_print(kind, payload, **kw):
            if kind == "bench.decided":
                bench.withdraw_card(other, card_id, by=ROW[0])
            return real(kind, payload, **kw)

        monkeypatch.setattr(core.tape, "print", slow_print)
        with pytest.raises(bench.BenchError, match="already closed"):
            cards.decide(core, card_id, action="approve")
        assert [e.kind for e in core.tape.by_subject(f"bench:{card_id}")] == [
            "bench.filed", "bench.withdrawn"]
        row = ladder.row_of(core, *ROW)
        assert (row["approvals"], row["streak"]) == (0, 0)
    finally:
        other.close()


def test_a_refused_click_already_on_the_tape_is_not_tallied(core):
    """Before card writes were guarded, a decision that lost a race with a
    withdrawal stayed on the Tape. The owner was told it did not count, and
    events are never rewritten, so the ladder has to pass it over."""
    card_id = ask(core)
    bench.withdraw_card(core, card_id, by=ROW[0])
    stale = {"card_id": card_id, "card_kind": "posts.draft", "action": "approve",
             "label": "Approve", "closes": True,
             "ladder": {"actor": ROW[0], "tool": ROW[1], "action": ROW[2],
                        "category": "publish"}}
    core.tape.print("bench.decided", stale, actor="owner", subject=f"bench:{card_id}")
    row = ladder.row_of(core, *ROW)
    assert (row["approvals"], row["streak"]) == (0, 0)

    # The same for a second click on a card the first click closed.
    again = ask(core)
    cards.decide(core, again, action="approve")
    core.tape.print("bench.decided", {**stale, "card_id": again}, actor="owner",
                    subject=f"bench:{again}")
    row = ladder.row_of(core, *ROW)
    assert (row["approvals"], row["streak"]) == (1, 1)
