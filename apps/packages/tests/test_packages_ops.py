"""What the owner does to a package: add, rename, change carrier, remove, and set a status
by hand. Each refusal says nothing was changed and what to do instead."""

from __future__ import annotations

import pytest
from jimmyhq_app import CallError
from packages.trackers import YES, Reading, Reply, Result
from packages.trackers.practice import Practice
from packages_rig import HOSE, START
from packages_service import no_answer, ok, refused, registered, tracking


def raises(rig, match: str, call: str, args: dict):
    with pytest.raises(CallError, match=match):
        rig.core.call(call, args)


def know(rig, *numbers: str) -> None:
    rig.service.on("register", ok([registered(n) for n in numbers]))
    rig.service.on("gettrackinfo", ok([tracking(n, "InTransit") for n in numbers]))


# --- adding ---------------------------------------------------------------------------------


def test_a_package_is_added_with_a_name_a_number_and_a_carrier_to_detect(rig):
    know(rig, HOSE)
    row = rig.add()
    assert (row["label"], row["number"], row["carrier"]) == ("Garden hose", HOSE, "")
    assert row["said"] == "Added Garden hose."
    assert row["status"] == "on_its_way" and row["registered"] is True


def test_a_tracking_number_is_cleaned_of_spaces_and_kept_as_typed_otherwise(rig):
    know(rig, "ZX100200300QQ")
    row = rig.add(number="  ZX1002 00300 QQ ")
    assert row["number"] == "ZX100200300QQ"


def test_a_package_without_a_name_is_refused_and_nothing_is_added(rig):
    raises(rig, r"Give the package a short name.*Nothing was added", "package.add",
           {"label": "  ", "number": HOSE})
    raises(rig, "at most 80 characters", "package.add", {"label": "x" * 81, "number": HOSE})
    assert rig.store.packages() == [] and rig.service.calls == []


def test_a_number_that_cannot_be_one_is_refused_before_anything_is_sent(rig):
    raises(rig, "Type the tracking number", "package.add", {"label": "Hose", "number": ""})
    raises(rig, "4 to 60 letters and digits", "package.add", {"label": "Hose", "number": "<b>"})
    raises(rig, "4 to 60 letters and digits", "package.add", {"label": "Hose", "number": "ab"})
    assert rig.store.packages() == [] and rig.service.calls == []


def test_the_same_number_twice_is_refused_naming_the_package_that_has_it(rig):
    know(rig, HOSE)
    rig.add()
    raises(rig, r"\"Garden hose\" already has that number. Nothing was added", "package.add",
           {"label": "Again", "number": HOSE.lower()})
    assert len(rig.store.packages()) == 1


def test_a_package_that_could_not_be_looked_up_just_now_is_kept_and_tried_again_later(rig):
    rig.service.on("register", no_answer())
    row = rig.add()
    assert "could not be looked up just now" in row["said"]
    assert "tried again at the next check" in row["said"]
    assert row["registered"] is False and len(rig.store.packages()) == 1


def test_a_number_the_service_refused_is_kept_with_its_words_beside_it(rig):
    rig.service.on("register", ok(rejected=[refused(
        HOSE, -18019903, "The carrier cannot be detected.")]))
    row = rig.add()
    assert "did not take it: The carrier cannot be detected." in row["said"]
    assert rig.core.call("overview")["rows"][0]["refused"] == "The carrier cannot be detected."


def test_a_carrier_typed_with_a_name_is_saved_so_the_picker_offers_it_next_time(rig):
    know(rig, HOSE)
    rig.add(carrier="3011", carrier_name="Corner Post")
    assert rig.core.call("overview")["carriers"] == [{"code": "3011", "name": "Corner Post"}]
    assert rig.core.call("overview")["rows"][0]["carrier_name"] == "Corner Post"


def test_a_saved_carrier_can_be_taken_out_of_the_list_and_packages_keep_theirs(rig):
    know(rig, HOSE)
    rig.add(carrier="3011", carrier_name="Corner Post")
    rig.core.call("carrier.forget", {"carrier": "3011"})
    overview = rig.core.call("overview")
    assert overview["carriers"] == [] and overview["rows"][0]["carrier"] == "3011"
    raises(rig, "not in your saved list", "carrier.forget", {"carrier": "3011"})


# --- renaming and changing the carrier ------------------------------------------------------


def test_a_package_is_renamed_and_nothing_else_about_it_changes(rig):
    know(rig, HOSE)
    row = rig.add()
    after = rig.core.call("package.rename", {"id": row["id"], "label": "Water hose"})
    assert after["label"] == "Water hose" and after["number"] == HOSE
    assert after["status"] == row["status"]
    raises(rig, "short name", "package.rename", {"id": row["id"], "label": ""})
    assert rig.package(row["id"])["label"] == "Water hose"


