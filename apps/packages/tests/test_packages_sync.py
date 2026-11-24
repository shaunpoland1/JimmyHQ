"""Checking the packages: the batches, the schedule the job keeps, a failed check that
changes nothing and is tried again soon, the gap Refresh now keeps, and a number that is
registered once and charged once. Every answer is canned; nothing reaches a network."""

from __future__ import annotations

import pytest
from packages_rig import DAY, HOSE, HOUR, MINUTE, START
from packages_service import (
    no_answer,
    ok,
    quota,
    refused,
    registered,
    reply,
    tracking,
)

NUMBERS = [f"ZX1002004{n:02d}QQ" for n in range(45)]


def stock(rig, count: int, *, registered_already: bool = False) -> list[int]:
    """Packages put straight in the store, so a test need not add each one."""
    ids = []
    for n in range(count):
        row = {"label": f"Parcel {n}", "number": NUMBERS[n], "created": START,
               "updated": START}
        if registered_already:
            row.update(registered_with="seventeentrack", registered_ms=START)
        ids.append(rig.store.insert("package", row))
    return ids


def answer_all(rig, status: str = "InTransit") -> None:
    """Whatever numbers are asked about are accepted, and read as ``status``."""
    def make(path):
        def answer(args):
            body = args["json"]
            if path == "register":
                return ok([registered(b["number"]) for b in body])
            return ok([tracking(b["number"], status) for b in body])
        return answer
    for path in ("register", "gettrackinfo"):
        rig.service.on(path, make(path))


