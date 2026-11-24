"""The Village's reading of the Tape: where it picks up after the Tape went back."""

from __future__ import annotations

from types import SimpleNamespace

from jimmyhq.tape.registry import example_of
from jimmyhq.village import intake, roster


def test_a_village_cursor_past_the_end_of_a_restored_tape_goes_back_and_misses_nothing_new(
        core, monkeypatch):
    seen = []
    monkeypatch.setitem(intake.HANDLERS, "village.said", lambda _v, e: seen.append(e.seq))
    v = SimpleNamespace(core=core)
    end = core.tape.last_seq()
    # The Village's file was copied after a Tape that had five fewer events.
    roster.set_cursor(core, intake.CURSOR, end + 5)
    intake.read_tape(v)
    assert seen == [] and roster.cursor(core, intake.CURSOR) == end
    said = core.tape.print("village.said", example_of("village.said"), actor="owner")
    intake.read_tape(v)
    assert seen == [said.seq]  # a number the Village had counted as read, taken in
    assert roster.cursor(core, intake.CURSOR) == said.seq
