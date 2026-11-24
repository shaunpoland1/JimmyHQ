"""The Library: path safety, saving, scanning hand edits, facts and history."""

from __future__ import annotations

import importlib
import os
import re
import sys
import threading
import time

import pytest

from jimmyhq import memory
from jimmyhq.core import open_core
from jimmyhq.memory import notes as md
from jimmyhq.memory.errors import LibraryError
from jimmyhq.memory.paths import clean_path
from test_memory_fakes import tape_text

ID = r"<!-- m:[0-9A-HJKMNP-TV-Z]{26} -->"


@pytest.fixture
def lib(core):
    memory.status(core)  # first use creates the Library
    return core.config.data / "memory"


def events(core, kind):
    return [e for e in core.tape.after(0, limit=100_000) if e.kind == kind]


def facts(core, note=None):
    found = memory.list_facts(core, note=note, limit=200)["facts"]
    return {f["text"]: f for f in found}


def write(lib, rel, data):
    path = lib / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def settle(path):
    """Make a file look as if it was last written a minute ago, not just now."""
    old = time.time() - 60
    os.utime(path, (old, old))
    return path


# --- paths -----------------------------------------------------------------------


@pytest.mark.parametrize("path", [
    "", "/etc/passwd.md", "../x.md", "shared/../../x.md", "shared/..", "shared\\x.md",
    "C:/x.md", "C:\\x.md", "c:x.md", "\\\\server\\share\\x.md", "shared/x.md\x00",
    "shared/\x07x.md", "shared/x\n.md", "shared/x\x7f.md", "CON.md", "shared/nul.md",
    "shared/com1.md", "shared/LPT9.txt.md", "aux/x.md", "shared/x.md.", "shared/x.md ",
    "shared/dir./x.md", "shared/dir /x.md", "shared/ x.md", "shared/x.txt", "shared/x",
    "shared//x.md", "shared/./x.md", ".hidden/x.md", "shared/.x.md", "~/x.md",
    "shared/a:b.md", "shared/x.md/", ".md", "shared/.md", "shared/x?.md", "shared/x*.md",
    'shared/"x".md', "shared/<x>.md", "shared/x|y.md", "x" * 500 + ".md",
    "shared/" + "y" * 130 + ".md", "/".join(["d"] * 13) + "/x.md", "shared/\ud800.md",
    5, None, ["shared/x.md"],
])
def test_a_path_that_could_leave_the_library_or_break_a_disk_is_refused(core, lib, path):
    with pytest.raises(LibraryError) as e:
        clean_path(path)
    assert e.value.status == 422 and "shared/ideas.md" in e.value.detail
    with pytest.raises(LibraryError):
        memory.save_note(core, path, "text", actor="owner")
    assert sorted(p.name for p in lib.parent.iterdir() if p.suffix == ".md") == []


@pytest.mark.parametrize("path", [
    "shared/x.md", "agents/scout/notes/today.md", "top.md", "shared/Caf\u00e9 notes.md",
    "shared/x.MD", "shared/history/old.md",
])
def test_an_ordinary_path_is_accepted(core, lib, path):
    assert clean_path(path) == path
    assert memory.save_note(core, path, "Hello.\n", actor="owner")["path"] == path
    assert (lib / path).read_text() == "Hello.\n"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symbolic links")
