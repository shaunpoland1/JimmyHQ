"""A new browser target starts paused, and is let go only once its requests are watched.
A fake of the browser's wire stands in for the browser: no browser is needed here."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jimmyhq.browser import cdp
from jimmyhq.browser.page import UNSAFE_TAB_NOTE, ChromeDriver


class FakeWire:
    """What ``ChromeDriver`` uses of ``cdp.CDP``. ``fail`` names a command that raises."""

    def __init__(self, fail: str | None = None, hold: str | None = None):
        self.fail, self.hold = fail, hold
        self.release = asyncio.Event() if hold else None
        self.timeline: list[str] = []  # every command, sent or fired, in order
        self.sent: list[tuple[str, str | None]] = []
        self.fired: list[tuple[str, dict | None, str | None]] = []
        self.closed = False

    async def send(self, method: str, params: dict | None = None, session: str | None = None,
                   timeout: float = 30.0) -> dict[str, Any]:
        self.sent.append((method, session))
        self.timeline.append(f"{method}@{session}")
        if method == self.hold:
            await self.release.wait()
        if method == self.fail:
            raise cdp.CDPError(f"the browser did not answer {method} in time")
        return {}

    def fire(self, method: str, params: dict | None = None, session: str | None = None) -> None:
        self.fired.append((method, params, session))
        self.timeline.append(f"{method}@{session}")

    def resumed(self, session: str) -> bool:
        return any(m == "Runtime.runIfWaitingForDebugger" and s == session
                   for m, _, s in self.fired)

    def closed_targets(self) -> list[str]:
        return [p["targetId"] for m, p, _ in self.fired if m == "Target.closeTarget"]


def driver(wire: FakeWire, max_tabs: int = 4) -> ChromeDriver:
    d = ChromeDriver(Path("browser"), Path("profile"), 1, width=800, height=600,
                     max_tabs=max_tabs, page_timeout_s=1, settle_ms=0, start_timeout_s=1)
    d.c = wire  # type: ignore[assignment]
    d.level = "read"
    return d


def target(kind: str, session: str, name: str = "t") -> dict:
    return {"sessionId": session, "targetInfo": {"type": kind, "targetId": name + session}}


def run(d: ChromeDriver, *events: tuple[dict, str | None]) -> None:
    async def go():
        for p, parent in events:
            await d._attached(p, parent)
    asyncio.run(go())


def test_a_tab_whose_request_check_cannot_be_turned_on_is_closed_and_never_resumed():
    wire = FakeWire(fail="Fetch.enable")
    d = driver(wire)
    run(d, (target("page", "s1"), None))
    assert not wire.resumed("s1")
    assert wire.closed_targets() == ["ts1"]
    assert d.pages == {} and d.order == [] and d.active is None
    assert UNSAFE_TAB_NOTE in d.notes() and "closed" in UNSAFE_TAB_NOTE


def test_a_tab_whose_children_cannot_be_watched_is_closed_and_never_resumed():
    wire = FakeWire(fail="Target.setAutoAttach")
    d = driver(wire)
    run(d, (target("page", "s1"), None))
    assert not wire.resumed("s1") and wire.closed_targets() == ["ts1"] and d.pages == {}


@pytest.mark.parametrize("step", ["Page.enable", "Page.setInterceptFileChooserDialog",
                                  "Emulation.setDeviceMetricsOverride"])
def test_a_new_tab_that_fails_a_later_step_is_closed_and_does_not_take_the_front(step):
    wire = FakeWire(fail=step)
    d = driver(wire)
    d.pages["s0"] = object()  # type: ignore[assignment]
    d.order, d.active = ["s0"], "s0"
    run(d, (target("page", "s1"), None))
    # By then its requests are already checked: Fetch.enable came before it ran.
    assert wire.timeline.index("Fetch.enable@s1") < wire.timeline.index(
        "Runtime.runIfWaitingForDebugger@s1")
    assert wire.closed_targets() == ["ts1"]
    assert d.active == "s0" and d.order == ["s0"] and "s1" not in d.pages
    assert UNSAFE_TAB_NOTE in d.notes()


def test_a_tab_that_sets_up_is_resumed_only_after_interception_is_on_and_comes_to_the_front():
    wire = FakeWire()
    d = driver(wire)
    run(d, (target("page", "s1"), None), (target("page", "s2"), None))
    assert wire.timeline.index("Fetch.enable@s2") < wire.timeline.index(
        "Runtime.runIfWaitingForDebugger@s2")
    assert d.active == "s2" and d.order == ["s1", "s2"] and d.pages["s2"].ready.is_set()


def test_a_frame_or_worker_whose_fetch_enable_fails_is_closed_and_not_resumed():
    wire = FakeWire(fail="Fetch.enable")
    d = driver(wire)
    run(d, (target("worker", "w1", "w"), None))
    assert not wire.resumed("w1") and wire.closed_targets() == ["ww1"]
    assert UNSAFE_TAB_NOTE in d.notes()


def test_a_frame_that_cannot_be_set_up_takes_its_tab_down_with_it():
    good = FakeWire()
    d = driver(good)
    run(d, (target("page", "s1"), None))
    assert "s1" in d.pages
    good.fail = "Fetch.enable"
    run(d, (target("iframe", "f1", "f"), "s1"))
    assert not good.resumed("f1")
    assert d.pages == {} and d.order == [] and d.active is None
    assert set(good.closed_targets()) == {"ff1", "ts1"}


def test_a_target_that_makes_no_requests_of_its_own_is_resumed_without_setup():
    wire = FakeWire(fail="Fetch.enable")
    d = driver(wire)
    run(d, (target("tab", "c1"), None))
    assert wire.resumed("c1") and wire.sent == [] and d.notes() == []


def test_a_tab_that_goes_away_while_it_is_being_set_up_is_not_added():
    wire = FakeWire(hold="Fetch.enable")

    async def go():
        d = driver(wire)
        task = asyncio.create_task(d._attached(target("page", "s1"), None))
        await asyncio.sleep(0.05)
        assert d.pages == {}  # not a tab until it is set up
        d._event("Target.detachedFromTarget", {"sessionId": "s1"}, None)
        wire.release.set()
        await task
        return d
    d = asyncio.run(go())
    assert d.pages == {} and d.order == [] and d.active is None


def test_a_tab_past_the_limit_is_closed_without_being_set_up():
    wire = FakeWire()
    d = driver(wire, max_tabs=1)
    run(d, (target("page", "s1"), None), (target("page", "s2"), None))
    assert d.order == ["s1"] and "ts2" in wire.closed_targets()
    assert not wire.resumed("s2") and ("Fetch.enable", "s2") not in wire.sent
