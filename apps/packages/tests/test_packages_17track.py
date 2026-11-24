"""17TRACK's tracker, against canned answers only: what is asked of exactly one host, how
each answer is sorted into yes, no or no clear answer, how its statuses become Packages'
own, and that the owner's key never shows up anywhere. Nothing is ever tried again by
itself."""

from __future__ import annotations

import json
import logging

import pytest
from jimmyhq_app import CallError
from packages.status import STATUSES
from packages.trackers import Item
from packages.trackers import seventeentrack as st
from packages.trackers.seventeentrack import classify
from packages_rig import HOSE, RAKE
from packages_service import (
    KEY,
    event,
    no_answer,
    ok,
    quota,
    refused,
    registered,
    reply,
    tracking,
)


def tracker(rig):
    return rig.ctx.trackers["seventeentrack"]


def refresh(rig):
    """Refresh now with the gap since the last request out of the way."""
    rig.ctx.store.set_meta(last_sent=None)
    return rig.core.call("refresh")


# --- sorting one answer ---------------------------------------------------------------------


def test_a_200_with_code_zero_is_a_yes():
    assert classify(ok(), None, KEY).kind == "yes"


def test_a_401_is_a_definite_no_that_says_what_may_be_wrong_with_the_key():
    answer = classify(reply(401, {"code": -18010002, "data": {"error": {
        "message": "The security key is invalid."}}}), None, KEY)
    assert answer.kind == "no"
    assert "did not accept the key" in answer.said
    assert "The security key is invalid." in answer.said


def test_a_200_whose_code_is_not_zero_is_a_no_in_the_services_own_words():
    answer = classify(reply(200, {"code": -18010011, "data": {"errors": [
        {"message": "Invalid data value."}]}}), None, KEY)
    assert (answer.kind, answer.said) == ("no", "Invalid data value.")


def test_a_failed_request_with_no_words_is_said_with_what_the_documented_code_means():
    answer = classify(reply(200, {"code": -18010004}), None, KEY)
    assert answer.kind == "no" and "account is disabled" in answer.said


def test_the_services_own_trouble_is_no_clear_answer_not_a_no():
    assert classify(reply(200, {"code": -18010003, "data": {}}), None, KEY).kind == "none"
    assert classify(reply(200, {"code": -18019817, "data": {}}), None, KEY).kind == "none"


def test_a_timeout_is_no_clear_answer():
    answer = classify(None, no_answer(), KEY)
    assert answer.kind == "none" and "took longer" in answer.said


def test_a_server_error_a_rate_limit_and_a_suspension_are_no_clear_answer():
    for status in (500, 502, 503, 429):
        assert classify(reply(status, text="<html>Try later</html>"), None, KEY).kind == "none"


def test_a_body_that_cannot_be_read_is_no_clear_answer_even_with_a_200():
    answer = classify(reply(200, text="<html>Welcome</html>"), None, KEY)
    assert answer.kind == "none" and "could not be read" in answer.said
    assert classify(reply(200, {"data": {}}), None, KEY).kind == "none"  # no code at all
    assert classify(None, None, KEY).kind == "none"


def test_a_refusal_by_jimmyhq_before_anything_was_sent_is_a_definite_no():
    error = CallError("not_allowed", "Your app has not been granted that host, so nothing "
                                      "was sent.")
    assert classify(None, error, KEY).kind == "no"


# --- the requests -----------------------------------------------------------------------------