def test_a_link_is_never_followed_out_of_the_library(core, lib, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.md").write_text("- A fact that lives elsewhere\n")
    try:
        os.symlink(outside, lib / "shared" / "away")
        os.symlink(outside / "secret.md", lib / "shared" / "linked.md")
    except OSError:
        pytest.skip("this system does not allow symbolic links")
    result = memory.scan(core)
    assert result["facts_added"] == 0
    skipped = {s["path"]: s["reason"] for s in memory.status(core)["skipped"]}
    assert "link" in skipped["shared/linked.md"] and "shared/away/secret.md" not in skipped
    assert (outside / "secret.md").read_text() == "- A fact that lives elsewhere\n"
    for path in ("shared/away/new.md", "shared/away/secret.md", "shared/linked.md"):
        with pytest.raises(LibraryError, match="link"):
            memory.save_note(core, path, "x", actor="owner")
        with pytest.raises(LibraryError):
            memory.read_note(core, path)
    assert sorted(p.name for p in outside.iterdir()) == ["secret.md"]


def test_a_spelling_that_differs_only_in_case_is_refused(core, lib):
    memory.save_note(core, "shared/Plans.md", "x\n", actor="owner")
    with pytest.raises(LibraryError, match="Plans.md") as e:
        memory.save_note(core, "shared/plans.md", "y\n", actor="owner")
    assert e.value.status == 409


# --- the starter Library ---------------------------------------------------------


def test_first_use_creates_a_small_generic_library(core, lib):
    names = sorted(p.name for p in (lib / "shared").iterdir())
    assert names == ["about-me.md", "decisions.md", "people.md", "preferences.md",
                     "projects.md", "rules.md"]
    assert (lib / "agents").is_dir()
    assert (lib / "shared" / "people.md").read_text().startswith("# People\n")
    status = memory.status(core)
    assert status["counts"] == {"notes": 6, "facts": 0, "chunks": 0, "chunks_with_vectors": 0}
    assert {e.actor for e in events(core, "memory.note.saved")} == {"system"}
    # A starter note the owner deletes is not brought back.
    (lib / "shared" / "rules.md").unlink()
    memory.scan(core)
    memory.status(core)
    assert not (lib / "shared" / "rules.md").exists()


# --- which lines are facts -------------------------------------------------------


def test_only_top_level_bullets_outside_code_are_facts():
    text = ("# Title\n\n- one\n* two\n+ three\n  - nested under three\n  wrapped\n"
            "1. numbered\n  - indented bullet\n-no space\n- \n- - -\n---\n"
            "```\n- inside code\n```\n~~~\n- inside tildes\n~~~\nplain\n- last")
    parsed = md.parse(text)
    assert [f.text for f in parsed.facts] == ["one", "two", "three", "last"]
    assert parsed.body(parsed.facts[2]) == "three\n  - nested under three\n  wrapped"
    assert parsed.render() == text


def test_passages_are_facts_alone_and_free_text_by_heading_without_comments():
    text = ("# Garden\n\n<!-- a note to the editor -->\nSoil is clay.\nIt drains slowly.\n\n"
            "## Beds\n\n- Bed one has beans <!-- m:01JABCDEFGHJKMNPQRSTVWXYZ0 -->\n"
            "  - planted in spring\n\nWater <!-- hidden --> weekly.\n")
    got = [(c.fact_id, c.heading, c.text) for c in md.chunks(md.parse(text), 800)]
    assert got == [
        (None, "Garden", "Soil is clay.\nIt drains slowly."),
        ("01JABCDEFGHJKMNPQRSTVWXYZ0", "Garden > Beds",
         "Bed one has beans\n  - planted in spring"),
        (None, "Garden > Beds", "Water  weekly."),
    ]


def test_long_free_text_is_cut_at_sentence_ends_within_the_size():
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    pieces = md.chunks(md.parse(text), 300)
    assert len(pieces) > 5 and all(len(c.text) <= 300 for c in pieces)
    assert all(c.text.endswith(".") for c in pieces)
    assert " ".join(c.text for c in pieces) == text


# --- saving through the app ------------------------------------------------------


def test_saving_gives_typed_facts_an_id_and_returns_the_text_as_stored(core, lib):
    saved = memory.save_note(core, "shared/ideas.md", "# Ideas\n\n- Build a shed\nText.\n",
                             actor="owner")
    assert re.fullmatch(rf"# Ideas\n\n- Build a shed {ID}\nText\.\n", saved["text"])
    assert (lib / "shared/ideas.md").read_text() == saved["text"]
    (fact,) = saved["facts"]
    assert (fact["text"], fact["note"], fact["line"], fact["saved_by"], fact["confirmed"]) == (
        "Build a shed", "shared/ideas.md", 3, "owner", True)
    assert fact["id"] in saved["text"]
    assert not [p for p in (lib / "shared").iterdir() if p.name.endswith(".tmp")]


def test_a_save_against_a_stale_revision_is_refused_and_writes_nothing(core, lib):
    first = memory.save_note(core, "shared/ideas.md", "one\n", actor="owner")
    second = memory.save_note(core, "shared/ideas.md", "two\n", actor="owner",
                              revision=first["revision"])
    with pytest.raises(LibraryError, match="Reload it") as e:
        memory.save_note(core, "shared/ideas.md", "three\n", actor="owner",
                         revision=first["revision"])
    assert e.value.status == 409
    assert (lib / "shared/ideas.md").read_text() == "two\n"
    # A hand edit also makes the revision stale.
    write(lib, "shared/ideas.md", "edited by hand\n")
    with pytest.raises(LibraryError, match="Reload it"):
        memory.save_note(core, "shared/ideas.md", "four\n", actor="owner",
                         revision=second["revision"])
    assert (lib / "shared/ideas.md").read_text() == "edited by hand\n"
    with pytest.raises(LibraryError):  # a revision for a note that is gone
        memory.save_note(core, "shared/none.md", "x\n", actor="owner", revision="0" * 64)
    assert not (lib / "shared/none.md").exists()


def test_a_save_that_fails_midway_leaves_the_old_note_whole(core, lib, monkeypatch):
    memory.save_note(core, "shared/ideas.md", "the old text\n", actor="owner")

    def broken(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(LibraryError, match="could not be written"):
        memory.save_note(core, "shared/ideas.md", "the new text\n", actor="owner")
    monkeypatch.undo()
    assert (lib / "shared/ideas.md").read_text() == "the old text\n"
    assert [p.name for p in (lib / "shared").iterdir() if ".tmp" in p.name] == []


def test_a_note_larger_than_the_limit_is_refused(core, lib):
    core.settings.set("memory.max_note_kb", 1, actor="owner")
    with pytest.raises(LibraryError, match="larger than the 1 KB limit") as e:
        memory.save_note(core, "shared/big.md", "x" * 2000, actor="owner")
    assert e.value.status == 413 and not (lib / "shared/big.md").exists()


def test_moving_a_note_keeps_its_facts_and_deleting_it_retires_them(core, lib):
    saved = memory.save_note(core, "shared/a.md", "- Likes rain\n", actor="owner")
    fact_id = saved["facts"][0]["id"]
    moved = memory.move_note(core, "shared/a.md", "shared/sub/b.md", actor="owner")
    assert moved["path"] == "shared/sub/b.md" and moved["facts"][0]["id"] == fact_id
    assert not (lib / "shared/a.md").exists()
    assert events(core, "memory.note.removed")[-1].payload["moved_to"] == "shared/sub/b.md"
    assert events(core, "memory.note.saved")[-1].payload["moved_from"] == "shared/a.md"
    assert events(core, "memory.fact.edited")[-1].payload["changed"] == ["note"]
    with pytest.raises(LibraryError) as e:
        memory.move_note(core, "shared/sub/b.md", "shared/people.md", actor="owner")
    assert e.value.status == 409
    with pytest.raises(LibraryError) as e:
        memory.move_note(core, "shared/gone.md", "shared/c.md", actor="owner")
    assert e.value.status == 404
    memory.delete_note(core, "shared/sub/b.md", actor="owner")
    assert not (lib / "shared/sub/b.md").exists()
    retired = memory.get_fact(core, fact_id, live_only=False)
    assert retired.retired_ms and retired.retired_reason == "Its note was deleted."
    with pytest.raises(LibraryError) as e:
        memory.delete_note(core, "shared/sub/b.md", actor="owner")
    assert e.value.status == 404


# --- hand edits ------------------------------------------------------------------


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
@pytest.mark.parametrize("ending", ["with newline", "without"])
def test_a_scan_gives_hand_typed_facts_an_id_and_changes_nothing_else(core, lib, eol, ending):
    lines = ["# Notes", "", "Some text  ", "- First fact", "  - nested   ", "* Second fact  ",
             "", "```", "- not a fact", "```", "\t tabbed line", "- Third: caf\u00e9 \u2603"]
    raw = eol.join(lines) + (eol if ending == "with newline" else "")
    path = write(lib, "shared/notes.md", raw)
    result = memory.scan(core)
    assert (result["added"], result["facts_added"], result["ids_written"]) == (1, 3, 1)
    after = path.read_bytes().decode("utf-8")
    assert after != raw
    # Taking the ids back out gives the original bytes exactly.
    assert re.sub(rf" {ID}", "", after).encode("utf-8") == raw.encode("utf-8")
    assert len(re.findall(ID, after)) == 3
    assert after.endswith(eol) is (ending == "with newline")
    assert f"* Second fact <!-- m:{facts(core)['Second fact']['id']} -->  {eol}" in after
    assert set(facts(core, "shared/notes.md")) == {"First fact", "Second fact",
                                                   "Third: caf\u00e9 \u2603"}
    assert all(f["saved_by"] == "owner:disk" for f in facts(core).values())
    # Nothing more to do: the file is not touched again.
    stamp = path.stat().st_mtime_ns
    again = memory.scan(core)
    assert (again["changed"], again["ids_written"], again["facts_added"]) == (0, 0, 0)
    assert path.stat().st_mtime_ns == stamp and path.read_bytes().decode("utf-8") == after


def test_editing_a_line_edits_the_fact_and_deleting_it_retires_the_fact(core, lib):
    path = write(lib, "shared/notes.md", "- Tea in the morning\n- Walks at noon\n")
    memory.scan(core)
    before = facts(core)
    tea, walks = before["Tea in the morning"]["id"], before["Walks at noon"]["id"]
    text = path.read_text().replace("Tea in the morning", "Coffee in the morning")
    path.write_text(text.replace(f"- Walks at noon <!-- m:{walks} -->\n", ""))
    result = memory.scan(core)
    assert (result["facts_edited"], result["facts_retired"], result["facts_added"]) == (1, 1, 0)
    after = facts(core)
    assert set(after) == {"Coffee in the morning"} and after["Coffee in the morning"]["id"] == tea
    edited = events(core, "memory.fact.edited")[-1]
    assert edited.actor == "owner:disk" and edited.payload["changed"] == ["text"]
    retired = events(core, "memory.fact.retired")[-1]
    assert retired.payload["fact_id"] == walks and retired.actor == "owner:disk"
    assert memory.get_fact(core, walks, live_only=False).retired_ms is not None
    with pytest.raises(LibraryError):
        memory.get_fact(core, walks)
    # Typing the line back, id and all, brings the same fact back.
    path.write_text(path.read_text() + f"- Walks at noon <!-- m:{walks} -->\n")
    memory.scan(core)
    assert facts(core)["Walks at noon"]["id"] == walks
    assert events(core, "memory.fact.saved")[-1].payload["revived"] is True


def test_moving_a_line_between_notes_moves_the_fact_and_copying_it_makes_a_new_one(core, lib):
    a = write(lib, "shared/a.md", "- Stays here\n- Moves away\n")
    b = write(lib, "shared/b.md", "# B\n")
    memory.scan(core)
    ids = {t: f["id"] for t, f in facts(core).items()}
    moving = f"- Moves away <!-- m:{ids['Moves away']} -->\n"
    a.write_text(a.read_text().replace(moving, ""))
    copied = f"- Stays here <!-- m:{ids['Stays here']} -->\n"
    b.write_text(b.read_text() + moving + copied)
    result = memory.scan(core)
    assert (result["facts_edited"], result["facts_added"], result["facts_retired"]) == (1, 1, 0)
    assert memory.get_fact(core, ids["Moves away"]).note == "shared/b.md"
    assert events(core, "memory.fact.edited")[-1].payload["from_note"] == "shared/a.md"
    assert memory.get_fact(core, ids["Stays here"]).note == "shared/a.md"
    copies = [f for f in memory.list_facts(core)["facts"] if f["text"] == "Stays here"]
    assert len(copies) == 2 and len({f["id"] for f in copies}) == 2
    assert ids["Stays here"] not in b.read_text()


def test_renaming_a_file_by_hand_is_seen_as_a_move(core, lib):
    write(lib, "shared/old-name.md", "# Things\n\n- Keeps its id\n")
    memory.scan(core)
    fact_id = facts(core)["Keeps its id"]["id"]
    (lib / "agents" / "scout").mkdir()
    os.rename(lib / "shared/old-name.md", lib / "agents/scout/new-name.md")
    result = memory.scan(core)
    assert (result["moved"], result["added"], result["removed"], result["facts_retired"]) == (
        1, 0, 0, 0)
    assert memory.get_fact(core, fact_id).note == "agents/scout/new-name.md"
    saved = events(core, "memory.note.saved")[-1]
    assert saved.payload["moved_from"] == "shared/old-name.md" and saved.actor == "owner:disk"
    assert "agents/scout" in memory.library(core).tree()["folders"]


def test_a_vanished_file_is_removed_and_its_facts_retired(core, lib):
    path = write(lib, "shared/gone.md", "- Here today\n")
    memory.scan(core)
    path.unlink()
    result = memory.scan(core)
    assert (result["removed"], result["facts_retired"]) == (1, 1)
    assert "shared/gone.md" not in [n["path"] for n in memory.library(core).tree()["notes"]]
    assert events(core, "memory.note.removed")[-1].actor == "owner:disk"


def test_files_that_cannot_be_notes_are_skipped_with_a_reason_and_never_crash(core, lib):
    core.settings.set("memory.max_note_kb", 1, actor="owner")
    write(lib, "shared/latin.md", "caf\xe9 au lait\n".encode("latin-1"))
    write(lib, "shared/huge.md", "- a fact\n" * 400)
    write(lib, "shared/binary.md", b"abc\x00def")
    write(lib, "shared/.hidden.md", "- hidden\n")
    write(lib, "shared/.obsidian/workspace.md", "- hidden folder\n")
    write(lib, "shared/draft.md~", "- backup\n")
    write(lib, "shared/#draft.md#", "- autosave\n")
    write(lib, "shared/notes.md.tmp", "- temp\n")
    write(lib, "shared/.notes.md.swp", "- swap\n")
    write(lib, "shared/picture.png", b"\x89PNG")
    write(lib, "shared/fine.md", "- A good fact\n")
    result = memory.scan(core)
    assert result["skipped"] == 3 and result["facts_added"] == 1
    skipped = {s["path"]: s["reason"] for s in memory.status(core)["skipped"]}
    assert set(skipped) == {"shared/latin.md", "shared/huge.md", "shared/binary.md"}
    assert "UTF-8" in skipped["shared/latin.md"] and "1 KB limit" in skipped["shared/huge.md"]
    assert set(facts(core)) == {"A good fact"}
    with pytest.raises(LibraryError, match="not part of memory") as e:
        memory.read_note(core, "shared/latin.md")
    assert e.value.status == 422
    # Fixing the file brings it in; raising the limit brings the big one in.
    write(lib, "shared/latin.md", "caf\u00e9 au lait\n")
    core.settings.set("memory.max_note_kb", 512, actor="owner")
    result = memory.scan(core)
    assert result["skipped"] == 1 and result["facts_added"] == 400


def test_a_note_that_becomes_unreadable_leaves_memory_until_it_is_fixed(core, lib):
    path = write(lib, "shared/notes.md", "- Still true\n")
    memory.scan(core)
    fact_id = facts(core)["Still true"]["id"]
    good = path.read_bytes()
    path.write_bytes(b"\xff\xfe broken")
    assert memory.scan(core)["facts_retired"] == 1
    path.write_bytes(good)
    memory.scan(core)
    assert memory.get_fact(core, fact_id).text == "Still true"


def test_an_unchanged_note_is_not_read_again(core, lib, monkeypatch):
    for i in range(20):
        write(lib, f"shared/n{i}.md", f"- Fact number {i}\n")
    memory.scan(core)
    old = time.time() - 60
    for path in lib.rglob("*.md"):
        os.utime(path, (old, old))
    memory.scan(core)  # sees the new stamps, finds the same bytes
    from pathlib import Path
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    result = memory.scan(core)
    assert reads == [] and result["notes"] == 26 and result["changed"] == 0
    write(lib, "shared/n3.md", "- Fact number 3, changed\n")
    memory.scan(core)
    assert [p.name for p in reads] == ["n3.md"]


def test_hand_edits_are_picked_up_when_a_note_is_read_unless_that_is_switched_off(core, lib):
    settle(write(lib, "shared/notes.md", "- Typed by hand\n"))
    assert memory.read_note(core, "shared/notes.md")["facts"][0]["text"] == "Typed by hand"
    core.settings.set("memory.watch_disk", False, actor="owner")
    write(lib, "shared/other.md", "- Also typed by hand\n")
    got = memory.read_note(core, "shared/other.md")
    assert got["text"] == "- Also typed by hand\n" and got["facts"] == []
    memory.scan(core)  # pressing Scan still works
    assert memory.read_note(core, "shared/other.md")["facts"][0]["text"] == "Also typed by hand"


# --- history ---------------------------------------------------------------------


def test_history_shows_what_a_note_said_before_including_a_hand_edit(core, lib):
    lb = memory.library(core)
    memory.save_note(core, "shared/log.md", "version one\n", actor="owner")
    write(lib, "shared/log.md", "version two, by hand\n")
    memory.save_note(core, "shared/log.md", "version three\n", actor="owner")
    memory.move_note(core, "shared/log.md", "shared/journal.md", actor="owner")
    memory.save_note(core, "shared/journal.md", "version four\n", actor="owner")
    versions = lb.history("shared/journal.md")
    assert [v["actor"] for v in versions] == ["owner", "owner", "owner", "owner:disk", "owner"]
    assert [v["note"] for v in versions] == ["shared/journal.md"] * 2 + ["shared/log.md"] * 3
    texts = [lb.version("shared/journal.md", v["version"])["text"] for v in versions]
    assert texts == ["version four\n", "version three\n", "version three\n",
                     "version two, by hand\n", "version one\n"]
    assert all(v["available"] and v["size"] > 0 for v in versions)
    newest = versions[0]["version"]
    other_id = newest[:-1] + ("1" if newest.endswith("0") else "0")  # never the id itself
    for bad in ("nope", other_id, events(core, "system.opened")[0].id):
        with pytest.raises(LibraryError) as e:
            lb.version("shared/journal.md", bad)
        assert e.value.status == 404
    with pytest.raises(LibraryError):  # a version of one note cannot be read as another's
        lb.version("shared/people.md", versions[0]["version"])


# --- remember, forget, edit ------------------------------------------------------


def test_remember_puts_a_fact_in_the_note_for_its_kind_with_its_metadata(core, lib):
    fact = memory.remember(core, "Prefers meetings after 10 a.m.", kind="preference",
                           actor="agent:miner", confidence=0.7,
                           evidence=["tape:01JABCDEFGHJKMNPQRSTVWXYZ1", "chat:42"])
    assert fact.created and fact.note == "shared/preferences.md"
    assert (fact.kind, fact.confidence, fact.confirmed, fact.saved_by) == (
        "preference", 0.7, False, "agent:miner")
    assert fact.evidence == ("tape:01JABCDEFGHJKMNPQRSTVWXYZ1", "chat:42")
    text = (lib / "shared/preferences.md").read_text()
    assert text.endswith(f"\n\n- Prefers meetings after 10 a.m. <!-- m:{fact.id} -->\n")
    second = memory.remember(core, "Likes short emails", kind="preference", actor="owner")
    assert (lib / "shared/preferences.md").read_text().endswith(
        f"<!-- m:{fact.id} -->\n- Likes short emails <!-- m:{second.id} -->\n")
    where = {k: memory.remember(core, f"A {k} thing to keep", kind=k, actor="owner").note
             for k in ("person", "project", "decision", "rule", "hobby")}
    assert where == {"person": "shared/people.md", "project": "shared/projects.md",
                     "decision": "shared/decisions.md", "rule": "shared/rules.md",
                     "hobby": "shared/about-me.md"}
    saved = events(core, "memory.fact.saved")[0]
    assert saved.actor == "agent:miner" and saved.subject == f"memory_fact:{fact.id}"
    assert saved.payload["evidence"] == ["tape:01JABCDEFGHJKMNPQRSTVWXYZ1", "chat:42"]


def test_remember_follows_the_kind_to_note_setting_and_can_name_a_new_note(core, lib):
    core.settings.set("memory.kind_notes", ["hobby=shared/hobbies.md"], actor="owner")
    core.settings.set("memory.default_note", "shared/misc.md", actor="owner")
    assert memory.remember(core, "Plays chess", kind="hobby", actor="owner").note == (
        "shared/hobbies.md")
    assert (lib / "shared/hobbies.md").read_text().startswith("# Hobbies\n\n- Plays chess")
    assert memory.remember(core, "Is tall", kind="person", actor="owner").note == (
        "shared/misc.md")
    named = memory.remember(core, "Scout found a bug", kind="finding", actor="agent:scout",
                            note="agents/scout/findings.md")
    assert named.note == "agents/scout/findings.md"
    from jimmyhq.settings import SettingError
    for bad in (["no equals sign"], ["Bad Kind=shared/x.md"], ["a=../x.md"],
                ["a=shared/x.md", "a=shared/y.md"]):
        with pytest.raises(SettingError):
            core.settings.set("memory.kind_notes", bad, actor="owner")
    with pytest.raises(SettingError):
        core.settings.set("memory.default_note", "/etc/x.md", actor="owner")


@pytest.mark.parametrize("again", [
    "Prefers meetings after 10 a.m.", "prefers meetings after 10 AM",
    "  Prefers meetings, after 10 a.m!  ", "Prefers meetings after 10 a.m. please",
    "Prefers meetings after 10am",
])
def test_remember_refuses_a_fact_that_is_already_there_or_nearly(core, lib, again):
    core.settings.set("memory.default_note", "shared/misc.md", actor="owner")
    first = memory.remember(core, "Prefers meetings after 10 a.m. on weekdays in the office",
                            kind="preference", actor="owner")
    long_again = again.rstrip() + " on weekdays in the office" if "please" not in again else (
        "Prefers meetings after 10 a.m. on weekdays in the office please")
    before = (lib / "shared/preferences.md").read_bytes()
    got = memory.remember(core, long_again, kind="other", actor="agent:miner")
    assert got.id == first.id and got.created is False
    assert (lib / "shared/preferences.md").read_bytes() == before
    assert not (lib / "shared/misc.md").exists()
    assert len(events(core, "memory.fact.saved")) == 1


@pytest.mark.parametrize("different", [
    "Prefers meetings after 11 a.m. on weekdays in the office",
    "Prefers calls after 10 a.m. on weekends at home",
    "Dislikes meetings", "Prefers meetings after 10 a.m. on weekdays in the orchard",
])
def test_remember_keeps_a_fact_that_only_looks_similar(core, lib, different):
    first = memory.remember(core, "Prefers meetings after 10 a.m. on weekdays in the office",
                            kind="preference", actor="owner")
    got = memory.remember(core, different, kind="preference", actor="owner")
    assert got.created and got.id != first.id


def test_a_retired_fact_does_not_block_remembering_it_again(core, lib):
    first = memory.remember(core, "Owns a red bicycle", kind="about", actor="owner")
    memory.forget(core, first.id, actor="owner", reason="Sold it.")
    again = memory.remember(core, "Owns a red bicycle", kind="about", actor="owner")
    assert again.created and again.id != first.id


@pytest.mark.parametrize("bad", [
    {"text": ""}, {"text": "   "}, {"text": "two\nlines"}, {"text": "x" * 2001},
    {"text": "has <!-- a comment --> in it"}, {"text": "..."}, {"text": 5},
    {"text": "bell\x07"}, {"kind": "Bad Kind"}, {"kind": ""}, {"kind": None},
    {"confidence": 1.5}, {"confidence": -0.1}, {"confidence": float("nan")},
    {"confidence": "high"}, {"confidence": True}, {"evidence": "tape:1"},
    {"evidence": ["has space"]}, {"evidence": ["x"] * 21}, {"evidence": [5]},
    {"confirmed": "yes"}, {"note": "../x.md"}, {"note": "shared/x.txt"}, {"actor": ""},
])
def test_remember_refuses_what_it_cannot_store_with_a_readable_reason(core, lib, bad):
    args = {"text": "A fine fact", "kind": "about", "actor": "owner"} | bad
    text = args.pop("text")
    with pytest.raises(LibraryError) as e:
        memory.remember(core, text, **args)
    assert e.value.status == 422 and e.value.detail.endswith(".")
    assert events(core, "memory.fact.saved") == []


def test_forget_removes_the_line_and_what_is_nested_under_it(core, lib):
    path = write(lib, "shared/notes.md", "# N\r\n\r\n- Keep this\r\n- Drop this\r\n"
                                         "  - and its detail\r\n- Keep that\r\n")
    memory.scan(core)
    target = facts(core)["Drop this"]["id"]
    gone = memory.forget(core, target, actor="agent:miner", reason="  Was   wrong. ")
    assert gone.retired_ms and gone.retired_reason == "Was wrong."
    text = path.read_bytes().decode()
    assert "Drop this" not in text and "and its detail" not in text
    assert re.fullmatch(rf"# N\r\n\r\n- Keep this {ID}\r\n- Keep that {ID}\r\n", text)
    retired = events(core, "memory.fact.retired")[-1]
    assert retired.actor == "agent:miner" and retired.payload["reason"] == "Was wrong."
    for missing in (target, "01JABCDEFGHJKMNPQRSTVWXYZ0", "nope", None):
        with pytest.raises(LibraryError) as e:
            memory.forget(core, missing, actor="owner", reason=None)
        assert e.value.status == 404


def test_editing_a_fact_rewrites_only_its_line_and_records_what_changed(core, lib):
    path = write(lib, "shared/notes.md", "- Alpha  \n  - detail\n- Beta\n")
    memory.scan(core)
    alpha = facts(core)["Alpha"]
    edited = memory.edit_fact(core, alpha["id"], actor="owner", text="Alpha, improved",
                              kind="project", confirmed=False)
    assert (edited.text, edited.kind, edited.confirmed) == ("Alpha, improved", "project", False)
    assert path.read_text().startswith(
        f"- Alpha, improved <!-- m:{alpha['id']} -->  \n  - detail\n- Beta")
    changed = [e.payload["changed"] for e in events(core, "memory.fact.edited")]
    assert changed == [["text"], ["kind", "confirmed"]]
    same = memory.edit_fact(core, alpha["id"], actor="owner", kind="project")
    assert same.updated_ms == edited.updated_ms
    assert len(events(core, "memory.fact.edited")) == 2
    for bad in ({"text": "two\nlines"}, {"kind": "NOPE"}, {"confirmed": "yes"}):
        with pytest.raises(LibraryError) as e:
            memory.edit_fact(core, alpha["id"], actor="owner", **bad)
        assert e.value.status == 422


def test_facts_can_be_listed_filtered_and_paged(core, lib):
    for i in range(7):
        memory.remember(core, f"Project item number {i} is unique", kind="project",
                        actor="agent:miner")
    memory.remember(core, "Likes 100% cotton_shirts", kind="preference", actor="owner",
                    confirmed=True)
    page = memory.list_facts(core, kind="project", limit=3)
    assert len(page["facts"]) == 3 and page["next_before"]
    seen = [f["id"] for f in page["facts"]]
    while page["next_before"]:
        page = memory.list_facts(core, kind="project", limit=3, before=page["next_before"])
        seen += [f["id"] for f in page["facts"]]
    assert len(seen) == len(set(seen)) == 7
    assert [f["text"] for f in memory.list_facts(core, confirmed=True)["facts"]] == [
        "Likes 100% cotton_shirts"]
    assert len(memory.list_facts(core, q="100% cotton_")["facts"]) == 1
    assert memory.list_facts(core, q="100%%")["facts"] == []
    assert len(memory.list_facts(core, q="NUMBER 3")["facts"]) == 1
    with pytest.raises(LibraryError):
        memory.list_facts(core, before="garbage")


# --- two processes ---------------------------------------------------------------


def test_two_processes_saving_and_scanning_at_once_lose_nothing(cfg, core, lib):
    other = open_core(cfg)
    try:
        errors: list[BaseException] = []

        def work(c, name):
            try:
                for i in range(12):
                    memory.remember(c, f"{name} remembers item {i} of the list", kind=name,
                                    actor=f"agent:{name}", note="shared/both.md")
                    memory.save_note(c, f"shared/{name}-{i}.md", f"- {name} note {i}\n",
                                     actor="owner")
                    memory.scan(c)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(c, n))
                   for c, n in ((core, "alpha"), (other, "beta"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert errors == []
        text = (lib / "shared/both.md").read_text()
        ids = re.findall(r"<!-- m:(\w{26}) -->", text)
        assert len(ids) == len(set(ids)) == 24
        for name in ("alpha", "beta"):
            for i in range(12):
                assert f"- {name} remembers item {i} of the list <!--" in text
        # Both see the same record, and the Tape has each fact exactly once.
        for c in (core, other):
            assert memory.status(c)["counts"]["facts"] == 48
            assert len(memory.list_facts(c, note="shared/both.md", limit=200)["facts"]) == 24
        saved = [e.payload["fact_id"] for e in events(core, "memory.fact.saved")]
        assert len(saved) == len(set(saved)) == 48
        assert memory.scan(core)["changed"] == memory.scan(other)["changed"] == 0
    finally:
        other.close()


def test_a_save_that_races_another_writer_past_the_lock_is_refused_not_lost(core, lib):
    memory.save_note(core, "shared/race.md", "one\n", actor="owner")
    lb = memory.library(core)
    real = lb._sync

    def racing(**kwargs):
        if kwargs.get("guard"):
            # Someone else records a change to the same note at this very moment.
            core.tape.print("memory.note.saved", {
                "note": "shared/race.md", "ref": "sha256:" + "0" * 64, "size": 1, "facts": 0},
                actor="token:other", subject="memory_note:shared/race.md")
        return real(**kwargs)

    lb._sync = racing
    with pytest.raises(LibraryError, match="at the same moment") as e:
        memory.save_note(core, "shared/race.md", "two\n", actor="owner")
    assert e.value.status == 409
    lb._sync = real
    # The file wins: the next scan records what is really there.
    memory.scan(core)
    assert memory.read_note(core, "shared/race.md")["text"] == "two\n"
    assert events(core, "memory.note.saved")[-1].actor == "owner:disk"


# --- privacy and metadata that survives ------------------------------------------


def test_events_and_logs_never_carry_note_or_fact_text(core, lib, caplog):
    with caplog.at_level("DEBUG"):
        memory.save_note(core, "shared/private.md",
                         "# Diary\n\nThe violet umbrella is hidden.\n\n- Fears the marmalade\n",
                         actor="owner")
        fact = memory.remember(core, "Keeps the zeppelin ticket", kind="about", actor="owner")
        memory.edit_fact(core, fact.id, actor="owner", text="Keeps the dirigible ticket")
        write(lib, "shared/byhand.md", "- Whispers about the pangolin\n")
        memory.scan(core)
        memory.forget(core, fact.id, actor="owner", reason="Done.")
        memory.reindex(core)
    recorded = tape_text(core)
    views = (core.config.data / "views.db").read_bytes()
    for word in ("violet", "umbrella", "marmalade", "zeppelin", "dirigible", "pangolin"):
        assert word not in recorded, word
        assert word not in caplog.text, word
        assert word.encode() not in views, word
    assert "shared/private.md" in recorded  # paths are how history is found


def test_what_is_known_about_a_fact_survives_losing_the_database(cfg, core, lib):
    fact = memory.remember(core, "Allergic to walnuts", kind="health", actor="agent:miner",
                           confidence=0.4, evidence=["chat:7"])
    memory.edit_fact(core, fact.id, actor="owner", confirmed=True)
    memory.library(core).close()
    core._services.pop("memory")
    for name in ("memory.db", "memory.db-wal", "memory.db-shm"):
        (cfg.data / name).unlink(missing_ok=True)
    memory.scan(core)
    back = memory.get_fact(core, fact.id)
    assert (back.text, back.kind, back.confidence, back.confirmed, back.saved_by) == (
        "Allergic to walnuts", "health", 0.4, True, "agent:miner")
    assert back.evidence == ("chat:7",)


# --- scale -----------------------------------------------------------------------


def test_a_library_of_a_few_thousand_notes_scans_and_searches_quickly(core, lib):
    topics = ["garden", "kitchen", "bicycle", "piano", "harbour", "library", "mountain"]
    for i in range(3000):
        topic = topics[i % len(topics)]
        write(lib, f"shared/bulk/{i % 30:02d}/note-{i}.md",
              f"# Note {i}\n\nSome words about the {topic}, entry {i}.\n\n"
              f"- The {topic} fact number {i}\n- Another line for note {i}\n")
    write(lib, "shared/bulk/needle.md", "- The xylophone is kept in the attic\n")
    start = time.monotonic()
    first = memory.scan(core)
    first_s = time.monotonic() - start
    assert (first["added"], first["facts_added"], first["ids_written"]) == (3001, 6001, 3001)
    old = time.time() - 60
    for path in (lib / "shared/bulk").rglob("*.md"):
        os.utime(path, (old, old))
    memory.scan(core)
    start = time.monotonic()
    again = memory.scan(core)
    again_s = time.monotonic() - start
    assert again["changed"] == 0 and again["notes"] == 3007
    start = time.monotonic()
    found = memory.recall(core, "where is the xylophone", actor="owner", caller="test.scale")
    search_s = time.monotonic() - start
    assert found.passages[0].text == "The xylophone is kept in the attic"
    print(f"\nscale: first scan {first_s:.2f}s, rescan {again_s:.3f}s, search {search_s:.3f}s")
    # Generous limits: slow CI machines must pass, a quadratic mistake must not.
    # Writing three thousand small files is several times slower on Windows runners
    # (about 90 seconds has been seen), so the first scan gets more room there.
    first_limit = 240 if sys.platform == "win32" else 60
    assert first_s < first_limit and again_s < 5 and search_s < 2


# --- what a second look found -----------------------------------------------------


@pytest.mark.parametrize("text", [
    # YAML front matter: a list in it is another tool's data.
    "---\ntitle: Plans\ntags:\n- alpha\n- beta\n---\n\n- A real fact\n",
    "---\ntags:\n- alpha\n...\n- A real fact\n",
    # An HTML comment over several lines: an id inside it would end it early.
    "<!--\n- commented out\n- and this\n-->\n- A real fact\n",
    "Text <!-- starts here\n- still hidden\n--> shown again\n\n- A real fact\n",
    "- A real fact\n- half hidden <!-- opens and\n- stays hidden\n-->\n",
    # A fence of four is not closed by three, nor a fence by a line naming a language.
    "````\n```\n- code\n```\n- code still\n````\n- A real fact\n",
    "```\ncode\n```python\n- code still\n```\n- A real fact\n",
    "~~~\n```\n- code\n~~~\n- A real fact\n",
])
def test_a_list_in_front_matter_a_comment_or_a_code_block_is_never_given_an_id(core, lib, text):
    path = write(lib, "shared/mixed.md", text)
    memory.scan(core)
    now = path.read_text()
    assert [f.text for f in md.parse(now).facts] == ["A real fact"]
    assert len(re.findall(ID, now)) == 1
    assert re.sub(" " + ID, "", now) == text  # nothing but the one id was added
    assert list(facts(core, "shared/mixed.md")) == ["A real fact"]


@pytest.mark.parametrize("text", [
    "---\n- First fact\n\nSome words: and more.\n\n---\n- Second fact\n",
    "---\n- First fact\n---\n- Second fact\n",
    "---\ntitle: never closed\n- First fact\n- Second fact\n",
    "---\n\nA paragraph.\n\n- First fact\n\n---\n\n- Second fact\n",
])
def test_a_note_that_only_begins_with_a_rule_keeps_the_facts_under_it(text):
    assert [f.text for f in md.parse(text).facts] == ["First fact", "Second fact"]


def test_front_matter_and_code_are_still_searched_but_a_yaml_comment_is_not_a_heading():
    parsed = md.parse("---\ntitle: Plans\n# not a heading\ntags:\n- alpha\n---\n\n"
                      "# Real\n\nBody text.\n")
    found = md.chunks(parsed, 800)
    assert [(c.heading, c.text) for c in found] == [
        ("", "---\ntitle: Plans\n# not a heading\ntags:\n- alpha\n---"),
        ("Real", "Body text.")]


def test_a_name_that_differs_only_in_how_an_accent_is_stored_is_one_note(core, lib):
    import unicodedata
    composed = unicodedata.normalize("NFC", "shared/caf\u00e9.md")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    path = write(lib, decomposed, "- Likes the corner table\n")
    memory.scan(core)
    on_disk = next(n for n in os.listdir(lib / "shared") if n.startswith("caf"))
    stored = f"shared/{on_disk}"  # whichever form this disk kept
    fact_id = facts(core, stored)["Likes the corner table"]["id"]
    other = composed if stored == decomposed else decomposed
    for attempt in (lambda: memory.read_note(core, other),
                    lambda: memory.save_note(core, other, "- Replaced\n", actor="owner"),
                    lambda: memory.remember(core, "Something new", kind="about", actor="owner",
                                            note=other)):
        with pytest.raises(LibraryError) as e:
            attempt()
        assert e.value.status == 409 and "Use that spelling" in e.value.detail
    memory.scan(core)
    paths = [n["path"] for n in memory.library(core).tree()["notes"] if "caf" in n["path"]]
    assert paths == [stored]
    assert memory.get_fact(core, fact_id).note == stored
    assert fact_id in path.read_text()
    assert len(events(core, "memory.fact.saved")) == 1


def test_the_background_scan_leaves_a_note_that_is_still_being_written_alone(core, lib):
    path = settle(write(lib, "shared/draft.md", "- Already known\n"))
    memory.scan(core)
    known = facts(core, "shared/draft.md")["Already known"]["id"]
    # A program has written the first half and has the file open for the rest.
    half = path.read_text() + "- First half of a save\n"
    path.write_bytes(half.encode("utf-8"))  # bytes: no line-ending translation on Windows
    result = memory.scan(core, settle=True)
    assert result["deferred"] == 1 and result["ids_written"] == 0
    assert path.read_text() == half  # not replaced under the program writing it
    assert memory.get_fact(core, known).retired_ms is None  # and nothing was retired
    assert events(core, "memory.fact.retired") == []
    assert memory.read_note(core, "shared/draft.md")["text"] == half  # reading leaves it too
    assert path.read_text() == half
    # Once it has been quiet for a moment, the next scan gives the new line its id.
    settle(path)
    assert memory.scan(core, settle=True)["ids_written"] == 1
    assert set(facts(core, "shared/draft.md")) == {"Already known", "First half of a save"}
    assert memory.get_fact(core, known).note == "shared/draft.md"


def test_a_note_whose_ids_cannot_be_written_keeps_its_facts_and_says_why(core, lib, monkeypatch):
    path = write(lib, "shared/locked.md", "- Known before\n")
    memory.scan(core)
    known = facts(core, "shared/locked.md")["Known before"]["id"]
    path.write_text(path.read_text() + "- Typed later\n")
    library_module = importlib.import_module("jimmyhq.memory.library")

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(library_module, "atomic_write", refuse)
    result = memory.scan(core)
    assert result["deferred"] == 1 and result["facts_retired"] == 0
    assert memory.get_fact(core, known).retired_ms is None
    skipped = {s["path"]: s["reason"] for s in memory.status(core)["skipped"]}
    assert "cannot be written" in skipped["shared/locked.md"]
    assert "permissions" in skipped["shared/locked.md"]
    monkeypatch.undo()
    assert memory.scan(core)["ids_written"] == 1
    assert set(facts(core, "shared/locked.md")) == {"Known before", "Typed later"}
    assert memory.status(core)["skipped"] == []


def test_a_note_with_a_name_as_long_as_the_disk_allows_is_still_given_ids(core, lib):
    name = "\U0001F600" * 58 + ".md"  # 235 bytes: no room for a temp file named after it
    try:
        path = write(lib, f"shared/{name}", "- Has a long name\n")
    except OSError:
        pytest.skip("this disk cannot hold a name that long")
    result = memory.scan(core)
    assert result["deferred"] == 0 and result["ids_written"] == 1
    assert re.search(ID, path.read_text())
    assert facts(core, f"shared/{name}")["Has a long name"]
    with pytest.raises(LibraryError) as e:  # and one that could not be stored is refused early
        clean_path("shared/" + "\U0001F600" * 100 + ".md")
    assert e.value.status == 422 and "too long" in e.value.detail


def test_a_temp_file_left_by_a_crash_is_cleared_away_and_nobody_elses_is_touched(core, lib):
    ours_old = settle(write(lib, "shared/.01JABCDEFGHJKMNPQRSTVWXYZ0.tmp", "half a note"))
    old = time.time() - 7200
    os.utime(ours_old, (old, old))
    ours_new = write(lib, "shared/.01JABCDEFGHJKMNPQRSTVWXYZ1.tmp", "being written now")
    theirs = write(lib, "shared/.draft.tmp", "an editor's own file")
    os.utime(theirs, (old, old))
    memory.scan(core)
    assert not ours_old.exists() and ours_new.exists() and theirs.exists()
    memory.save_note(core, "shared/tidy.md", "- Saved cleanly\n", actor="owner")
    assert sorted(p.name for p in (lib / "shared").iterdir() if p.name.endswith(".tmp")) == [
        ".01JABCDEFGHJKMNPQRSTVWXYZ1.tmp", ".draft.tmp"]


def test_a_note_that_vanishes_under_a_change_is_a_plain_refusal_not_a_crash(
        core, lib, monkeypatch):
    fact = memory.remember(core, "Here one moment", kind="about", actor="owner")
    memory.save_note(core, "shared/gone.md", "one\n", actor="owner")
    from pathlib import Path
    real = Path.read_bytes

    def vanishing(self):
        if self.name in ("about-me.md", "gone.md") and self.parent.name == "shared":
            raise FileNotFoundError(2, "No such file or directory")
        return real(self)

    lb = memory.library(core)
    real_sync = lb._sync
    lb._sync = lambda **kw: {}  # the scan before the change saw the file; then it went
    monkeypatch.setattr(Path, "read_bytes", vanishing)
    for attempt in (lambda: memory.forget(core, fact.id, actor="owner"),
                    lambda: memory.edit_fact(core, fact.id, actor="owner", text="Changed"),
                    lambda: memory.remember(core, "Another one", kind="about", actor="owner"),
                    lambda: memory.save_note(core, "shared/gone.md", "two\n", actor="owner")):
        with pytest.raises(LibraryError) as e:
            attempt()
        assert e.value.status == 409 and "try again" in e.value.detail
    lb._sync = real_sync


@pytest.mark.parametrize("pair", [
    ("The weekly review with the design team about the new kiosk layout is every Monday",
     "The weekly review with the design team about the new kiosk layout is every Tuesday"),
    ("Always send the supplier invoices to the accounts team before the monthly close",
     "Never send the supplier invoices to the accounts team before the monthly close"),
])
def test_a_long_fact_with_one_word_changed_is_a_different_fact(core, lib, pair):
    one = memory.remember(core, pair[0], kind="about", actor="owner")
    two = memory.remember(core, pair[1], kind="about", actor="agent:miner")
    assert two.created and two.id != one.id
    core.settings.set("memory.keyword_floor", 0, actor="owner")
    found = memory.recall(core, pair[0], actor="owner", caller="test.recall", k=10)
    assert {one.id, two.id} <= {p.fact_id for p in found.passages}
    # Adding a word or two to a long statement is still the same statement.
    again = memory.remember(core, pair[0] + " please", kind="about", actor="agent:miner")
    assert again.created is False and again.id == one.id


def test_everything_is_rebuilt_from_the_files_and_the_tape_when_the_database_is_lost(
        cfg, core, lib):
    memory.save_note(core, "shared/trip.md", "# Trip\n\nThe ferry leaves at nine.\n\n"
                                              "- Pack the green raincoat\n", actor="owner")
    fact = memory.remember(core, "Window seat when possible", kind="preference",
                           actor="agent:miner", confidence=0.6, evidence=["chat:9"])
    write(lib, "shared/byhand.md", "- Typed by hand before the loss\n")
    memory.scan(core)
    def known():
        found = memory.list_facts(core, limit=200)["facts"]
        return ({f["id"]: (f["text"], f["note"], f["kind"], f["confidence"], f["confirmed"],
                           f["saved_by"], f["evidence"]) for f in found},
                {f["id"]: f["created_ms"] for f in found})

    before, made = known()
    tree = [(n["path"], n["size"], n["facts"]) for n in memory.library(core).tree()["notes"]]
    files = {p: p.read_bytes() for p in lib.rglob("*.md")}
    memory.library(core).close()
    core._services.pop("memory")
    for name in ("memory.db", "memory.db-wal", "memory.db-shm"):
        (cfg.data / name).unlink(missing_ok=True)
    memory.scan(core)
    after, made_again = known()
    assert after == before and fact.id in after
    # When each was first saved comes back from its first event, to within a moment.
    assert all(abs(made_again[i] - made[i]) < 1000 for i in made)
    assert [(n["path"], n["size"], n["facts"])
            for n in memory.library(core).tree()["notes"]] == tree
    assert {p: p.read_bytes() for p in lib.rglob("*.md")} == files  # no file was touched
    found = memory.recall(core, "when does the ferry leave", actor="owner", caller="test.x")
    assert found.passages[0].text == "The ferry leaves at nine."

