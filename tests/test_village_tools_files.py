"""The file tools. They are a security boundary: an agent reads and writes inside its own
folder and the shared folders it is allowed, and nowhere else, whatever the path looks
like."""

from __future__ import annotations

import os

import pytest

from jimmyhq.village import files
from jimmyhq.village.files import Files, FilesError
from test_village_tools_fakes import call, refused, v  # noqa: F401

H = "home"  # the folder an agent's files live under


@pytest.fixture
def box(tmp_path):
    """One agent's folder, a shared folder, and a secret next to both."""
    (tmp_path / "secret.txt").write_text("the owner's secret", encoding="utf-8")
    other = tmp_path / "agents" / "bo" / "home" / "files"
    other.mkdir(parents=True)
    (other / "private.md").write_text("bo's private notes", encoding="utf-8")
    shared = tmp_path / "shared"
    (shared / "bo").mkdir(parents=True)
    (shared / "bo" / "draft.md").write_text("bo's draft", encoding="utf-8")
    (shared / "left" / "cy").mkdir(parents=True)
    (shared / "left" / "cy" / "handoff.md").write_text("what cy left", encoding="utf-8")
    return Files(tmp_path / "agents" / "ada" / "home" / "files", shared=shared, me="ada")


def link(target, at) -> None:
    """A symbolic link, or a skipped test where this system will not make one."""
    try:
        os.symlink(target, at)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow symbolic links here")


def rule_of(fn, *args, **kwargs) -> FilesError:
    with pytest.raises(FilesError) as e:
        fn(*args, **kwargs)
    return e.value


# --- confinement -------------------------------------------------------------------------

ESCAPES = [
    "../secret.txt", "../../secret.txt", "notes/../../../secret.txt", "..", "notes/..",
    "..\\..\\secret.txt", "notes\\..\\..\\secret.txt", "./../secret.txt",
    "shared/../../secret.txt", "shared/bo/../../../secret.txt", "shared/ada/../bo/draft.md",
    f"../bo/{H}/files/private.md", f"../../bo/{H}/files/private.md",
]


@pytest.mark.parametrize("path", ESCAPES)
def test_a_path_with_dot_dot_never_reads_or_writes_anything(box, path):
    for act in (lambda: box.read(path), lambda: box.write(path, "x"),
                lambda: box.append(path, "x"), lambda: box.edit(path, "a", "b"),
                lambda: box.list(path)):
        e = rule_of(act)
        assert e.rule == "files_confined"
        assert e.sentence.startswith("Nothing happened")
    assert (box.root.parents[3] / "secret.txt").read_text(encoding="utf-8") == "the owner's secret"


WINDOWS = ["C:\\Windows\\win.ini", "C:/Windows/win.ini", "c:secret.txt", "D:",
           "\\\\server\\share\\file.txt", "//server/share/file.txt", "\\\\?\\C:\\secret.txt",
           "\\\\.\\pipe\\name"]


@pytest.mark.parametrize("path", WINDOWS)
def test_a_drive_letter_or_network_path_is_refused_before_the_disk_is_touched(box, path):
    assert rule_of(box.read, path).rule == "files_confined"
    assert rule_of(box.write, path, "x").rule == "files_confined"
    assert "drive or another computer" in rule_of(files.clean, path).sentence


def test_an_absolute_path_is_read_as_a_path_inside_the_agents_own_folder(box):
    assert box.write("/etc/passwd", "mine") == "created etc/passwd (4 characters)"
    assert (box.root / "etc" / "passwd").read_text(encoding="utf-8") == "mine"
    assert box.read("/etc/passwd") == "mine"
    assert box.path("/etc/passwd") == box.root / "etc" / "passwd"


def test_a_home_shortcut_stays_inside_the_folder(box):
    box.write("~/notes.md", "n")
    assert (box.root / "notes.md").is_file()
    assert rule_of(box.read, "~/../secret.txt").rule == "files_confined"