def test_every_request_goes_to_the_one_host_with_the_key_in_its_header_only(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit", event=event())]))
    rig.service.on("getquota", quota())
    rig.add()
    refresh(rig)
    assert rig.service.urls
    assert all(u.startswith("https://api.17track.net/track/v2.4/") for u in rig.service.urls)
    assert {u.split("/")[2] for u in rig.service.urls} == {"api.17track.net"}
    for _, body, headers in rig.service.calls:
        assert headers["17token"] == KEY
        assert KEY not in json.dumps(body)


def test_the_documented_endpoints_are_the_ones_asked(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]))
    rig.service.on("changecarrier", ok([registered(HOSE, 3021)]))
    rig.service.on("deletetrack", ok([{"number": HOSE, "carrier": 3011}]))
    row = rig.add()
    rig.core.call("package.carrier", {"id": row["id"], "carrier": "3021"})
    rig.core.call("package.remove", {"id": row["id"]})
    assert rig.service.asked() == ["register", "gettrackinfo", "changecarrier", "deletetrack"]
    assert rig.service.sent("register") == [[{"number": HOSE}]]
    assert rig.service.sent("changecarrier") == [[{"number": HOSE, "carrier_old": 3011,
                                                   "carrier_new": 3021}]]
    assert rig.service.sent("deletetrack") == [[{"number": HOSE}]]


def test_a_carrier_the_owner_chose_is_sent_as_the_whole_number_the_service_wants(rig):
    rig.service.on("register", ok([registered(HOSE, 100003)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InfoReceived", carrier=100003)]))
    rig.add(carrier="100003")
    assert rig.service.sent("register") == [[{"number": HOSE, "carrier": 100003}]]
    assert rig.service.sent("gettrackinfo") == [[{"number": HOSE, "carrier": 100003}]]


def test_a_carrier_that_is_not_a_number_is_refused_before_anything_is_sent(rig):
    with pytest.raises(CallError, match="whole number"):
        rig.add(carrier="Royal Post")
    assert rig.service.calls == [] and rig.store.packages() == []


def test_no_request_is_made_without_the_permission_for_that_host(make_rig):
    rig = make_rig(permissions=["jobs", "bench.file"])
    row = rig.add()
    assert rig.core.fetched == []
    assert row["registered"] is False
    assert "could not be looked up just now" in row["said"]


def test_without_a_key_nothing_is_sent_and_the_screen_says_where_to_type_it(make_rig):
    rig = make_rig(secrets={})
    row = rig.add()
    assert rig.service.calls == [] and rig.core.fetched == []
    assert "has no 17TRACK key" in row["said"] and "Settings" in row["said"]
    assert rig.core.call("overview")["tracker"]["key_problem"]


# --- what comes back, in Packages' own words -------------------------------------------------


def test_every_documented_main_status_becomes_one_of_packages_own():
    seen = {st.status_of(name) for name in (
        "NotFound", "InfoReceived", "InTransit", "Expired", "AvailableForPickup",
        "OutForDelivery", "DeliveryFailure", "Delivered", "Exception")}
    assert seen <= set(STATUSES) and None not in seen
    assert st.status_of("Delivered") == "delivered"
    assert st.status_of("DeliveryFailure") == st.status_of("Exception") == "problem"
    assert st.status_of("InfoReceived") == "waiting"
    assert st.status_of("Teleported") is None


def test_a_status_the_documentation_did_not_list_changes_nothing_and_says_so(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit")]),
                   ok([tracking(HOSE, "Teleported")]))
    row = rig.add()
    refresh(rig)
    after = rig.package(row["id"])
    assert after["status"] == "on_its_way"
    assert "does not know" in after["note"] and "Teleported" in after["note"]


def test_the_latest_event_is_kept_with_its_place_and_its_time(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "InTransit", event=event(
        "Arrived at the depot", "Springfield", "2027-01-15T06:30:00Z"))]))
    row = rig.add()
    kept = rig.package(row["id"])
    assert kept["event"] == "Arrived at the depot (Springfield)"
    assert kept["event_ms"] == 1_799_994_600_000  # 2027-01-15 06:30 UTC


def test_a_number_with_no_tracking_information_yet_is_no_news_not_a_problem(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, None)]))
    row = rig.add()
    assert row["status"] == "waiting" and rig.package(row["id"])["note"] == ""


