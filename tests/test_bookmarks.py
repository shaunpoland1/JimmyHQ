"""Stored places on the Tape ("read up to event N") that end up past the Tape's end, as
after a restore from a backup whose Tape is older than the file holding the place. Each
reader goes back to the Tape's end, so the next new event is not taken as already read.
Stored views and the Village have their own tests (test_lens.py, test_village_intake.py);
the readers inside the apps platform are tried next to their other tests."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from types import SimpleNamespace

from jimmyhq import memory
from jimmyhq.bookmark import within_tape
from jimmyhq.hands import execute
from jimmyhq.tape.registry import example_of
from test_memory_fakes import with_miner
from test_memory_mining import lib, talk, texts_shown  # noqa: F401 - lib is a fixture


def test_a_place_within_the_tape_is_kept_as_it_is():
    tape = SimpleNamespace(last_seq=lambda: 7)
    assert within_tape(tape, 0, "a reader") == 0
    assert within_tape(tape, 7, "a reader") == 7


def test_a_place_past_the_tapes_end_goes_back_to_the_end_and_the_log_names_the_reader(caplog):
    tape = SimpleNamespace(last_seq=lambda: 7)
    with caplog.at_level(logging.WARNING, logger="jimmyhq.bookmark"):
        assert within_tape(tape, 12, "the test reader") == 7
    assert "the test reader" in caplog.text and "12" in caplog.text


def test_the_hands_cursor_past_the_end_of_a_restored_tape_goes_back_and_misses_nothing_new(
        core, monkeypatch):
    seen = []

    async def handle(_core, event):
        seen.append(event.seq)

    monkeypatch.setattr(execute, "handle", handle)
    h = execute._hands(core)  # noqa: SLF001
    end = core.tape.last_seq()
    # The hands' file was copied after a Tape that had five fewer events.
    h.store.set_cursor(execute.CURSOR, end + 5)
    asyncio.run(execute.run(core))
    assert seen == [] and h.store.cursor(execute.CURSOR) == end
    decided = core.tape.print("bench.decided", example_of("bench.decided"), actor="owner")
    asyncio.run(execute.run(core))
    assert seen == [decided.seq]  # a number the hands had counted as read, acted on
    assert h.store.cursor(execute.CURSOR) == decided.seq


def test_a_memory_source_read_past_the_end_of_a_restored_tape_reads_the_next_new_events(
        core, cfg, lib):  # noqa: F811
    fake = with_miner(core, chat_reply="Understood.")
    talk(core, "First thing I said.")
    assert memory.mine(core, source="chat", trigger="owner")["sources"]["chat"]["read"] > 0
    # The memory store was copied after a Tape that had a thousand fewer events.
    conn = sqlite3.connect(cfg.data / "memory_store.db")
    conn.execute("UPDATE cursors SET cursor = ? WHERE source = 'chat'",
                 (str(core.tape.last_seq() + 1000),))
    conn.commit()
    conn.close()
    assert memory.mine(core, source="chat", trigger="owner")["sources"] == {}  # nothing new
    talk(core, "Second thing I said.")  # numbers the old cursor had counted as read
    assert memory.mine(core, source="chat", trigger="owner")["sources"]["chat"]["read"] > 0
    shown = texts_shown(fake)
    assert shown.count("First thing I said.") == 1 and "Second thing I said." in shown