def test_a_link_that_points_outside_cannot_be_read_written_or_listed(box, tmp_path):
    outside = tmp_path / "secret.txt"
    link(outside, box.root / "link.md")
    link(tmp_path, box.root / "up")
    for path in ("link.md", "up/secret.txt"):
        assert rule_of(box.read, path).rule == "files_confined"
        assert rule_of(box.write, path, "pwned", True).rule == "files_confined"
        assert rule_of(box.append, path, "pwned").rule == "files_confined"
        assert rule_of(box.edit, path, "secret", "pwned").rule == "files_confined"
    assert rule_of(box.list, "up").rule == "files_confined"
    assert outside.read_text(encoding="utf-8") == "the owner's secret"
    # and a link is not shown in a listing at all
    box.write("real.md", "x")
    assert box.list("") .splitlines()[0] == "real.md (1 bytes)"
    assert "link.md" not in box.list("") and "up/" not in box.list("")


def test_a_link_to_a_file_that_does_not_exist_yet_cannot_be_written_through(box, tmp_path):
    link(tmp_path / "new-outside.txt", box.root / "later.md")
    assert rule_of(box.write, "later.md", "x").rule == "files_confined"
    assert not (tmp_path / "new-outside.txt").exists()


def test_a_link_in_the_agents_shared_folder_cannot_write_into_a_coworkers(box):
    mine = box.shared / "ada"
    mine.mkdir()
    link(box.shared / "bo" / "draft.md", mine / "theirs.md")
    link(box.shared / "bo", mine / "bo")
    assert rule_of(box.write, "shared/ada/theirs.md", "x", True).rule == "shared_write_own_only"
    assert rule_of(box.write, "shared/ada/bo/new.md", "x").rule == "shared_write_own_only"
    assert (box.shared / "bo" / "draft.md").read_text(encoding="utf-8") == "bo's draft"


def test_a_link_in_a_shared_folder_cannot_read_a_coworkers_private_files(box, tmp_path):
    private = tmp_path / "agents" / "bo" / "home" / "files" / "private.md"
    link(private, box.shared / "bo" / "peek.md")
    assert rule_of(box.read, "shared/bo/peek.md").rule == "files_confined"


CASE = ["SHARED/bo/draft.md", "Shared/BO/new.md", "shared/Bo/new.md", "shared/LEFT/cy/x.md",
        "Shared/Left/cy/handoff.md"]


@pytest.mark.parametrize("path", CASE)
def test_odd_case_does_not_open_a_coworkers_folder_for_writing(box, path):
    assert rule_of(box.write, path, "x", True).rule == "shared_write_own_only"
    assert (box.shared / "bo" / "draft.md").read_text(encoding="utf-8") == "bo's draft"
    assert (box.shared / "left" / "cy" / "handoff.md").read_text(encoding="utf-8") == \
        "what cy left"


def test_the_shared_place_is_reached_whatever_case_its_name_is_written_in(box):
    assert box.read("SHARED/bo/draft.md") == "bo's draft"
    assert box.read("Shared/LEFT/cy/handoff.md") == "what cy left"
    box.write("Shared/ADA/mine.md", "x")
    assert (box.shared / "ada" / "mine.md").is_file()
    assert not (box.root / "Shared").exists() and not (box.root / "shared").exists()


def test_a_very_long_name_or_path_is_refused_with_its_size(box):
    e = rule_of(box.write, "a" * 300 + ".md", "x")
    assert e.rule == "path_too_long" and "303 characters and a name takes 120" in e.sentence
    e = rule_of(box.read, "/".join(["folder"] * 80))
    assert e.rule == "path_too_long" and "a path takes 400" in e.sentence
    e = rule_of(box.write, "x" * 5000, "x")
    assert e.rule == "path_too_long" and "5000 characters" in e.sentence
    assert list(box.root.iterdir()) == []


