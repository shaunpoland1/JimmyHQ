"""The live door, under an engine that is slower than the device will wait.

The fake engine is held and released with events rather than sleeps, so the
only real waiting is the door's own short deadline.
"""

from __future__ import annotations

import threading

import pytest

from jimmyhq.speech import LiveDoor, LiveLimits

QUICK = 2.0  # a deadline no released chunk comes near
SHORT = 0.05  # a deadline a held chunk always misses


class Engine:
    """Transcribes b"name" as "name". Chunks named in ``held`` wait for release()."""

    def __init__(self, held=()):
        self.gates = {name: threading.Event() for name in held}
        self.started: list[str] = []
        self.threads: set[str] = set()
        self.running = 0
        self.most_at_once = 0
        self._lock = threading.Lock()
        self.entered = threading.Event()

    def __call__(self, chunk: bytes) -> str:
        name = chunk.decode()
        with self._lock:
            self.started.append(name)
            self.threads.add(threading.current_thread().name)
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        self.entered.set()
        try:
            if name in self.gates:
                assert self.gates[name].wait(5)
            if name == "bad":
                raise RuntimeError("the model fell over")
            return "" if name == "silence" else name
        finally:
            with self._lock:
                self.running -= 1

    def release(self, name: str) -> None:
        self.gates[name].set()


def door(engine, *, deadline=QUICK, backlog=10, window=60.0, **kwargs):
    return LiveDoor(engine, LiveLimits(deadline=deadline, backlog=backlog,
                                       duplicate_window=window), **kwargs)


@pytest.fixture
def heard():
    return []


def keeper(heard):
    return lambda number, text, late: heard.append((number, text, late))


def test_a_chunk_the_engine_keeps_up_with_is_answered_with_its_text(heard):
    live = door(Engine(), on_text=keeper(heard))
    answer = live.submit(b"hello")
    assert (answer.text, answer.status) == ("hello", "answered")
    assert live.drain() and heard == [(1, "hello", False)]
    assert live.stats() == {"backlog": 0, "answered": 1, "deferred": 0, "duplicate": 0,
                            "busy": 0, "failed": 0}


def test_a_chunk_that_misses_the_deadline_is_answered_empty_and_finished_behind(heard):
    engine = Engine(held=["slow"])
    live = door(engine, deadline=SHORT, on_text=keeper(heard))
    answer = live.submit(b"slow")
    assert (answer.text, answer.status) == ("", "deferred")
    assert heard == []
    engine.release("slow")
    assert live.drain()
    assert heard == [(1, "slow", True)]  # late, but not lost


def test_a_resend_of_the_same_chunk_is_not_transcribed_twice():
    engine = Engine()
    live = door(engine)
    assert live.submit(b"hello").status == "answered"
    again = live.submit(b"hello")
    assert (again.text, again.status) == ("", "duplicate")
    assert engine.started == ["hello"]


def test_a_resend_is_recognised_while_the_first_copy_is_still_being_transcribed():
    engine = Engine(held=["slow"])
    live = door(engine, deadline=SHORT)
    assert live.submit(b"slow").status == "deferred"
    assert live.submit(b"slow").status == "duplicate"
    engine.release("slow")
    assert live.drain() and engine.started == ["slow"]


def test_the_same_chunk_is_welcome_again_once_the_duplicate_window_has_passed():
    now = [100.0]
    engine = Engine()
    live = door(engine, window=30.0, clock=lambda: now[0])
    assert live.submit(b"hello").status == "answered"
    now[0] += 29.9
    assert live.submit(b"hello").status == "duplicate"
    now[0] += 0.2
    assert live.submit(b"hello").status == "answered"
    assert engine.started == ["hello", "hello"]


def test_a_duplicate_window_of_zero_turns_duplicate_dropping_off():
    engine = Engine()
    live = door(engine, window=0)
    assert [live.submit(b"hello").status for _ in range(2)] == ["answered", "answered"]


def test_a_full_backlog_answers_busy_at_once_and_drops_the_chunk(heard):
    engine = Engine(held=["one"])
    live = door(engine, deadline=SHORT, backlog=2, on_text=keeper(heard))
    assert live.submit(b"one").status == "deferred"
    assert live.submit(b"two").status == "deferred"
    assert live.stats()["backlog"] == 2
    refused = live.submit(b"three")
    assert (refused.text, refused.status) == ("", "busy")
    engine.release("one")
    assert live.drain()
    assert engine.started == ["one", "two"] and [h[1] for h in heard] == ["one", "two"]
    # A chunk turned away is not remembered, so the device's resend gets in.
    assert live.submit(b"three").status == "answered"


