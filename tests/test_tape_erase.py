"""Erasure: the one way a payload leaves the Tape, and what is left behind."""

from __future__ import annotations

import os
import sqlite3
import time

import pytest

from jimmyhq.lens import Lens, LensRunner
from jimmyhq.tape import Tape, TapeConflict, TapeRefused

MARK = "quincemarmalade"


def said(core, text, **more):
    return core.tape.print("setting.updated", {"key": "note", "value": text}, actor="owner",
                           **more)


def file_bytes(cfg, name) -> bytes:
    """A database and whatever is still in its write-ahead log, as stored."""
    return b"".join(p.read_bytes() for p in sorted(cfg.data.glob(f"{name}*")) if p.is_file())


class Notes(Lens):
    name = "notes_test"
    version = 1
    kinds = ("setting.updated",)

    def initial(self):
        return {"notes": []}

    def fold(self, state, event):
        state["notes"].append(event.payload["value"])
        return state


class Resets(Notes):
    name = "resets_test"
    kinds = ("setting.reset",)

    def fold(self, state, event):
        state["notes"].append(event.seq)
        return state


def test_an_erased_event_keeps_its_place_and_loses_only_what_it_said(core):
    before = said(core, "kept")
    gone = said(core, MARK, subject="thing:1", cause=before.id, root=before.id)
    after = said(core, "also kept")
    count = core.tape.stats()["events"]
    done = core.tape.erase([gone.id], actor="owner", reason="setting.reset")
    assert (done.events, done.kinds, done.settled) == (1, {"setting.updated": 1}, True)
    back = core.tape.get(gone.id)
    assert back.model_dump() == gone.model_dump() | {
        "payload": {}, "readable": False, "erased": True}
    assert [e.seq for e in core.tape.after(before.seq - 1, kind="setting.updated")] == [
        before.seq, gone.seq, after.seq]
    assert core.tape.stats()["events"] == count + 1  # only the record of the erasure is new
    assert core.tape.get(before.id).payload["value"] == "kept"
    assert core.tape.by_subject("thing:1")[0].erased is True
    assert core.tape.caused_by(before.id)[0].id == gone.id


def test_an_erasure_is_recorded_with_counts_and_a_reason_and_never_the_words(core):
    first, second = said(core, MARK), said(core, MARK + " again")
    reset = core.tape.print("setting.reset", {"key": "note"}, actor="owner")
    done = core.tape.erase([first.id, second.id, reset.id], actor="owner",
                           reason="setting.reset", subject="thing:1", cause=reset.id)
    receipt = core.tape.latest(limit=1)[0]
    assert receipt.id == done.receipt.id and receipt.seq == done.receipt.seq
    assert (receipt.kind, receipt.actor, receipt.subject, receipt.cause) == (
        "tape.erased", "owner", "thing:1", reset.id)
    assert receipt.payload == {"reason": "setting.reset", "events": 3,
                               "kinds": {"setting.reset": 1, "setting.updated": 2}}
    assert MARK not in receipt.model_dump_json()


def test_erased_text_is_gone_from_the_bytes_of_the_database_file_and_its_log(cfg, core):
    short = said(core, MARK)
    long = said(core, (MARK + " ") * 4000)  # big enough to spill onto pages of its own
    core.tape.settle()  # the words are in the main file now, not only in the log
    assert file_bytes(cfg, "tape.db").count(MARK.encode()) > 3000
    core.tape.erase([short.id, long.id], actor="owner", reason="setting.reset")
    assert MARK.encode() not in file_bytes(cfg, "tape.db")
    said(core, "something after")
    core.tape.settle()
    assert MARK.encode() not in file_bytes(cfg, "tape.db")


def test_the_text_is_gone_from_the_file_even_when_a_reader_delayed_the_checkpoint(cfg, core):
    event = said(core, MARK)
    reader = sqlite3.connect(cfg.tape_db)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM tape").fetchone()  # holds the log open
        core.tape.erase([event.id], actor="owner", reason="setting.reset")
    finally:
        reader.close()  # closed before the file is read, as Windows needs
    assert core.tape.settle() is True
    assert MARK.encode() not in file_bytes(cfg, "tape.db")


def test_only_the_owner_or_the_system_may_erase(core):
    event = said(core, MARK)
    for actor in ("token:scout", "agent:scout", "app:notes", "system:memory", ""):
        with pytest.raises(TapeRefused):
            core.tape.erase([event.id], actor=actor, reason="setting.reset")
    assert core.tape.get(event.id).payload["value"] == MARK
    assert not core.tape.latest(kind="tape.erased")
    assert core.tape.erase([event.id], actor="system", reason="setting.reset").events == 1


