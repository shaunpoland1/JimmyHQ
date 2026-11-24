"""17TRACK: looks parcels up through its Tracking API, version 2.4.

Every request goes through JimmyHQ's ``net.fetch`` to exactly ``api.17track.net``, which
the manifest asks for, with the owner's key from the app's Settings page. With no key the
tracker says so and nothing is sent. This file is the only one that knows 17TRACK's
addresses, fields and statuses; it hands the rest of Packages plain items, readings and
sentences. Each answer is classified once, by ``classify``: a definite yes, a definite no
(with the service's own words) or no clear answer. Nothing here ever tries again.

What the service does that shapes this file. Everything below was read in 17TRACK's own
documentation for this version; nothing here is from memory:

- https://api.17track.net/en/doc?version=v2.4 (the Tracking API v2.4 reference): base
  address ``https://api.17track.net/track/v2.4``; every endpoint is ``POST`` with a UTF-8
  JSON body; the key goes in the ``17token`` header; at most 3 requests a second (HTTP 429
  past that); HTTP 200 is "processed", 401 a bad key, an address not allowed or a disabled
  account, 404 a wrong address, 429 too fast, 500 a server error, 503 suspended.
- ``/register``: up to 40 numbers, each ``{number, carrier?}`` (``carrier`` is a whole number,
  the carrier code; ``auto_detection`` is on by default and ignored when a carrier is
  given). The answer is ``{code, data: {accepted: [{origin, number, carrier, ...}],
  rejected: [{number, carrier, error: {code, message}}]}}``; ``origin`` 3 means the service
  guessed the carrier. Quota: "each tracking number counts as one shipment", no duplicate
  charge while it is being tracked, and a number deleted and registered again is charged
  again. Whether a rejected number costs quota is not said.
- ``/gettrackinfo``: up to 40 numbers, each ``{number, carrier?}``; the answer is the same
  wrapper; an accepted item holds ``track_info`` with ``latest_status`` (``status``,
  ``sub_status``) and ``latest_event`` (may be null: ``time_iso``/``time_utc``,
  ``description``, ``location``). The main statuses are NotFound, InfoReceived, InTransit,
  Expired, AvailableForPickup, OutForDelivery, DeliveryFailure, Delivered and Exception.
  A number given without a carrier that matches several carriers is answered once for
  each. Results come "within seconds" of registering, sometimes after more than 5 minutes.
- ``/changecarrier``: up to 40 of ``{number, carrier_old?, carrier_new}``; ``carrier_old``
  says which carrier to change when a number has several (-18019801 asks for it); a
  number's carrier may be changed 5 times at most (-18019807); -18019805 (not under that
  carrier), -18019806 (a stopped number) and -18019808 (the last change has no result
  yet) are refusals too. ``/deletetrack``: up to 40 of ``{number}``; ``/getquota``: no
  parameters, answers ``quota_total``, ``quota_used``, ``quota_remain``.
- Tracking stops by itself after 30 days with no new event from the carrier (and 15 days
  after delivery); a stopped number stays in the account for 90 days and is then deleted.
  A stopped number can be started again once (``/retrack``), which Packages does not do.
- Error codes (``error.code``): -18019901 already registered, -18019902 not registered,
  -18019903 carrier cannot be detected, -18019909 no tracking information yet,
  -18019908 quota exhausted, -18019907 daily tracking limit exceeded, -18019803 new
  carrier is the same, -18010002 invalid key, -18010003 internal service error.
- The carrier list is a static file, https://res.17track.net/asset/carrier/info/apicarrier.all.json
  (and ``.csv``), on another host (about 700 KB, hundreds of entries: ``key``, ``_name``,
  ``_country_iso``). That host is not this app's, so Packages asks for no permission to
  it and bundles none of it: the owner picks "detect automatically", or types a carrier
  code and may save it under a name of his own.

What the documentation leaves unclear, and how this file reads it (see the tests): the
shape of an answer to a whole request that failed (a non-zero ``code`` with no
``accepted``/``rejected``), so the words are looked for under ``data.error``,
``data.errors``, ``error`` and ``message``, and the documented code's own meaning is used
when none is found; whether ``/gettrackinfo`` and ``/getquota`` use quota (only
registration is documented to), so Packages asks them freely but never more often than the
owner's schedule; whether a carrier change is charged; the full "Package status" table;
whether ``/gettrackinfo`` without a carrier answers once for each carrier a number is
registered under (the documentation says so of ``/push``, ``/stoptrack`` and
``/deletetrack``; ``pick`` reads several entries for one number either way); and what a
timed-out ``/changecarrier`` did, so Packages then reads the number without a carrier
until a read tells it which carrier 17TRACK has. A status Packages does not know is left
unchanged, with a sentence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jimmyhq_app import CallError

from packages.config import SECRET
from packages.trackers import NO, NONE, YES, Item, Quota, Reading, Reply, Result

log = logging.getLogger("packages.17track")

HOST = "api.17track.net"
BASE = f"https://{HOST}/track/v2.4"
KEY_HEADER = "17token"
LABEL = "17TRACK"
BATCH_MOST = 40  # the documented most numbers in one request
EVENT_CHARS = 300

NO_KEY = ("Packages has no 17TRACK key, so nothing was sent. Type it in on Packages' "
          "Settings page under Apps, then press Refresh now.")

# 17TRACK's main statuses, in Packages' words. An unknown one changes nothing.
MAIN = {
    "NotFound": "not_found", "InfoReceived": "waiting", "InTransit": "on_its_way",
    "Expired": "expired", "AvailableForPickup": "pickup", "OutForDelivery": "out_for_delivery",
    "DeliveryFailure": "problem", "Delivered": "delivered", "Exception": "problem",
}

ALREADY, NOT_REGISTERED, NO_CARRIER, NO_INFO_YET = -18019901, -18019902, -18019903, -18019909
SAME_CARRIER = -18019803
# /changecarrier's own refusals, in the owner's words (the error code table).
CHANGE_WORDS = {
    -18019801: ("17TRACK holds this number under more than one carrier and needs to be told "
                "which one to change, and Packages does not know it. Nothing was changed."),
    -18019805: ("17TRACK does not hold this number under the carrier Packages has for it. "
                "Nothing was changed. Press Refresh now, then try again."),
    -18019806: ("17TRACK has stopped tracking this number, and a stopped number's carrier "
                "cannot be changed. Nothing was changed."),
    -18019807: ("17TRACK lets a number's carrier be changed 5 times at most, and this one has "
                "used them all. Nothing was changed. To track it with another carrier, remove "
                "the package and add it again with that carrier: that registers the number "
                "again and uses another place in your quota."),
    -18019808: ("17TRACK has not finished with this number's last registration or change "
                "yet. Nothing was changed. Try again in a few minutes."),
}
QUOTA_USED_UP, DAILY_LIMIT = -18019908, -18019907
# The service's own trouble, not the owner's: no clear answer, so try again later.
TROUBLE = frozenset({-18010003, -18019815, -18019816, -18019817})
# What the documentation says each general code means, for when the answer gives no words.
MEANT = {
    -18010001: "this computer's address is not on the account's allowed list",
    -18010002: "the key is not valid",
    -18010004: "the account is disabled",
    -18010005: "the request was not authorised",
    -18010014: "too many numbers were sent in one request",
}
NOT_SENT = frozenset({"not_allowed", "invalid", "not_found", "quota"})  # nothing left here


# --- the rules, one function each --------------------------------------------------------


def scrub(text: Any, key: str | None) -> str:
    """Text that may be shown or kept, with the key taken out wherever it appears."""
    text = str(text or "")
    if key:
        text = text.replace(key, "[your key]")
    return text


def service_words(data: Any) -> str:
    """What the service said, in its own words, wherever it put them; "" when it gave none."""
    if not isinstance(data, dict):
        return ""
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    for place in (inner.get("error"), inner, data.get("error"), data):
        if isinstance(place, dict):
            if isinstance(place.get("message"), str) and place["message"].strip():
                return place["message"].strip()[:300]
    errors = inner.get("errors") if isinstance(inner.get("errors"), list) else []
    for one in errors:
        if isinstance(one, dict) and isinstance(one.get("message"), str) and one["message"]:
            return one["message"].strip()[:300]
    return ""


@dataclass(frozen=True)
class Answer:
    """One request's result. ``kind`` is ``yes``, ``no`` (a definite refusal; ``said`` has
    the words) or ``none`` (no answer that can be trusted either way)."""

    kind: str
    status: int | None = None
    data: Any = None
    said: str = ""


def classify(reply: dict[str, Any] | None, error: Exception | None, key: str | None) -> Answer:
    """One answer, sorted. A refusal by JimmyHQ before sending is a no; a timeout, a broken
    connection, a server error, a rate limit or a body that cannot be read is no answer; a
    200 whose ``code`` is not 0 is a no (unless it names the service's own trouble); a 401
    or any other 4xx is a no."""
    if error is not None:
        message = scrub(getattr(error, "message", "") or str(error), key)
        if isinstance(error, CallError) and error.code in NOT_SENT:
            return Answer(NO, said=message)
        return Answer(NONE, said=message or "No answer came back.")
    if not isinstance(reply, dict) or not isinstance(reply.get("status"), int):
        return Answer(NONE, said="The answer could not be read.")
    status, text = reply["status"], str(reply.get("text") or "")
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        data = None
    words = scrub(service_words(data), key)
    if status == 200:
        if not isinstance(data, dict) or not isinstance(data.get("code"), int):
            return Answer(NONE, status, None, f"{LABEL} said yes but its answer could not "
                                              "be read.")
        code = data["code"]
        if code == 0:
            return Answer(YES, status, data)
        if code in TROUBLE:
            return Answer(NONE, status, data, words or f"{LABEL} had trouble on its side.")
        return Answer(NO, status, data, words or meant_sentence(code))
    if status == 401:
        return Answer(NO, status, data, (f"{LABEL} did not accept the key: it may be wrong, "
                                         "this computer's address may not be allowed on the "
                                         "account, or the account may be disabled."
                                         + (f" It said: {words}" if words else "")))
    if status == 429:
        return Answer(NONE, status, data, f"{LABEL} said too many requests came at once.")
    if status == 503:
        return Answer(NONE, status, data, f"{LABEL} says it is suspended for a while.")
    if 400 <= status < 500:
        return Answer(NO, status, data, words or f"{LABEL} refused it (HTTP {status}).")
    return Answer(NONE, status, data, words or f"{LABEL} gave no clear answer (HTTP "
                                               f"{status}).")


def meant_sentence(code: int) -> str:
    return f"{LABEL} refused it: {MEANT.get(code, f'its code was {code}')}."


def status_of(main: Any) -> str | None:
    """Packages' status for 17TRACK's main status, or None when it is not one it knows."""
    return MAIN.get(main) if isinstance(main, str) else None


def event_time(event: dict[str, Any]) -> int | None:
    """An event's time as unix milliseconds, from ``time_utc`` or else ``time_iso``."""
    for name in ("time_utc", "time_iso"):
        raw = event.get(name)
        if isinstance(raw, str) and raw.strip():
            try:
                when = datetime.fromisoformat(raw.strip())
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            return int(when.timestamp() * 1000)
    return None


def event_text(event: Any) -> str:
    """The latest event as one line: what happened, and where when the service says."""
    if not isinstance(event, dict):
        return ""
    said = str(event.get("description") or "").strip()
    place = str(event.get("location") or "").strip()
    if said and place and place not in said:
        said = f"{said} ({place})"
    return " ".join((said or place).split())[:EVENT_CHARS]


def reading_of(entry: dict[str, Any]) -> tuple[Reading | None, str]:
    """``(reading, problem)`` from one accepted ``/gettrackinfo`` entry: a reading, None
    for "no news yet", and a sentence when the status is not one Packages knows."""
    info = entry.get("track_info")
    latest = info.get("latest_status") if isinstance(info, dict) else None
    main = latest.get("status") if isinstance(latest, dict) else None
    if main is None:
        return None, ""
    found = status_of(main)
    if found is None:
        return None, (f"{LABEL} gave a status Packages does not know (\"{str(main)[:40]}\"), "
                      "so nothing was changed for this package.")
    event = info.get("latest_event") if isinstance(info, dict) else None
    return Reading(found, event_text(event),
                   event_time(event) if isinstance(event, dict) else None), ""


def entries(data: Any) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]] | None:
    """``(accepted, rejected)`` by upper-cased number, or None when the answer holds neither
    list (so it cannot be trusted)."""
    body = data.get("data") if isinstance(data, dict) else None
    if not isinstance(body, dict) or not (
            isinstance(body.get("accepted"), list) or isinstance(body.get("rejected"), list)):
        return None
    accepted: dict[str, list[dict[str, Any]]] = {}
    for one in body.get("accepted") or []:
        if isinstance(one, dict) and one.get("number"):
            accepted.setdefault(str(one["number"]).upper(), []).append(one)
    rejected = {str(one["number"]).upper(): one for one in body.get("rejected") or []
                if isinstance(one, dict) and one.get("number")}
    return accepted, rejected