NAMES = ["a\x00b.md", "notes/a\nb.md", "con", "NUL.txt", "notes/com1.md", "a:b.md",
         "file.txt:stream", "what?.md", "star*.md", "a|b", "trailing.", "trailing ./x.md",
         "<x>.md", 'quote".md']


@pytest.mark.parametrize("path", NAMES)
def test_a_name_that_means_something_else_on_another_system_is_refused(box, path):
    e = rule_of(box.write, path, "x")
    assert e.rule == "files_confined" and e.sentence.startswith("Nothing happened")


@pytest.mark.parametrize("path", [".env", "notes/.hidden.md", ".git/config", "shared/bo/.x"])
def test_hidden_files_are_not_available(box, path):
    assert rule_of(box.read, path).rule == "hidden_files"
    assert rule_of(box.write, path, "x").rule == "hidden_files"


def test_a_path_that_is_not_text_is_refused(box):
    assert rule_of(box.read, 12).rule == "files_confined"
    assert rule_of(box.write, ["a"], "x").rule == "files_confined"


def test_the_top_folder_or_a_folder_is_not_a_file_to_write(box):
    box.write("notes/a.md", "x")
    assert rule_of(box.write, "", "x").rule == "file_name_needed"
    assert rule_of(box.write, "notes", "x").rule == "file_name_needed"
    assert rule_of(box.append, "/", "x").rule == "file_name_needed"


# --- shared folders ------------------------------------------------------------------------


def test_an_agent_reads_every_shared_folder_and_writes_only_its_own(box):
    assert box.read("shared/bo/draft.md") == "bo's draft"
    assert box.read("shared/left/cy/handoff.md") == "what cy left"
    assert box.write("shared/ada/review.md", "ok") == "created shared/ada/review.md (2 characters)"
    assert (box.shared / "ada" / "review.md").read_text(encoding="utf-8") == "ok"
    assert box.append("shared/ada/review.md", "!") == "added 1 characters to shared/ada/review.md"

    e = rule_of(box.write, "shared/bo/draft.md", "mine now", True)
    assert e.rule == "shared_write_own_only"
    assert e.sentence == ("Nothing was written: shared/bo/ belongs to a coworker. You can read "
                          "it, and only they can write there. Your own shared folder is "
                          "shared/ada/: write there.")
    e = rule_of(box.edit, "shared/left/cy/handoff.md", "cy", "ada")
    assert e.rule == "shared_write_own_only" and "read-only" in e.sentence
    assert rule_of(box.append, "shared/new.md", "x").rule == "shared_write_own_only"
    assert rule_of(box.write, "shared/ada", "x").rule == "file_name_needed"
    assert rule_of(box.write, "shared", "x").rule == "shared_write_own_only"


def test_the_shared_listing_says_whose_each_folder_is(box):
    assert box.list("shared") == (
        "ada/ (yours: you can write here; your coworkers can read it)\n"
        "bo/ (a coworker's: read-only for you)\n"
        "left/ (read-only: work left by agents who have gone)")
    assert box.list("shared/ada") == "(empty)"
    assert box.list("shared/left") == "cy/"
    assert box.list("shared/bo") == "draft.md (10 bytes)"
    assert box.list("").endswith("work left by agents who have gone)")


def test_a_missing_shared_folder_names_the_folders_there(box):
    e = rule_of(box.list, "shared/nobody")
    assert e.rule == "folder_missing"
    assert "The 3 folders in shared/ are: ada/, bo/, left/." in e.sentence


# --- list and read ---------------------------------------------------------------------------


def test_a_missing_folder_names_the_folders_that_are_there(box):
    box.write("notes/a.md", "x")
    box.write("research/deep/b.md", "x")
    e = rule_of(box.list, "drafts")
    assert e.rule == "folder_missing"
    assert e.sentence == ("Nothing was listed: there is no folder drafts. The 3 folders in your "
                          "top folder are: notes/, research/, shared/. List one of those, or "
                          "your top folder with an empty path.")
    e = rule_of(box.list, "research/nope/deeper")
    assert "The 1 folders in research/ are: deep/." in e.sentence
    assert rule_of(box.list, "notes/a.md").rule == "not_a_folder"