def test_erasing_twice_or_erasing_nothing_changes_nothing_and_prints_nothing(core):
    event = said(core, MARK)
    assert core.tape.erase([event.id, event.id], actor="owner", reason="setting.reset").events == 1
    end = core.tape.last_seq()
    for ids in ([event.id], [], ["01JABCDEFGHJKMNPQRSTVWXYZ0"]):
        again = core.tape.erase(ids, actor="owner", reason="setting.reset")
        assert (again.events, again.receipt, again.orphans) == (0, None, ())
    assert core.tape.last_seq() == end


def test_the_record_of_an_erasure_cannot_be_erased(core):
    event = said(core, MARK)
    receipt = core.tape.erase([event.id], actor="owner", reason="setting.reset").receipt
    other = said(core, "kept")
    with pytest.raises(ValueError, match="cannot itself be erased"):
        core.tape.erase([other.id, receipt.id], actor="owner", reason="setting.reset")
    assert core.tape.get(other.id).erased is False  # all or nothing


def test_nothing_is_erased_when_the_subject_has_moved_on(core):
    event = said(core, MARK, subject="thing:1")
    said(core, "newer", subject="thing:1")
    with pytest.raises(TapeConflict):
        core.tape.erase([event.id], actor="owner", reason="setting.reset", subject="thing:1",
                        expect_subject_seq=event.seq)
    assert core.tape.get(event.id).erased is False


def test_a_vault_file_is_handed_back_only_when_no_other_event_names_it(core):
    alone, shared = core.vault.put_text("only here"), core.vault.put_text("said twice")
    first = said(core, {"a": alone, "b": shared})
    said(core, {"b": shared})
    done = core.tape.erase([first.id], actor="owner", reason="setting.reset")
    assert done.orphans == (alone,)
    assert core.vault.remove(alone) is True and core.vault.has(alone) is False
    assert core.vault.remove(alone) is False and core.vault.remove("sha256:nonsense") is False
    assert core.vault.get_text(shared) == "said twice"


