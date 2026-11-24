"""``app.ui.count`` and ``@app.ui.counter`` in the Python SDK: against the fake core, which
refuses what JimmyHQ refuses, and against the real app API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, CallError  # noqa: E402
from jimmyhq_app._transport import HttpTransport  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import client_for, install_full  # noqa: E402
from jimmyhq.apps import counts  # noqa: E402


def made(core: FakeCore, **jobs) -> App:
    with core.installed():
        app = App()
    for name, fn in jobs.items():
        app.job(name, every=60)(fn)
    return app


def test_a_program_says_a_count_and_the_fake_core_records_it():
    core = FakeCore("boards")
    app = made(core)
    app.ui.count(3)
    app.ui.count(0)
    assert core.counts == [3, 0] and core.count == 0


@pytest.mark.parametrize("n", [-1, 1000, 2.5, "3", True])
def test_the_fake_core_refuses_the_numbers_jimmyhq_refuses_with_the_same_words(n):
    core = FakeCore("boards")
    app = made(core)
    with pytest.raises(CallError) as caught:
        app.ui.count(n)
    assert caught.value.code == "invalid"
    assert "whole number from 0 to 999" in caught.value.message
    assert "Nothing was changed" in caught.value.message
    assert core.counts == []


def test_the_fake_core_and_jimmyhq_refuse_in_the_same_sentence():
    core = FakeCore("boards")
    app = made(core)
    with pytest.raises(CallError) as fake:
        app.ui.count(1000)
    from jimmyhq.apps import bridge

    with pytest.raises(bridge.BridgeError) as real:
        counts.check_count(1000)
    assert fake.value.message == real.value.message


def test_a_counter_is_asked_after_every_delivery_and_says_only_what_changed():
    core = FakeCore("boards")
    waiting = {"n": 2}
    asked = []

    def counter() -> int:
        asked.append(waiting["n"])
        return waiting["n"]

    app = made(core, sweep=lambda: waiting.update(n=5))
    app.ui.counter(counter)
    app.run()  # a fake core: it only registers, and the count is said at the start
    assert core.counts == [2]
    core.run_job("sweep")
    assert core.counts == [2, 5]
    core.run_job("sweep")  # nothing changed: nothing is said again
    assert core.counts == [2, 5] and asked == [2, 5, 5]


def test_a_count_above_999_is_said_as_999_and_a_failing_counter_never_fails_the_work():
    core = FakeCore("boards")
    state = {"n": 4000, "boom": False}

    def counter() -> int:
        if state["boom"]:
            raise RuntimeError("the table is locked")
        return state["n"]

    done = []
    app = made(core, work=lambda: done.append(1))
    app.ui.counter(counter)
    core.run_job("work")
    assert core.counts == [999]
    state["boom"] = True
    core.run_job("work")  # the delivery still succeeds
    assert done == [1, 1] and core.counts == [999]


def test_a_count_the_core_refuses_is_logged_and_the_work_still_succeeds():
    core = FakeCore("boards")
    app = made(core, work=lambda: "done")
    app.ui.counter(lambda: 1)

    def too_fast(args):
        raise CallError("quota", "Too fast. Nothing was changed.")

    core._ui_count = too_fast  # noqa: SLF001
    assert core.run_job("work") == "done"
    assert core.counts == []


@pytest.fixture
def real(core, tmp_path):
    install_full(core, tmp_path, "boards")
    with client_for(core) as web:
        transport = HttpTransport(client=web, token=core.auth.mint_app_token("boards"))
        yield App(transport=transport, app_id="boards", data_dir=tmp_path / "data")


def test_the_real_app_api_keeps_what_the_sdk_says_and_refuses_what_it_should(core, real):
    real.ui.count(7)
    assert counts.counts(core).get("boards") == 7
    with pytest.raises(CallError, match="whole number from 0 to 999"):
        real.ui.count(1000)
    assert counts.counts(core).get("boards") == 7


def test_a_counter_says_its_number_to_the_real_app_api_after_a_delivery(core, real):
    real.job("sweep", every=60)(lambda: None)
    real.ui.counter(lambda: 12)
    real.handle({"id": "d1", "type": "job", "data": {"name": "sweep"}})
    assert counts.counts(core).get("boards") == 12
