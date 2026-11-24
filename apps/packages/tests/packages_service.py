"""A stand-in for 17TRACK inside the fake core: canned answers by endpoint, and a record of
every request. Nothing reaches the network.

Every answer is set by the test with ``service.on(path, *answers)``; the last answer for an
endpoint repeats. An endpoint with no answer gets no answer (as a timeout) and is listed in
``service.unscripted``. Numbers and names are invented; the key is the made-up one every
test uses.
"""

from __future__ import annotations

import json
from typing import Any

from jimmyhq_app import CallError
from packages.trackers.seventeentrack import BASE

KEY = "sk-test-SECRET-0123456789"


def reply(status: int = 200, data: Any = None, text: str | None = None) -> dict[str, Any]:
    body = text if text is not None else ("" if data is None else json.dumps(data))
    return {"status": status, "headers": {"content-type": "application/json"}, "text": body}


def no_answer() -> CallError:
    return CallError("unavailable", "api.17track.net took longer than 30 seconds to answer. "
                                    "Try again later.")


def ok(accepted: list | tuple = (), rejected: list | tuple = ()) -> dict[str, Any]:
    return reply(200, {"code": 0, "data": {"accepted": list(accepted),
                                           "rejected": list(rejected)}})


def registered(number: str, carrier: int = 3011, origin: int = 1) -> dict[str, Any]:
    return {"origin": origin, "number": number, "carrier": carrier, "email": None,
            "lang": None}


def refused(number: str, code: int, message: str, carrier: int = 0) -> dict[str, Any]:
    return {"number": number, "carrier": carrier, "error": {"code": code, "message": message}}


def tracking(number: str, status: str | None, *, carrier: int = 3011,
             event: dict | None = None, sub: str = "") -> dict[str, Any]:
    """One accepted ``gettrackinfo`` entry. ``status`` None is "no information yet"."""
    latest = {"status": status, "sub_status": sub or status, "sub_status_descr": None}
    info = None if status is None else {
        "latest_status": latest, "latest_event": event,
        "tracking": {"providers": []}}
    return {"number": number, "carrier": carrier, "param": None, "tag": "", "track_info": info}


def event(text: str = "Departed the sorting centre", where: str = "Springfield",
          when: str = "2027-01-15T06:30:00Z") -> dict[str, Any]:
    return {"time_iso": when, "time_utc": when, "description": text, "location": where,
            "stage": "Departure"}


def quota(total: int = 200, used: int = 3) -> dict[str, Any]:
    return reply(200, {"code": 0, "data": {"quota_total": total, "quota_used": used,
                                           "quota_remain": total - used, "today_used": 0,
                                           "max_track_daily": 10000000}})


class Service:
    def __init__(self, core: Any):
        self.core = core
        self.script: dict[str, list[Any]] = {}
        self.calls: list[tuple[str, Any, dict[str, str]]] = []
        self.urls: list[str] = []
        self.unscripted: list[str] = []
        self._fetch_as_core = core._net_fetch  # noqa: SLF001 - keeps the host rules
        core._net_fetch = self._fetch  # noqa: SLF001

    def on(self, path: str, *answers: Any) -> None:
        self.script.setdefault(path, []).extend(answers)

    def _fetch(self, args: dict[str, Any]) -> dict[str, Any]:
        url = str(args["url"])
        self.urls.append(url)
        path = url.removeprefix(BASE + "/")
        self.calls.append((path, args.get("json"), dict(args.get("headers") or {})))
        queue = self.script.get(path)
        if not queue:
            self.unscripted.append(path)
            raise no_answer()
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        self.core.responses[url] = answer
        return self._fetch_as_core(args)

    def sent(self, path: str) -> list[Any]:
        """The JSON bodies sent to one endpoint, in order."""
        return [body for p, body, _ in self.calls if p == path]

    def asked(self) -> list[str]:
        return [p for p, _, _ in self.calls]

    def clear(self) -> None:
        self.calls.clear()
        self.urls.clear()