def test_a_number_the_service_says_it_never_registered_is_held_until_the_owner_asks_again(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    row = rig.add()
    rig.service.on("gettrackinfo", ok(rejected=[refused(
        HOSE, -18019902, "The tracking number has not been registered.")]))
    refresh(rig)
    after = rig.package(row["id"])
    assert after["registered_ms"] is None
    assert "does not know this number" in after["refused"]
    assert "Refresh now" in after["note"]


def test_a_number_that_comes_back_for_two_carriers_is_read_from_the_one_with_information(rig):
    rig.service.on("register", ok([registered(HOSE)]))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, None, carrier=3011),
                                       tracking(HOSE, "OutForDelivery", carrier=3021)]))
    assert rig.add()["status"] == "out_for_delivery"


def test_the_quota_is_read_as_total_used_and_left(rig):
    rig.service.on("getquota", quota(200, 3))
    found = tracker(rig).quota()
    assert (found.quota.total, found.quota.used, found.quota.left) == (200, 3, 197)
    rig.service.script.clear()
    rig.service.on("getquota", reply(200, {"code": 0, "data": {"quota_total": "many"}}))
    assert tracker(rig).quota().kind == "none"


def test_changing_a_registered_number_back_to_detecting_is_refused_with_what_to_do_instead(rig):
    found = tracker(rig).change_carrier(Item(1, HOSE, "3011"), "")
    assert found.items[0].kind == "no"
    assert "remove the package and add it again" in found.items[0].said
    assert "uses another place in your quota" in found.items[0].said


def test_a_carrier_change_without_a_known_carrier_sends_none_as_the_old_one(rig):
    rig.service.on("changecarrier", ok([registered(HOSE, 3021)]))
    tracker(rig).change_carrier(Item(1, HOSE, ""), "3021")
    assert rig.service.sent("changecarrier") == [[{"number": HOSE, "carrier_new": 3021}]]


@pytest.mark.parametrize("code, words", [
    (-18019807, "changed 5 times at most"), (-18019801, "more than one carrier"),
    (-18019806, "stopped tracking this number"), (-18019808, "not finished with")])
def test_each_documented_refusal_of_a_carrier_change_is_said_in_words(rig, code, words):
    rig.service.on("changecarrier", ok(rejected=[refused(HOSE, code, "Refused.")]))
    [one] = tracker(rig).change_carrier(Item(1, HOSE, "3011"), "3021").items
    assert one.kind == "no" and words in one.said and "Nothing was changed." in one.said


def test_the_limit_of_five_changes_says_what_another_carrier_would_cost(rig):
    rig.service.on("changecarrier", ok(rejected=[refused(HOSE, -18019807, "Limit.")]))
    [one] = tracker(rig).change_carrier(Item(1, HOSE, "3011"), "3021").items
    assert "registers the number again and uses another place in your quota" in one.said


# --- the key stays secret ---------------------------------------------------------------------


def test_the_key_is_taken_out_of_whatever_the_service_or_the_network_says(rig):
    rig.service.on("register", reply(401, {"code": -18010002, "data": {"error": {
        "message": f"Key {KEY} is not valid."}}}))
    row = rig.add()
    assert KEY not in row["said"] and "[your key]" in row["said"]
    rig.service.script.clear()
    rig.service.on("register", CallError("unavailable", f"connection for {KEY} was reset"))
    said = refresh(rig)["said"]
    assert "was reset" in said and KEY not in said and "[your key]" in said
    assert KEY not in json.dumps(rig.core.call("overview"))


def test_the_key_never_appears_in_the_log_the_store_the_cards_or_anything_sent_to_the_page(
        rig, caplog):
    caplog.set_level(logging.DEBUG)
    rig.service.on("register", ok([registered(HOSE)]), reply(401, text=f"bad {KEY}"))
    rig.service.on("gettrackinfo", ok([tracking(HOSE, "Delivered")]),
                   reply(500, text=f"oops {KEY}"))
    rig.service.on("getquota", quota())
    rig.add()
    refresh(rig)
    rig.add("Rake", RAKE)
    assert KEY not in caplog.text
    assert KEY not in rig.wherever_it_could_be_written()
    assert KEY not in "\n".join(rig.ctx.store.conn.iterdump())
    assert KEY not in json.dumps(rig.core.call("overview"))