def test_a_listing_shows_folders_first_then_files_with_their_sizes(box):
    box.write("b.md", "12345")
    box.write("a/x.md", "1")
    assert box.list("").splitlines()[:2] == ["a/", "b.md (5 bytes)"]
    assert Files(box.root / "empty").list("") == "(empty)"


def test_reading_a_folder_returns_its_listing(box):
    box.write("notes/a.md", "x")
    assert box.read("notes") == "notes is a folder, not a file. What's in it:\na.md (1 bytes)"


def test_a_missing_file_says_so_and_offers_similar_names(box):
    box.write("drafts/welcome-email-series.md", "x")
    e = rule_of(box.read, "deliverables/welcome-email.md")
    assert e.rule == "file_missing"
    assert e.sentence == ("Nothing was read: there is no file deliverables/welcome-email.md. "
                          "Files with similar names: drafts/welcome-email-series.md. Use "
                          "list_files to see what is in the folder.")


def test_start_returns_thirty_thousand_characters_and_the_next_start(box):
    box.write("long.md", "a" * 70_000)
    part = box.read("long.md", 0)
    head, body = part.split("\n", 1)
    assert head == "[long.md: characters 0 to 30000 of 70000; the rest starts at start=30000]"
    assert len(body) == 30_000
    assert box.read("long.md", "60000").startswith(
        "[long.md: characters 60000 to 70000 of 70000; that is the end of the file]")
    assert box.read("long.md") == "a" * 70_000
    e = rule_of(box.read, "long.md", "the middle")
    assert e.rule == "read_start" and "like 12000" in e.sentence


# --- write -------------------------------------------------------------------------------------


def test_shrink_guard_is_over_400_characters_shrinking_below_70_percent():
    assert files.shrink_guard(1000, 699)
    assert not files.shrink_guard(1000, 700)
    assert not files.shrink_guard(400, 1)
    assert files.shrink_guard(401, 1)


def test_a_big_shrink_needs_replace_and_then_keeps_the_old_text(box):
    box.write("notes.md", "x" * 1000)
    e = rule_of(box.write, "notes.md", "short")
    assert e.rule == "shrink_guard"
    assert e.sentence == ("Nothing was written: notes.md holds 1000 characters and this would "
                          "replace it with 5, losing most of what is there. To add to it, use "
                          "add_to_file. To change one part of it, use edit_file. To rewrite it "
                          "on purpose, call write_file again with replace: true.")
    assert box.read("notes.md") == "x" * 1000
    assert box.write("notes.md", "short", replace=True) == (
        "replaced notes.md (5 characters); the old text (1000 characters) is kept in "
        "notes.previous.md")
    assert box.read("notes.previous.md") == "x" * 1000
    assert box.write("notes.md", "other") == "replaced notes.md (5 characters)"


def test_a_file_takes_200000_characters_and_a_folder_2000_items(box, monkeypatch):
    assert files.over_size(200_001) and not files.over_size(200_000)
    e = rule_of(box.write, "big.md", "x" * 200_001)
    assert e.rule == "file_limits"
    assert "200001 characters and a file takes 200000" in e.sentence
    box.write("big.md", "x" * 199_999)
    e = rule_of(box.append, "big.md", "yy")
    assert e.rule == "file_limits" and e.sentence.startswith("Nothing was added")
    e = rule_of(box.edit, "big.md", "x", "yy", every=True)
    assert e.rule == "file_limits" and e.sentence.startswith("Nothing was changed")

    monkeypatch.setattr(files, "MAX_FILES", 3)
    box.write("a.md", "1")
    box.write("b.md", "1")
    e = rule_of(box.write, "c.md", "1")
    assert e.rule == "file_limits" and "already holds 3 items and it takes 3" in e.sentence
    assert box.write("a.md", "2") == "replaced a.md (1 characters)"  # an existing file still saves