def test_the_job_checks_at_once_the_first_time_and_then_waits_for_the_interval(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("getquota", quota())
    rig.store.insert("package", {"label": "Garden hose", "number": HOSE, "created": START,
                                 "updated": START})
    assert "Checked 1 package" in rig.sync()
    asked = len(rig.service.calls)
    rig.clock.advance(4 * HOUR - MINUTE)
    assert rig.sync().startswith("Next check in")
    assert len(rig.service.calls) == asked
    rig.clock.advance(MINUTE)
    assert "Checked 1 package" in rig.sync()
    assert len(rig.service.calls) > asked


def test_the_daily_time_brings_a_check_earlier_than_the_interval_in_the_owners_zone(make_rig):
    # 08:00 UTC is 03:00 in New York, so 06:00 there is three hours away.
    rig = make_rig(settings={"interval_hours": 48, "daily_time": "06:00"},
                   timezone="America/New_York")
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("getquota", quota())
    rig.store.insert("package", {"label": "Garden hose", "number": HOSE, "created": START,
                                 "updated": START})
    rig.sync()
    asked = len(rig.service.calls)
    rig.clock.advance(3 * HOUR - MINUTE)
    rig.sync()
    assert len(rig.service.calls) == asked
    rig.clock.advance(MINUTE)
    rig.sync()
    assert len(rig.service.calls) > asked


def test_with_no_daily_time_only_the_interval_counts(make_rig):
    rig = make_rig(settings={"interval_hours": 48, "daily_time": ""})
    assert rig.core.call("overview")["schedule"]["daily"] == ""
    rig.store.insert("package", {"label": "Garden hose", "number": HOSE, "created": START,
                                 "updated": START})
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("getquota", quota())
    rig.sync()
    rig.clock.advance(47 * HOUR)
    assert rig.sync().startswith("Next check in")


def test_a_daily_time_that_cannot_be_read_is_said_on_the_screen_and_06_00_is_used(make_rig):
    rig = make_rig(settings={"daily_time": "after lunch"})
    state = rig.core.call("overview")["schedule"]
    assert state["daily"] == "06:00" and "after lunch" in state["clock_note"]


def test_a_failed_check_changes_nothing_and_is_tried_again_after_the_retry_time(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    row = rig.add()
    before = dict(rig.package(row["id"]))
    rig.service.script.clear()
    rig.service.on("gettrackinfo", no_answer())
    rig.clock.advance(4 * HOUR)
    said = rig.sync()
    assert "Nothing was changed" in said and "try again in 15 minutes" in said
    after = rig.package(row["id"])
    assert (after["status"], after["checked_ms"], after["event"]) == (
        before["status"], before["checked_ms"], before["event"])
    asked = len(rig.service.calls)
    rig.clock.advance(14 * MINUTE)
    assert rig.sync().startswith("Next check in")
    assert len(rig.service.calls) == asked  # not before the retry time
    rig.clock.advance(MINUTE)
    rig.sync()
    assert len(rig.service.calls) == asked + 1


def test_each_failure_in_a_row_waits_twice_as_long_up_to_the_cap(make_rig):
    rig = make_rig(settings={"retry_minutes": 15, "retry_longest_minutes": 40})
    stock(rig, 1, registered_already=True)
    rig.service.on("gettrackinfo", no_answer())
    waits = []
    for _ in range(4):
        waits.append(rig.sync().split("try again in ")[1].rstrip("."))
        rig.clock.advance(int(waits[-1].split()[0]) * MINUTE)
    assert waits == ["15 minutes", "30 minutes", "40 minutes", "40 minutes"]


def test_a_good_check_after_failures_goes_back_to_the_usual_interval(rig):
    stock(rig, 1, registered_already=True)
    rig.service.on("gettrackinfo", no_answer(), ok([tracking(NUMBERS[0], "InTransit")]))
    rig.sync()
    rig.clock.advance(15 * MINUTE)
    assert "Checked 1 package" in rig.sync()
    assert rig.store.get_meta("fails") == 0
    rig.clock.advance(3 * HOUR)
    assert rig.sync().startswith("Next check in")


def test_an_unreadable_answer_is_a_failed_check_that_changes_nothing(rig):
    stock(rig, 1, registered_already=True)
    rig.service.on("gettrackinfo", reply(200, text="<html>Maintenance</html>"))
    said = rig.sync()
    assert "could not be read" in said and "Nothing was changed" in said
    assert rig.package(1)["status"] == "waiting" and rig.package(1)["checked_ms"] is None


def test_a_refusal_of_the_key_ends_the_check_and_says_so_without_changing_anything(rig):
    stock(rig, 1)
    rig.service.on("register", reply(401, {"code": -18010002, "data": {}}))
    said = rig.sync()
    assert "did not accept the key" in said and "Nothing was changed" in said
    assert rig.service.asked() == ["register"]  # never went on to read


def test_numbers_are_asked_about_in_batches_of_the_size_the_owner_chose(make_rig):
    rig = make_rig(settings={"batch_size": 2})
    stock(rig, 5)
    answer_all(rig)
    rig.sync()
    assert [len(b) for b in rig.service.sent("register")] == [2, 2, 1]
    assert [len(b) for b in rig.service.sent("gettrackinfo")] == [2, 2, 1]


def test_a_batch_is_never_bigger_than_the_service_takes_whatever_the_setting_says(make_rig):
    rig = make_rig(settings={"batch_size": 200})
    stock(rig, 45)
    answer_all(rig)
    rig.sync()
    assert [len(b) for b in rig.service.sent("register")] == [40, 5]
    assert [len(b) for b in rig.service.sent("gettrackinfo")] == [40, 5]


def test_the_check_pauses_between_requests_for_the_services_rate_limit(make_rig):
    rig = make_rig(settings={"batch_size": 2, "pause_ms": 400})
    stock(rig, 4)
    answer_all(rig)
    rig.sync()
    requests = len(rig.service.calls)
    assert requests >= 4 and rig.slept == [0.4] * (requests - 1)


def test_a_number_is_registered_once_and_the_quota_it_cost_is_written_down_once(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("getquota", quota())
    row = rig.add()
    charged = rig.package(row["id"])["charged_ms"]
    assert charged == START
    for _ in range(3):
        rig.clock.advance(DAY)
        rig.sync()
        rig.core.call("package.rename", {"id": row["id"], "label": "Garden hose"})
    rig.clock.advance(11 * MINUTE)
    rig.core.call("refresh")
    assert rig.service.asked().count("register") == 1
    assert rig.package(row["id"])["charged_ms"] == charged


def test_a_number_already_registered_there_is_taken_as_registered_and_not_charged_again(rig):
    rig.service.on("register", ok(rejected=[refused(
        HOSE, -18019901, "The tracking number has been registered.")]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    row = rig.add()
    kept = rig.package(row["id"])
    assert kept["registered_ms"] == START and kept["charged_ms"] is None
    assert row["status"] == "on_its_way"


def test_a_number_the_service_refused_is_not_offered_again_by_the_schedule(rig):
    rig.service.on("register", ok(rejected=[refused(
        HOSE, -18010012, "The format of 'ZX10' is invalid.")]))
    row = rig.add()
    assert "is invalid" in rig.package(row["id"])["refused"]
    for _ in range(3):
        rig.clock.advance(DAY)
        rig.sync()
    assert rig.service.asked() == ["register"]  # asked once, at the add


def test_refresh_now_offers_a_refused_number_again(rig):
    rig.service.on("register", ok(rejected=[refused(HOSE, -18010012, "Invalid format.")]),
                   ok([registered(HOSE)]))
    row = rig.add()
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("getquota", quota())
    rig.core.call("refresh")
    assert rig.package(row["id"])["registered_ms"] == START
    assert rig.package(row["id"])["refused"] == ""


def test_a_registration_stopped_by_an_empty_quota_is_tried_again_at_the_next_check(rig):
    rig.service.on("register", ok(rejected=[refused(HOSE, -18019908, "Quota exhausted.")]),
                   ok([registered(HOSE)]))
    row = rig.add()
    assert rig.package(row["id"])["registered_ms"] is None
    assert rig.package(row["id"])["refused"] == ""  # not a refusal of the number
    assert "quota is used up" in rig.package(row["id"])["note"]
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("getquota", quota())
    rig.clock.advance(4 * HOUR)
    rig.sync()
    assert rig.package(row["id"])["registered_ms"] == rig.clock.now


def test_the_quota_left_is_read_after_registering_and_shown(rig):
    stock(rig, 1)
    rig.service.on("register", ok([registered(NUMBERS[0])]))
    rig.service.on("gettrackinfo", ok([tracking(NUMBERS[0], "InTransit")]))
    rig.service.on("getquota", quota(200, 3))
    rig.sync()
    assert rig.core.call("overview")["quota"] == {"total": 200, "used": 3, "left": 197,
                                                  "at": START}


def test_a_check_with_nothing_new_to_register_does_not_ask_for_the_quota(rig):
    stock(rig, 1, registered_already=True)
    rig.service.on("gettrackinfo", ok([tracking(NUMBERS[0], "InTransit")]))
    rig.sync()
    assert rig.service.asked() == ["gettrackinfo"]


def test_refresh_now_checks_at_once_and_says_what_happened(rig):
    stock(rig, 2, registered_already=True)
    rig.service.on("gettrackinfo", ok([tracking(NUMBERS[0], "Delivered"),
                                       tracking(NUMBERS[1], "InTransit")]))
    rig.service.on("getquota", quota())
    done = rig.core.call("refresh")
    assert done == {"ok": True, "said": "Checked 2 packages; 2 changed.", "checked": 2,
                    "changed": 2}


def test_refresh_now_is_refused_inside_the_gap_and_says_how_long_to_wait(rig):
    stock(rig, 1, registered_already=True)
    rig.service.on("gettrackinfo", ok([tracking(NUMBERS[0], "InTransit")]))
    rig.service.on("getquota", quota())
    rig.core.call("refresh")
    asked = len(rig.service.calls)
    rig.clock.advance(3 * MINUTE)
    with pytest.raises(Exception, match="Try again in 7 minutes. Nothing was checked"):
        rig.core.call("refresh")
    assert len(rig.service.calls) == asked
    rig.clock.advance(7 * MINUTE)
    rig.core.call("refresh")
    assert len(rig.service.calls) > asked


def test_the_gap_counts_from_a_request_that_got_no_answer_too(rig):
    stock(rig, 1, registered_already=True)
    rig.service.on("gettrackinfo", no_answer())
    rig.core.call("refresh")
    rig.clock.advance(MINUTE)
    with pytest.raises(Exception, match="waits at least 10 minutes"):
        rig.core.call("refresh")


def test_a_gap_of_zero_lets_refresh_now_go_every_time(make_rig):
    rig = make_rig(settings={"refresh_gap_minutes": 0})
    stock(rig, 1, registered_already=True)
    rig.service.on("gettrackinfo", ok([tracking(NUMBERS[0], "InTransit")]))
    rig.service.on("getquota", quota())
    rig.core.call("refresh")
    rig.core.call("refresh")
    assert rig.service.asked().count("gettrackinfo") == 2


def test_a_check_that_sent_nothing_does_not_start_the_gap(make_rig):
    rig = make_rig(secrets={})
    stock(rig, 1)
    assert "no 17TRACK key" in rig.core.call("refresh")["said"]
    assert rig.store.get_meta("last_sent") is None


def test_a_check_already_running_makes_a_second_one_say_so_and_do_nothing(rig):
    stock(rig, 1, registered_already=True)
    assert rig.ctx.busy.acquire(blocking=False)
    try:
        done = rig.core.call("refresh")
    finally:
        rig.ctx.busy.release()
    assert done["ok"] is False and "already running" in done["said"]
    assert rig.service.calls == []


def test_with_no_packages_nothing_is_asked_of_the_service(rig):
    assert rig.sync().startswith("There is nothing to check")
    assert rig.service.calls == []


def test_a_delivered_package_is_checked_until_its_days_are_up_and_then_left_alone(make_rig):
    rig = make_rig(settings={"delivered_days": 14})
    [one, two] = stock(rig, 2, registered_already=True)
    rig.store.set(one, status="delivered", delivered_ms=START)
    rig.service.on("gettrackinfo", lambda a: ok([tracking(b["number"], "InTransit") if b[
        "number"] == NUMBERS[1] else tracking(b["number"], "Delivered") for b in a["json"]]))
    rig.service.on("getquota", quota())
    rig.clock.advance(13 * DAY)
    rig.sync()
    assert [b["number"] for b in rig.service.sent("gettrackinfo")[0]] == [NUMBERS[0], NUMBERS[1]]
    rig.service.clear()
    rig.clock.advance(1 * DAY)
    rig.sync()
    assert [b["number"] for b in rig.service.sent("gettrackinfo")[0]] == [NUMBERS[1]]
    assert rig.package(one)["archived_ms"] is not None
    assert rig.core.call("overview")["rows"][0]["tab"] == "delivered"
    assert two  # still on its way, still checked


def test_a_new_package_the_carrier_does_not_know_yet_reads_as_waiting_not_as_not_found(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "NotFound")]))
    row = rig.add()
    assert row["status"] == "waiting"
    rig.clock.advance(25 * HOUR)
    rig.sync()
    assert rig.package(row["id"])["status"] == "not_found"


def test_changing_trackers_registers_the_numbers_with_the_new_one(make_rig):
    rig = make_rig(tracker="practice")
    row = rig.add()
    assert rig.package(row["id"])["registered_with"] == ""
    rig.core.change_setting("tracker", "seventeentrack")
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("getquota", quota())
    rig.core.call("refresh")
    assert rig.package(row["id"])["registered_with"] == "seventeentrack"


def test_the_job_reads_every_package_the_screen_lists_as_still_being_checked(rig):
    stock(rig, 3, registered_already=True)
    rig.store.set(2, archived_ms=START)
    rig.service.on("gettrackinfo", lambda a: ok([tracking(b["number"], "InTransit")
                                                 for b in a["json"]]))
    rig.service.on("getquota", quota())
    rig.sync()
    assert [b["number"] for b in rig.service.sent("gettrackinfo")[0]] == [
        NUMBERS[0], NUMBERS[2]]


# --- reading without a pinned carrier, a quiet package, a number 17TRACK forgot -------------


def test_a_package_left_on_detect_it_is_read_without_the_carrier_17track_detected(rig):
    rig.service.on("register", ok([registered(HOSE, 3011)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit", carrier=3011)]))
    row = rig.add()
    assert rig.package(row["id"])["carrier_found"] == "3011"
    rig.service.script["gettrackinfo"] = [ok([tracking(HOSE, None, carrier=3011),
                                              tracking(HOSE, "OutForDelivery", carrier=3021)])]
    rig.ctx.store.set_meta(last_sent=None)
    rig.core.call("refresh")
    assert rig.service.sent("gettrackinfo")[-1] == [{"number": HOSE}]
    assert rig.package(row["id"])["status"] == "out_for_delivery"


def test_a_package_with_no_news_for_the_set_days_stops_being_checked_and_says_so(rig):
    answer_all(rig)
    row = rig.add()
    rig.store.set(row["id"], event_ms=START - 31 * DAY, registered_ms=START - 40 * DAY)
    rig.clock.advance(5 * HOUR)
    rig.sync()
    after = rig.package(row["id"])
    assert after["archived_ms"] is not None and "no news of it for 30 days" in after["note"]
    rig.service.clear()
    rig.clock.advance(5 * HOUR)
    rig.sync()
    assert rig.service.sent("gettrackinfo") == []  # no longer asked about


def test_with_quiet_days_at_zero_a_quiet_package_is_checked_as_before(make_rig):
    rig = make_rig(settings={"quiet_days": 0})
    answer_all(rig)
    row = rig.add()
    rig.store.set(row["id"], event_ms=START - 300 * DAY, registered_ms=START - 300 * DAY)
    rig.clock.advance(5 * HOUR)
    rig.sync()
    assert rig.package(row["id"])["archived_ms"] is None
    assert rig.package(row["id"])["checked_ms"] == rig.clock.now  # it was read


def test_a_number_17track_forgot_is_registered_again_only_by_its_own_button(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    row = rig.add()
    rig.service.script["gettrackinfo"] = [ok(rejected=[refused(HOSE, -18019902,
                                                               "Not registered.")])]
    rig.ctx.store.set_meta(last_sent=None)
    rig.core.call("refresh")
    after = rig.core.call("overview", {})["rows"][0]
    assert after["held"] is True and "uses another place in your quota" in after["note"]
    rig.service.clear()
    rig.ctx.store.set_meta(last_sent=None)
    rig.core.call("refresh")  # Refresh now does not register it again
    rig.clock.advance(5 * HOUR)
    rig.sync()
    assert "register" not in rig.service.asked()
    rig.service.script["gettrackinfo"] = [ok([tracking(HOSE, "InTransit")])]
    again = rig.core.call("package.register", {"id": row["id"]})
    assert rig.service.asked().count("register") == 1 and again["held"] is False
    assert rig.package(row["id"])["charged_ms"] is not None
