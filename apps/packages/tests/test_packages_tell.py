"""Telling the owner: one card on the Bench for one change, never repeated, never carrying a
tracking number. And that nothing about a package reaches the owner's record."""

from __future__ import annotations

import json

import pytest
from jimmyhq_app import CallError
from packages import tell
from packages.config import MANIFEST
from packages_rig import DAY, HOSE, HOUR, RAKE
from packages_service import KEY, event, ok, quota, registered, tracking


def watch(rig, *statuses: str, number: str = HOSE):
    """A package added, then read as each status in turn at each check."""
    rig.service.on("register", ok([registered(number)]))
    rig.service.on("gettrackinfo", *[ok([tracking(number, s)]) for s in statuses])
    rig.service.on("getquota", quota())
    return rig.add(number=number)


def check(rig):
    rig.clock.advance(5 * HOUR)
    return rig.sync()


def titles(rig) -> list[str]:
    return [c["title"] for c in rig.cards()]


# --- which changes count --------------------------------------------------------------------


def test_the_rule_for_each_choice_of_tell_when():
    assert tell.should_tell("off", "on_its_way", "delivered") is False
    assert tell.should_tell("delivered", "on_its_way", "delivered") is True
    assert tell.should_tell("delivered", "on_its_way", "problem") is False
    assert tell.should_tell("delivered_or_problem", "on_its_way", "problem") is True
    assert tell.should_tell("delivered_or_problem", "waiting", "out_for_delivery") is False
    assert tell.should_tell("any_change", "waiting", "on_its_way") is True
    assert tell.should_tell("any_change", "on_its_way", "on_its_way") is False


def test_by_default_a_delivery_and_a_problem_are_told_and_other_news_is_not(rig):
    watch(rig, "InTransit", "OutForDelivery", "DeliveryFailure", "Delivered")
    assert rig.cards() == []  # on its way: not worth a card
    check(rig)
    assert rig.cards() == []  # out for delivery: not by default
    check(rig)
    assert titles(rig) == ["Garden hose has a problem."]
    check(rig)
    assert titles(rig) == ["Garden hose was delivered."]  # the newer card took its place


def test_off_tells_nothing(make_rig):
    rig = make_rig(settings={"tell_when": "off"})
    watch(rig, "Delivered")
    assert rig.core.cards == {}


def test_delivered_alone_does_not_tell_a_problem(make_rig):
    rig = make_rig(settings={"tell_when": "delivered"})
    watch(rig, "DeliveryFailure", "Delivered")
    assert rig.core.cards == {}
    check(rig)
    assert titles(rig) == ["Garden hose was delivered."]


def test_any_change_tells_every_status_change(make_rig):
    rig = make_rig(settings={"tell_when": "any_change"})
    watch(rig, "InTransit", "OutForDelivery")
    assert titles(rig) == ["Garden hose is on its way."]
    check(rig)
    assert titles(rig) == ["Garden hose is out for delivery."]
    assert len(rig.core.cards) == 2  # the first was withdrawn, not left beside it


# --- once for each change --------------------------------------------------------------------


def test_a_change_is_told_once_however_often_the_package_is_checked_after(rig):
    watch(rig, "Delivered")
    assert len(rig.core.cards) == 1
    for _ in range(4):
        check(rig)
    assert len(rig.core.cards) == 1
    rig.clock.advance(HOUR)
    rig.core.call("refresh")
    assert len(rig.core.cards) == 1


def test_a_package_that_goes_back_and_forth_is_told_for_each_new_change(rig):
    watch(rig, "DeliveryFailure", "InTransit", "DeliveryFailure")
    check(rig)
    check(rig)
    assert len(rig.core.cards) == 2
    assert len(rig.cards()) == 1  # only the newest stays on the Bench


def test_a_card_the_bench_could_not_take_is_kept_waiting_and_filed_once_later(rig):
    real = rig.core._bench_file  # noqa: SLF001

    def refuse(args):
        raise CallError("quota", "Your Bench already has 10 open cards from this app.")

    rig.core._bench_file = refuse  # noqa: SLF001
    row = watch(rig, "Delivered")
    assert rig.core.cards == {} and rig.package(row["id"])["tell_status"] == "delivered"
    assert rig.core.call("overview")["rows"][0]["waiting_to_tell"] is True
    rig.core._bench_file = real  # noqa: SLF001
    rig.clock.advance(5 * 60_000)
    rig.sync()
    assert titles(rig) == ["Garden hose was delivered."]
    assert rig.package(row["id"])["tell_status"] == ""
    rig.clock.advance(DAY)
    rig.sync()
    assert len(rig.core.cards) == 1