def test_chunks_are_transcribed_one_at_a_time_in_arrival_order_on_the_doors_own_thread(heard):
    engine = Engine(held=["a"])
    live = door(engine, deadline=SHORT, on_text=keeper(heard))
    names = ["a", "b", "c", "d", "e"]
    assert [live.submit(n.encode()).status for n in names] == ["deferred"] * 5
    engine.release("a")
    assert live.drain()
    assert engine.started == names
    assert heard == [(i + 1, n, True) for i, n in enumerate(names)]
    assert engine.most_at_once == 1
    assert engine.threads == {"speech-live"}
    assert threading.current_thread().name not in engine.threads


def test_many_callers_at_once_still_reach_the_engine_one_at_a_time():
    engine = Engine()
    live = door(engine, backlog=50)
    answers = []
    threads = [threading.Thread(target=lambda n=n: answers.append(live.submit(b"c%d" % n)))
               for n in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert sorted(a.text for a in answers) == sorted(f"c{n}" for n in range(20))
    assert engine.most_at_once == 1 and engine.threads == {"speech-live"}


def test_a_caller_waiting_behind_a_slow_chunk_gets_its_text_if_it_arrives_in_time(heard):
    engine = Engine(held=["slow"])
    live = door(engine, on_text=keeper(heard))
    answers = {}
    first = threading.Thread(target=lambda: answers.update(slow=live.submit(b"slow")))
    first.start()
    assert engine.entered.wait(5)
    second = threading.Thread(target=lambda: answers.update(next=live.submit(b"next")))
    second.start()
    engine.release("slow")
    first.join(5)
    second.join(5)
    assert answers["slow"].text == "slow" and answers["next"].text == "next"
    assert heard == [(1, "slow", False), (2, "next", False)]


def test_an_engine_failure_is_answered_and_the_door_keeps_working(heard):
    engine = Engine()
    live = door(engine, on_text=keeper(heard))
    failed = live.submit(b"bad")
    assert (failed.text, failed.status) == ("", "failed")
    assert live.submit(b"after").text == "after"
    assert live.drain() and heard == [(2, "after", False)]


def test_silence_is_answered_empty_and_not_passed_to_the_keeper(heard):
    live = door(Engine(), on_text=keeper(heard))
    answer = live.submit(b"silence")
    assert (answer.text, answer.status) == ("", "answered")
    assert live.drain() and heard == []


def test_a_keeper_that_fails_does_not_stop_the_door():
    def broken(number, text, late):
        raise RuntimeError("the store is full")

    live = door(Engine(), on_text=broken)
    assert live.submit(b"one").text == "one"
    assert live.submit(b"two").text == "two"


def test_a_closed_door_finishes_what_it_has_and_takes_nothing_new(heard):
    engine = Engine(held=["slow"])
    live = door(engine, deadline=SHORT, on_text=keeper(heard))
    assert live.submit(b"slow").status == "deferred"
    live.close()
    assert live.submit(b"more").status == "busy"
    engine.release("slow")
    assert live.drain() and heard == [(1, "slow", True)]
    live.close()


def test_the_doors_limits_come_from_settings(core):
    assert LiveLimits.from_settings(core.settings) == LiveLimits(
        deadline=8.0, backlog=30, duplicate_window=120.0)
    core.settings.set("speech.live_deadline_seconds", 2.5, actor="owner")
    core.settings.set("speech.live_backlog", 4, actor="owner")
    core.settings.set("speech.live_duplicate_seconds", 0, actor="owner")
    assert LiveLimits.from_settings(core.settings) == LiveLimits(
        deadline=2.5, backlog=4, duplicate_window=0.0)


def test_a_chunk_the_engine_failed_on_is_not_remembered_so_its_resend_is_tried_again():
    engine = Engine()
    live = door(engine)
    assert live.submit(b"bad").status == "failed"
    assert live.submit(b"bad").status == "failed"  # tried again, not waved off as a duplicate
    assert engine.started == ["bad", "bad"]