def test_a_package_that_is_not_there_is_said_not_to_be(rig):
    raises(rig, "There is no such package", "package.rename", {"id": 99, "label": "x"})
    raises(rig, "Say which package", "package.remove", {})


def test_a_carrier_change_on_a_registered_number_is_made_at_the_service_first(rig):
    know(rig, HOSE)
    row = rig.add()
    rig.service.on("changecarrier", ok([registered(HOSE, 3021)]))
    after = rig.core.call("package.carrier", {"id": row["id"], "carrier": "3021"})
    assert after["carrier"] == "3021" and "is changed" in after["said"]
    assert rig.package(row["id"])["carrier"] == "3021"


def test_a_carrier_change_the_service_refuses_changes_nothing_and_says_the_services_words(rig):
    know(rig, HOSE)
    row = rig.add()
    rig.service.on("changecarrier", ok(rejected=[refused(
        HOSE, -18019806, "Cannot change the carrier of a stopped number.")]))
    raises(rig, r"stopped number. Nothing was changed", "package.carrier",
           {"id": row["id"], "carrier": "3021"})
    assert rig.package(row["id"])["carrier"] == ""


def unanswered_change(rig):
    know(rig, HOSE)
    row = rig.add()
    rig.service.on("changecarrier", no_answer())
    after = rig.core.call("package.carrier", {"id": row["id"], "carrier": "3021"})
    rig.service.clear()
    return row, after


def test_a_carrier_change_with_no_clear_answer_is_marked_not_confirmed_not_changed(rig):
    row, after = unanswered_change(rig)
    assert after["said"].startswith("Carrier change not confirmed")
    assert after["carrier_unconfirmed"] == "3021"
    assert rig.package(row["id"])["carrier"] == ""  # Packages' record keeps the old one


def test_while_a_carrier_change_is_not_confirmed_the_number_is_read_without_a_carrier(rig):
    know(rig, HOSE)
    row = rig.add(carrier="3011")
    rig.service.on("changecarrier", no_answer())
    rig.core.call("package.carrier", {"id": row["id"], "carrier": "3021"})
    rig.service.script["gettrackinfo"] = [ok([tracking(HOSE, "InTransit", carrier=3021)])]
    rig.ctx.store.set_meta(last_sent=None)
    rig.core.call("refresh")
    assert rig.service.sent("gettrackinfo")[-1] == [{"number": HOSE}]  # no carrier pinned
    after = rig.package(row["id"])
    assert after["carrier"] == "3021" and after["note"] == ""  # 17TRACK had made it
    assert rig.core.call("overview", {})["rows"][0]["carrier_unconfirmed"] is None


def test_a_carrier_change_that_did_not_happen_is_said_once_a_read_shows_the_old_one(rig):
    row, _ = unanswered_change(rig)
    rig.service.script["gettrackinfo"] = [ok([tracking(HOSE, "InTransit", carrier=3011)])]
    rig.ctx.store.set_meta(last_sent=None)
    rig.core.call("refresh")
    after = rig.package(row["id"])
    assert after["carrier"] == "" and "did not happen" in after["note"]


def test_the_same_carrier_again_is_taken_as_done(rig):
    know(rig, HOSE)
    row = rig.add(carrier="3011")
    rig.service.on("changecarrier", ok(rejected=[refused(
        HOSE, -18019803, "The new carrier is the same.")]))
    same = rig.core.call("package.carrier", {"id": row["id"], "carrier": "3011"})
    assert same["carrier"] == "3011"


def test_a_carrier_change_before_the_number_is_registered_is_made_here_and_offers_it_again(rig):
    rig.service.on("register", ok(rejected=[refused(HOSE, -18019903, "No carrier.")]))
    row = rig.add()
    assert rig.package(row["id"])["refused"]
    after = rig.core.call("package.carrier", {"id": row["id"], "carrier": "3011"})
    assert after["carrier"] == "3011" and rig.package(row["id"])["refused"] == ""
    assert rig.service.asked() == ["register"]  # nothing was sent for the change itself


# --- removing -------------------------------------------------------------------------------


def test_a_removed_package_is_gone_for_good_and_the_service_is_asked_to_stop_tracking_it(rig):
    know(rig, HOSE)
    row = rig.add()
    rig.service.on("deletetrack", ok([{"number": HOSE, "carrier": 3011}]))
    said = rig.core.call("package.remove", {"id": row["id"]})["said"]
    assert said == "Garden hose is removed."
    assert rig.store.packages() == []
    assert HOSE.encode() not in (rig.ctx.app.data_dir / "packages.db").read_bytes()
    assert rig.service.sent("deletetrack") == [[{"number": HOSE}]]