def test_a_card_waiting_is_dropped_when_the_owner_turns_telling_off(rig):
    real = rig.core._bench_file  # noqa: SLF001
    rig.core._bench_file = lambda a: (_ for _ in ()).throw(CallError("quota", "Full."))  # noqa: SLF001
    row = watch(rig, "Delivered")
    rig.core._bench_file = real  # noqa: SLF001
    rig.core.change_setting("tell_when", "off")
    rig.clock.advance(5 * 60_000)
    rig.sync()
    assert rig.core.cards == {} and rig.package(row["id"])["tell_status"] == ""


def test_a_package_the_owner_marks_delivered_by_hand_has_its_card_like_one_a_tracker_read(rig):
    watch(rig, "InTransit")
    rig.core.call("package.mark", {"id": 1, "status": "delivered"})
    assert titles(rig) == ["Garden hose was delivered."]
    assert rig.package(1)["tell_status"] == "" and rig.package(1)["card_id"]


def test_marking_a_package_delivered_by_hand_twice_is_told_once(rig):
    watch(rig, "InTransit")
    rig.core.call("package.mark", {"id": 1, "status": "delivered"})
    rig.core.call("package.mark", {"id": 1, "status": "delivered"})
    assert len(rig.core.cards) == 1


def test_a_status_the_owner_sets_by_hand_other_than_delivered_is_never_told_back_to_him(
        make_rig):
    rig = make_rig(tracker="practice", settings={"tell_when": "any_change"})
    row = rig.add()
    for status in ("on_its_way", "problem", "pickup", "expired"):
        rig.core.call("package.mark", {"id": row["id"], "status": status})
    assert rig.core.cards == {}


def test_a_package_marked_delivered_by_hand_is_not_told_when_the_owner_asked_for_nothing(
        make_rig):
    rig = make_rig(tracker="practice", settings={"tell_when": "off"})
    row = rig.add()
    rig.core.call("package.mark", {"id": row["id"], "status": "delivered"})
    assert rig.core.cards == {}


def test_taking_back_a_hand_marked_delivery_does_not_file_a_card(make_rig):
    rig = make_rig(tracker="practice")
    row = rig.add()
    rig.core.call("package.mark", {"id": row["id"], "status": "delivered"})
    rig.core.call("package.unmark", {"id": row["id"]})
    assert len(rig.core.cards) == 1  # the one for the delivery, and none for taking it back


def test_closing_the_card_clears_it_from_the_package(rig):
    row = watch(rig, "Delivered")
    [card] = rig.cards()
    assert rig.package(row["id"])["card_id"] == card["card_id"]
    rig.core.decide(card["card_id"], "ok")
    assert rig.package(row["id"])["card_id"] is None


# --- what a card says ------------------------------------------------------------------------


def test_a_card_has_the_packages_name_and_its_status_in_words_and_no_number(rig):
    watch(rig, "Delivered")
    [card] = rig.cards()
    assert card["title"] == "Garden hose was delivered."
    assert card["kind"] == "app.packages.update"
    assert card["subject"] == "app.packages:package:1"
    assert HOSE not in json.dumps(card) and HOSE.lower() not in json.dumps(card).lower()
    assert [a["id"] for a in card["actions"]] == ["ok"] and card["actions"][0]["closes"]


def test_a_card_never_carries_the_carriers_words_or_the_place_of_the_last_event(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "Delivered", event=event(
        "Left at 14 Elm Street, front door", "Springfield"))]))
    rig.add()
    [card] = rig.cards()
    assert "Elm" not in json.dumps(card) and "Springfield" not in json.dumps(card)
    assert rig.package(1)["event"].startswith("Left at 14 Elm Street")  # kept in its store


def test_a_card_for_a_package_with_a_long_name_stays_within_the_benchs_limits(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "Delivered")]))
    rig.add(label="W" * 80)
    assert len(rig.cards()[0]["title"]) <= 200


# --- nothing reaches the record --------------------------------------------------------------


def test_nothing_is_written_on_the_owners_record_and_the_app_is_marked_private(rig):
    watch(rig, "Delivered")
    rig.add("Rake", RAKE)
    rig.core.call("package.mark", {"id": 2, "status": "delivered"})
    rig.core.call("package.remove", {"id": 2})
    rig.sync()
    assert rig.core.events == []
    with pytest.raises(CallError, match="private"):
        rig.app._ask("tape.print", {"kind": "x", "payload": {}})  # noqa: SLF001
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["private"] is True and "tape.read" not in manifest["permissions"]


def test_no_tracking_number_is_anywhere_but_the_apps_own_store(rig):
    watch(rig, "Delivered")
    rig.add("Rake", RAKE)
    rig.core.call("refresh")
    assert HOSE not in rig.wherever_it_could_be_written()
    assert RAKE not in rig.wherever_it_could_be_written()
    assert KEY not in rig.wherever_it_could_be_written()
    assert HOSE in "\n".join(rig.ctx.store.conn.iterdump())