def refusal(rejected: dict[str, Any], key: str | None) -> tuple[int | None, str]:
    error = rejected.get("error") if isinstance(rejected.get("error"), dict) else {}
    code = error.get("code") if isinstance(error.get("code"), int) else None
    return code, scrub(str(error.get("message") or "").strip()[:300], key)


def pick(options: list[dict[str, Any]], carrier: str) -> dict[str, Any]:
    """Of the entries one number came back with, the one that holds tracking information
    (for the owner's carrier when several do), else the one for the carrier, else the
    first."""
    holding = [o for o in options if isinstance(o.get("track_info"), dict)]
    for one in holding:
        if carrier and str(one.get("carrier")) == carrier:
            return one
    if holding:
        return holding[0]
    for one in options:
        if carrier and str(one.get("carrier")) == carrier:
            return one
    return options[0]


# --- the tracker --------------------------------------------------------------------------


class SeventeenTrack:
    id = "seventeentrack"
    label = LABEL
    outside = True
    registers = True
    batch_most = BATCH_MOST
    hand_statuses = ("delivered",)
    carrier_help = ("Leave it on \"Detect it\" and 17TRACK guesses, or type its carrier code, a "
                    "whole number from 17TRACK's carrier list (res.17track.net/asset/carrier/"
                    "info/apicarrier.all.json). A code you type can be saved under a name.")

    def __init__(self, app: Any, cfg: Any = None):
        self.app = app
        self._cfg = cfg

    # -- the owner's key and the one host ----------------------------------------------

    def _key(self) -> str | None:
        try:
            return (self.app.secrets.get(SECRET) or "").strip() or None
        except CallError:
            return None

    def key_problem(self) -> str | None:
        return None if self._key() else NO_KEY

    def _timeout(self) -> int:
        try:
            return int(self._cfg().timeout_s) if self._cfg else 30
        except Exception:  # noqa: BLE001 - a settings failure never stops a request
            return 30

    def _ask(self, path: str, payload: Any = None) -> Answer:
        key = self._key()
        if not key:
            return Answer(NO, said=NO_KEY)
        headers = {KEY_HEADER: key, "Content-Type": "application/json"}
        try:
            reply = self.app.net.fetch(f"{BASE}/{path}", method="POST", headers=headers,
                                       json=payload, timeout_s=self._timeout())
        except Exception as error:  # noqa: BLE001 - every failure is sorted by classify
            answer = classify(None, error, key)
        else:
            answer = classify(reply, None, key)
        log.info("17TRACK %s: %s", path, answer.kind)  # never the numbers, never the key
        return answer

    # -- carriers -----------------------------------------------------------------------

    def check_carrier(self, code: str) -> str | None:
        if code and not (code.isascii() and code.isdigit() and 0 < int(code) < 10**9):
            return ("A 17TRACK carrier code is a whole number, such as 3011. Nothing was "
                    "changed. Leave it empty to let 17TRACK detect the carrier.")
        return None

    # -- the calls ----------------------------------------------------------------------

    def _whole(self, answer: Answer) -> Reply | None:
        """The reply for a request that was not a clear yes, else None."""
        if answer.kind != YES:
            return Reply(answer.kind, answer.said)
        return None

    def register(self, items: list[Item]) -> Reply:
        body = [{"number": i.number, **({"carrier": int(i.carrier)} if i.carrier else {})}
                for i in items]
        answer = self._ask("register", body)
        failed = self._whole(answer)
        if failed:
            return failed
        found = entries(answer.data)
        if found is None:
            return Reply(NONE, f"{LABEL} said yes but its answer could not be read.")
        accepted, rejected = found
        key, out = self._key(), []
        for item in items:
            number = item.number.upper()
            if number in accepted:
                one = accepted[number][0]
                out.append(Result(item.id, YES, charged=True, carrier=str(one.get("carrier") or ""),
                                  guessed=one.get("origin") == 3))
            elif number in rejected:
                code, said = refusal(rejected[number], key)
                if code == ALREADY:
                    out.append(Result(item.id, YES))
                elif code in (QUOTA_USED_UP, DAILY_LIMIT):
                    why = ("your quota is used up" if code == QUOTA_USED_UP
                           else "its daily limit is reached")
                    out.append(Result(item.id, NONE, f"{LABEL} says {why}, so this package was "
                                                     "not registered. It is tried again at the "
                                                     "next check."))
                else:
                    out.append(Result(item.id, NO, said or f"{LABEL} did not take this number."))
            else:
                out.append(Result(item.id, NONE, f"{LABEL} did not mention this number in its "
                                                 "answer."))
        return Reply(YES, items=tuple(out))

    def read(self, items: list[Item]) -> Reply:
        body = [{"number": i.number, **({"carrier": int(i.carrier)} if i.carrier else {})}
                for i in items]
        answer = self._ask("gettrackinfo", body)
        failed = self._whole(answer)
        if failed:
            return failed
        found = entries(answer.data)
        if found is None:
            return Reply(NONE, f"{LABEL} said yes but its answer could not be read.")
        accepted, rejected = found
        key, out = self._key(), []
        for item in items:
            number = item.number.upper()
            if number in accepted:
                one = pick(accepted[number], item.carrier or item.prefer)
                reading, problem = reading_of(one)
                if problem:
                    out.append(Result(item.id, NONE, problem))
                else:
                    out.append(Result(item.id, YES, reading=reading,
                                      carrier=str(one.get("carrier") or "")))
            elif number in rejected:
                code, said = refusal(rejected[number], key)
                if code == NO_INFO_YET:
                    out.append(Result(item.id, YES))
                elif code == NOT_REGISTERED:
                    out.append(Result(item.id, NO, f"{LABEL} says it does not know this "
                                                   "number (it may have been deleted there).",
                                      gone=True))
                else:
                    out.append(Result(item.id, NO, said or f"{LABEL} gave no information."))
            else:
                out.append(Result(item.id, NONE, f"{LABEL} did not mention this number in its "
                                                 "answer."))
        return Reply(YES, items=tuple(out))

    def _one(self, path: str, item: Item, extra: dict[str, Any], *,
             fine: tuple[int, ...], codes: dict[int, str] | None = None) -> Reply:
        answer = self._ask(path, [{"number": item.number, **extra}])
        failed = self._whole(answer)
        if failed:
            return failed
        found = entries(answer.data)
        if found is None:
            return Reply(NONE, f"{LABEL} said yes but its answer could not be read.")
        accepted, rejected = found
        number = item.number.upper()
        if number in accepted:
            return Reply(YES, items=(Result(item.id, YES, carrier=str(
                accepted[number][0].get("carrier") or "")),))
        if number in rejected:
            code, said = refusal(rejected[number], self._key())
            if code in fine:
                return Reply(YES, items=(Result(item.id, YES),))
            if codes and code in codes:
                said = codes[code] + (f" 17TRACK said: {said}" if said else "")
            return Reply(YES, items=(Result(item.id, NO, said or f"{LABEL} refused it."),))
        return Reply(NONE, f"{LABEL} did not mention this number in its answer.")

    def change_carrier(self, item: Item, code: str) -> Reply:
        """Change the carrier of a number 17TRACK tracks: ``carrier_old`` is sent when
        Packages knows the carrier it has now (the documentation asks for it when a number
        has several, error -18019801). A refusal the documentation names is said in the
        owner's words, the 5-change limit among them."""
        if not code:
            return Reply(YES, items=(Result(item.id, NO, (
                f"{LABEL} needs a carrier code to change a number it already tracks, and "
                "cannot go back to detecting it. Type the carrier's code, or remove the "
                "package and add it again to let 17TRACK guess: that registers the number "
                "again and uses another place in your quota.")),))
        old = {"carrier_old": int(item.carrier)} if item.carrier.isdigit() else {}
        return self._one("changecarrier", item, {**old, "carrier_new": int(code)},
                         fine=(SAME_CARRIER,), codes=CHANGE_WORDS)

    def forget(self, item: Item) -> Reply:
        return self._one("deletetrack", item, {}, fine=(NOT_REGISTERED,))

    def quota(self) -> Reply:
        answer = self._ask("getquota")
        failed = self._whole(answer)
        if failed:
            return failed
        body = answer.data.get("data") if isinstance(answer.data, dict) else None
        try:
            return Reply(YES, quota=Quota(int(body["quota_total"]), int(body["quota_used"]),
                                          int(body["quota_remain"])))
        except (KeyError, TypeError, ValueError):
            return Reply(NONE, f"{LABEL} said yes but its quota could not be read.")