def test_text_is_stored_exactly_as_written_on_every_platform(box):
    box.write("crlf.md", "one\r\ntwo\n")
    assert (box.root / "crlf.md").read_bytes() == b"one\r\ntwo\n"
    box.edit("crlf.md", "two", "three")
    assert (box.root / "crlf.md").read_bytes() == b"one\r\nthree\n"
    box.append("crlf.md", "four\r\n")
    assert (box.root / "crlf.md").read_bytes() == b"one\r\nthree\nfour\r\n"


# --- add -----------------------------------------------------------------------------------------


def test_append_duplicate_is_80_characters_already_in_the_file():
    block = "b" * 80
    assert files.append_duplicate("a" + block + "c", "\n" + block + "\n")
    assert not files.append_duplicate("a" + "b" * 79, "b" * 79)
    assert not files.append_duplicate("", block)


def test_the_same_block_is_not_added_twice(box):
    block = "A finding worth keeping: " + "detail " * 12
    box.append("notes.md", block)
    e = rule_of(box.append, "notes.md", "\n" + block)
    assert e.rule == "append_duplicate"
    assert e.sentence == (f"Nothing was added: that exact text ({len(block.strip())} characters) "
                          "is already in notes.md, so it was added before. Carry on with the "
                          "next thing.")
    assert box.read("notes.md") == block
    box.append("notes.md", "ok")  # a short repeat is ordinary
    box.append("notes.md", "ok")
    assert box.read("notes.md").endswith("okok")


# --- edit ----------------------------------------------------------------------------------------


def test_an_edit_replaces_one_exact_passage(box):
    box.write("a.md", "one two three")
    assert box.edit("a.md", "two", "2") == ("changed a.md: replaced 3 characters with 1 (the "
                                            "file is now 11 characters)")
    assert box.read("a.md") == "one 2 three"
    box.edit("a.md", " 2", "")
    assert box.read("a.md") == "one three"


def test_an_edit_matches_through_differences_in_spacing_alone(box):
    assert files.locate("the quick\nbrown fox", "quick brown") == ("quick\nbrown", 1)
    assert files.locate("a b a b", "a b") == ("a b", 2)
    assert files.locate("only here", "  only here \n") == ("only here", 1)
    box.write("a.md", "the passage was wrapped onto\ntwo lines here")
    box.edit("a.md", "wrapped onto two lines", "kept on one line")
    assert box.read("a.md") == "the passage was kept on one line here"


def test_a_miss_shows_the_closest_passage(box):
    box.write("a.md", "first line\nDonate before the first of December\nlast line")
    e = rule_of(box.edit, "a.md", "Donate before the 1st of December", "x")
    assert e.rule == "edit_exact_once"
    assert e.sentence == (
        "Nothing was changed: that old_text is in a.md 0 times. The closest passage in the "
        "file now (line 2) is:\nDonate before the first of December\nCopy the passage exactly "
        "from the file as it is now (read_file it first).")
    e = rule_of(box.edit, "a.md", "gone", "last line")
    assert "has most likely been made already" in e.sentence
    assert rule_of(box.edit, "a.md", "", "x").rule == "edit_exact_once"
    assert rule_of(box.edit, "nope.md", "a", "b").rule == "file_missing"


def test_many_matches_need_every(box):
    box.write("a.md", "cat dog cat dog cat")
    e = rule_of(box.edit, "a.md", "cat", "bird")
    assert e.rule == "edit_exact_once"
    assert e.sentence == ("Nothing was changed: that old_text appears 3 times in a.md. Include "
                          "more of the text around it so it matches only one place, or set "
                          "every to true to change all of them.")
    assert "in 3 places" in box.edit("a.md", "cat", "bird", every=True)
    assert box.read("a.md") == "bird dog bird dog bird"