def test_a_package_is_removed_even_when_the_service_cannot_be_asked_and_the_sentence_says_so(rig):
    know(rig, HOSE)
    row = rig.add()
    rig.service.on("deletetrack", no_answer())
    said = rig.core.call("package.remove", {"id": row["id"]})["said"]
    assert said.startswith("Garden hose is removed.") and "still holds the number" in said
    assert rig.store.packages() == []


def test_a_package_never_registered_is_removed_without_asking_the_service(rig):
    rig.service.on("register", no_answer())
    row = rig.add()
    rig.service.clear()
    rig.core.call("package.remove", {"id": row["id"]})
    assert rig.service.calls == []


def test_removing_a_package_takes_its_card_off_the_bench(make_rig):
    rig = make_rig(settings={"tell_when": "any_change"})
    know(rig, HOSE)
    row = rig.add()
    [card] = rig.cards()
    rig.core.call("package.remove", {"id": row["id"]})
    assert rig.cards() == [] and rig.core.cards[card["card_id"]]["withdrawn"] is True


# --- marking by hand ------------------------------------------------------------------------


def test_with_17track_a_package_can_be_marked_delivered_by_hand_and_stops_being_checked(rig):
    know(rig, HOSE)
    row = rig.add()
    after = rig.core.call("package.mark", {"id": row["id"], "status": "delivered"})
    assert after["status"] == "delivered" and after["tab"] == "delivered"
    assert after["archived"] is True and after["event"] == "You marked it delivered."
    rig.service.clear()
    rig.clock.advance(5 * 3_600_000)
    rig.sync()
    assert rig.service.calls == []


def test_with_17track_no_other_status_can_be_set_by_hand(rig):
    know(rig, HOSE)
    row = rig.add()
    raises(rig, "only as: delivered. Nothing was changed", "package.mark",
           {"id": row["id"], "status": "problem"})
    raises(rig, "only as", "package.mark", {"id": row["id"], "status": "teleported"})


def test_a_mark_by_hand_can_be_taken_back_and_the_package_is_checked_again(rig):
    know(rig, HOSE)
    row = rig.add()
    rig.core.call("package.mark", {"id": row["id"], "status": "delivered"})
    back = rig.core.call("package.unmark", {"id": row["id"]})
    assert back["status"] == "waiting" and back["archived"] is False
    assert back["status_by"] == "tracker" and back["tab"] == "way"


def test_a_status_that_did_not_come_from_the_owner_cannot_be_taken_back(rig):
    know(rig, HOSE)
    row = rig.add()
    raises(rig, "did not come from you", "package.unmark", {"id": row["id"]})


def test_practice_tracks_nothing_outside_and_takes_any_status_by_hand(make_rig):
    rig = make_rig(tracker="practice")
    row = rig.add()
    assert rig.service.calls == [] and rig.core.fetched == []
    for status in ("on_its_way", "out_for_delivery", "problem", "not_found", "expired",
                   "pickup", "delivered"):
        assert rig.core.call("package.mark", {"id": row["id"], "status": status})[
            "status"] == status


def test_practice_asks_for_no_key_registers_nothing_and_a_refresh_says_there_was_nothing_to_check(
        make_rig):
    rig = make_rig(tracker="practice", secrets={})
    rig.add()
    done = rig.core.call("refresh")
    assert done["ok"] and "looks nothing up" in done["said"]
    assert rig.core.call("overview")["tracker"]["key_problem"] is None
    assert rig.store.packages()[0]["registered_ms"] is None
    assert rig.sync().startswith("The tracking service looks nothing up")


def test_practice_has_no_gap_on_refresh_now_because_it_asks_nobody(make_rig):
    rig = make_rig(tracker="practice")
    rig.add()
    rig.core.call("refresh")
    rig.core.call("refresh")  # not refused


def test_a_tracker_added_later_is_used_through_the_same_interface(make_rig):
    class Fixed(Practice):
        id, label, outside, registers = "fixed", "Fixed", True, False

        def read(self, items):
            return Reply(YES, items=tuple(Result(i.id, YES, reading=Reading(
                "out_for_delivery", "On the van", START)) for i in items))

    rig = make_rig(tracker="fixed", trackers={"practice": Practice(), "fixed": Fixed()})
    row = rig.add()
    assert rig.package(row["id"])["status"] == "out_for_delivery"
    assert rig.package(row["id"])["event"] == "On the van"
    assert rig.package(row["id"])["registered_ms"] is None  # it registers nothing


# --- the sidebar's number ---------------------------------------------------------------------


def test_the_program_counts_packages_out_for_delivery_or_with_a_problem_with_no_page_open(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.add()
    assert rig.core.count == 0  # on its way does not need the owner
    rig.store.set(rig.store.packages()[0]["id"], status="out_for_delivery")
    rig.core.call("overview", {})  # any call the program handles says the number again
    assert rig.core.count == 1