def test_a_tape_from_before_erasure_existed_gains_the_column_and_keeps_its_events(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE tape (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,"
        " ts INTEGER NOT NULL, kind TEXT NOT NULL, v INTEGER NOT NULL, actor TEXT NOT NULL,"
        " cause TEXT, root TEXT, subject TEXT, payload TEXT NOT NULL);"
        "INSERT INTO tape (id, ts, kind, v, actor, payload) VALUES"
        " ('01JABCDEFGHJKMNPQRSTVWXYZ0', 1, 'setting.reset', 1, 'owner', '{\"key\":\"k\"}');")
    conn.close()
    first, second = Tape(path), Tape(path)  # two processes opening it together
    try:
        (event,) = first.after(0)
        assert (event.payload, event.erased, event.readable) == ({"key": "k"}, False, True)
        assert second.erase([event.id], actor="owner", reason="setting.reset").events == 1
        assert first.get(event.id).erased is True
    finally:
        first.close()
        second.close()


def test_an_erased_event_folds_as_nothing_and_the_stored_view_forgets_it(cfg, core):
    lens = Notes()
    said(core, "kept")
    gone = said(core, MARK)
    said(core, (MARK + " ") * 3000)
    assert MARK in core.lenses.read(lens)["notes"]
    core.tape.erase([gone.id, core.tape.latest(limit=1)[0].id], actor="owner",
                    reason="setting.reset")
    assert core.lenses.read(lens) == {"notes": ["kept"]}
    assert MARK.encode() not in file_bytes(cfg, "views.db")
    fresh = LensRunner(core.tape, cfg.views_db)  # and it stays forgotten after a restart
    try:
        assert fresh.read(lens) == {"notes": ["kept"]}
    finally:
        fresh.close()


def test_another_process_with_the_old_view_in_hand_rebuilds_it_too(cfg, core):
    lens = Notes()
    gone = said(core, MARK)
    other = LensRunner(core.tape, cfg.views_db)
    try:
        assert other.read(lens) == {"notes": [MARK]}  # cached in the other process
        core.tape.erase([gone.id], actor="owner", reason="setting.reset")
        assert other.read(lens) == {"notes": []}
    finally:
        other.close()


def test_a_lens_that_folds_none_of_the_erased_kinds_is_not_replayed(core):
    class Fragile(Resets):
        def fold(self, state, event):
            if replaying:
                raise AssertionError("replayed for an erasure that did not touch it")
            return super().fold(state, event)

    replaying = False
    lens = Fragile()
    core.tape.print("setting.reset", {"key": "k"}, actor="owner")
    gone = said(core, MARK)
    before = core.lenses.read(lens)
    replaying = True
    core.tape.erase([gone.id], actor="owner", reason="setting.reset")
    assert core.lenses.read(lens) == before


def test_a_lens_may_take_an_erasure_out_of_its_own_state_instead_of_replaying(cfg, core):
    class Careful(Notes):
        name = "careful_test"

        def fold(self, state, event):
            if replaying:
                raise AssertionError("replayed although it handled the erasure itself")
            return super().fold(state, event)

        def erased(self, state, event):
            state["notes"] = [n for n in state["notes"] if MARK not in n]
            return state

    replaying = False
    lens = Careful()
    said(core, "kept")
    gone = said(core, (MARK + " ") * 3000)
    assert len(core.lenses.read(lens)["notes"]) == 2
    replaying = True
    core.tape.erase([gone.id], actor="owner", reason="setting.reset")
    assert core.lenses.read(lens) == {"notes": ["kept"]}
    assert MARK.encode() not in file_bytes(cfg, "views.db")


# --- what an erase still owes, and who pays it ------------------------------------------


def old(cfg, ref, seconds=120):
    """Make a Vault file look as if it was stored a while ago."""
    path = cfg.data / "vault" / ref[7:9] / ref[9:]
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_log_a_reader_kept_from_being_emptied_is_remembered_until_it_is(cfg, core, monkeypatch):
    event = said(core, MARK)
    assert core.tape.pending() == {"settle": False, "vault": 0}
    reader = sqlite3.connect(cfg.tape_db)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM tape").fetchone()  # holds the log open
        monkeypatch.setattr("jimmyhq.tape.store.time.sleep", lambda s: None)
        done = core.tape.erase([event.id], actor="owner", reason="setting.reset")
        assert done.settled is False
        other = Tape(cfg.tape_db)  # the debt is in the file: any process sees it
        try:
            assert other.pending()["settle"] is True
        finally:
            other.close()
    finally:
        reader.close()
    assert core.tape.finish(core.vault) == {"settle": False, "vault": 0}
    assert MARK.encode() not in file_bytes(cfg, "tape.db")


def test_an_erase_that_settled_owes_nothing(core):
    event = said(core, MARK)
    assert core.tape.erase([event.id], actor="owner", reason="setting.reset").settled is True
    assert core.tape.pending() == {"settle": False, "vault": 0}


def test_a_vault_file_left_unnamed_is_queued_with_the_erase_and_removed_when_safe(cfg, core):
    stale, fresh = core.vault.put_text("stored long ago"), core.vault.put_text("stored just now")
    again = core.vault.put_text("said again later")
    event = said(core, {"a": stale, "b": fresh, "c": again})
    old(cfg, stale)
    old(cfg, again)
    done = core.tape.erase([event.id], actor="owner", reason="setting.reset")
    assert set(done.orphans) == {stale, fresh, again}
    assert core.tape.pending()["vault"] == 3  # queued in the erase's own transaction
    said(core, {"c": again})  # another event names one of them again
    assert core.tape.release_orphans(core.vault) == 1
    assert not core.vault.has(stale)  # nothing names it and nobody has just used it
    assert core.vault.has(again)  # named again: no longer an orphan
    assert core.vault.has(fresh)  # stored a moment ago: its event may be on its way
    old(cfg, fresh)
    assert core.tape.release_orphans(core.vault) == 0 and not core.vault.has(fresh)


def test_putting_a_file_again_marks_it_as_in_use_so_it_is_not_removed(cfg, core):
    ref = core.vault.put_text("said twice")
    old(cfg, ref)
    assert core.vault.discard(ref, 60_000) is True and not core.vault.has(ref)
    assert core.vault.discard(ref, 60_000) is True  # already gone
    ref = core.vault.put_text("said twice")
    old(cfg, ref)
    assert core.vault.put_text("said twice") == ref  # a writer about to print its event
    assert core.vault.discard(ref, 60_000) is False
    assert core.vault.get_text(ref) == "said twice"
    leftovers = [p.name for p in (cfg.data / "vault").rglob("*") if p.is_file()]
    assert leftovers == [ref[9:]]