def test_an_insertion_already_made_is_not_made_twice(box):
    assert files.edit_already_made("a b c", "a", "a b", 1)
    assert not files.edit_already_made("a c", "a", "a b", 1)
    assert not files.edit_already_made("a b c", "a", "z", 1)
    box.write("a.md", "# Title\nbody")
    box.edit("a.md", "# Title", "# Title\nnew line")
    e = rule_of(box.edit, "a.md", "# Title", "# Title\nnew line")
    assert e.rule == "edit_already_made"
    assert e.sentence == ("Nothing was changed: that new_text is already in a.md, so this edit "
                          "was made before. Applying it again would put the passage in 2 times. "
                          "Carry on with the next thing.")
    assert box.read("a.md") == "# Title\nnew line\nbody"


# --- the tools, through the act stage ----------------------------------------------------


def test_the_file_tools_run_against_the_agents_own_folder(v):  # noqa: F811
    d = call(v, "ada", "write_file", {"path": "/files/notes/plan.md", "content": "one"})
    assert d.result == "created notes/plan.md (3 characters)"
    assert d.args_logged["path"] == "notes/plan.md" and d.category == "work"
    assert (v.home("ada") / "notes" / "plan.md").read_text(encoding="utf-8") == "one"
    assert call(v, "ada", "add_to_file", {"path": "notes/plan.md", "content": " two"}).result \
        == "added 4 characters to notes/plan.md"
    assert "replaced 3 characters with 1" in call(
        v, "ada", "edit_file", {"path": "notes/plan.md", "old_text": "one", "new_text": "1"}).result
    assert call(v, "ada", "read_file", {"path": "notes/plan.md"}, mode="explore").category == \
        "explore"
    assert call(v, "ada", "read_file", {"path": "notes/plan.md"}).result == "1 two"
    assert call(v, "ada", "list_files", {}).result.splitlines()[0] == "notes/"
    assert call(v, "ada", "list_files", {"path": "notes"}).result == "plan.md (5 bytes)"
    made = [e for e in v.log.query(agent="ada", kinds=["action"]) if e.data["making"]]
    assert [e.data["tool"] for e in made] == ["write_file", "add_to_file", "edit_file"]


def test_one_agent_cannot_reach_anothers_folder_through_the_tools(v):  # noqa: F811
    call(v, "bo", "write_file", {"path": "private.md", "content": "bo only"})
    call(v, "bo", "write_file", {"path": "shared/bo/open.md", "content": "for all"})
    for path in (f"../../bo/{H}/files/private.md", f"../../../agents/bo/{H}/files/private.md",
                 f"shared/../agents/bo/{H}/files/private.md"):
        e = refused(v, "ada", "read_file", {"path": path})
        assert e.rule == "files_confined" and e.sentence.startswith("Nothing happened")
    assert call(v, "ada", "read_file", {"path": "shared/bo/open.md"}).result == "for all"
    e = refused(v, "ada", "write_file", {"path": "shared/bo/open.md", "content": "x"})
    assert e.rule == "shared_write_own_only" and e.frustrate
    rejects = v.log.query(agent="ada", kinds=["validator_reject"])
    assert len(rejects) == 4 and rejects[-1].data["args"]["path"] == "shared/bo/open.md"


def test_a_refused_file_action_changes_nothing_on_disk(v):  # noqa: F811
    call(v, "ada", "write_file", {"path": "a.md", "content": "x" * 500})
    e = refused(v, "ada", "write_file", {"path": "a.md", "content": "tiny"})
    assert e.rule == "shrink_guard"
    assert (v.home("ada") / "a.md").read_text(encoding="utf-8") == "x" * 500
    d = call(v, "ada", "write_file", {"path": "a.md", "content": "tiny", "replace": True})
    assert "kept in a.previous.md" in d.result and d.args_logged["replace"] is True
