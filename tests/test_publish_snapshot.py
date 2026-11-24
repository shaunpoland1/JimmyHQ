"""Publishing a snapshot to another repository: only committed files go, as one commit, and
nothing goes while a private detail is in them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_snapshot as tool  # noqa: E402


def git(where: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=where, capture_output=True, text=True,  # noqa: S603, S607
                          check=True).stdout


def repo(path: Path, files: dict[str, str] | None = None) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Example Person")
    git(path, "config", "user.email", "person@example.com")
    git(path, "config", "commit.gpgsign", "false")
    for name, text in (files or {}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    if files:
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "first")
    return path


def test_only_committed_files_go_and_as_one_commit_without_the_history(tmp_path):
    source = repo(tmp_path / "dev", {"README.md": "one\n", "src/a.py": "x = 1\n"})
    (source / "README.md").write_text("two\n", encoding="utf-8")
    git(source, "commit", "-q", "-am", "a private note about the work")
    (source / "notes.txt").write_text("never committed\n", encoding="utf-8")
    target = repo(tmp_path / "public")

    said = tool.publish(source, target, "0.1.0: first public version", check_secrets=False)

    assert "Nothing was pushed" in said
    assert sorted(git(target, "ls-files").split()) == ["README.md", "src/a.py"]
    assert (target / "README.md").read_text(encoding="utf-8") == "two\n"
    log = git(target, "log", "--format=%s")
    assert log.splitlines() == ["0.1.0: first public version"]
    assert "private note" not in git(target, "log", "-p")


def test_a_later_snapshot_adds_one_commit_and_removes_what_was_deleted(tmp_path):
    source = repo(tmp_path / "dev", {"keep.md": "k\n", "old.md": "o\n"})
    target = repo(tmp_path / "public")
    tool.publish(source, target, "first", check_secrets=False)
    git(source, "rm", "-q", "old.md")
    (source / "new.md").write_text("n\n", encoding="utf-8")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "second")

    tool.publish(source, target, "second", check_secrets=False)

    assert sorted(git(target, "ls-files").split()) == ["keep.md", "new.md"]
    assert git(target, "log", "--format=%s").splitlines() == ["second", "first"]
    again = tool.publish(source, target, "third", check_secrets=False)
    assert "nothing to commit" in again


@pytest.mark.parametrize(("name", "text", "what"), [
    ("a.md", "see /Users/" + "someone/notes\n", "a home directory path"),
    ("a.md", "open http://box." + "ts.net:8440\n", "a private network hostname"),
    ("a.md", "write to someone@" + "mail-host.org\n", "an email address"),
    ("a.md", "at 192." + "168.4.20 today\n", "a private network address"),
])
def test_nothing_goes_while_a_personal_detail_is_in_the_files(tmp_path, name, text, what):
    source = repo(tmp_path / "dev", {name: text, "ok.md": "fine\n"})
    target = repo(tmp_path / "public")
    with pytest.raises(tool.Stop) as stop:
        tool.publish(source, target, "x", check_secrets=False)
    assert what in str(stop.value) and "a.md:1" in str(stop.value)
    assert git(target, "ls-files") == ""


def test_the_owners_private_words_stop_it_and_are_not_printed_whole(tmp_path):
    source = repo(tmp_path / "dev", {"docs/guide.md": "line\nthe Quibblewick room\n",
                                     "art/quibblewick.txt": "x\n"})
    target = repo(tmp_path / "public")
    terms = tmp_path / "terms.txt"
    terms.write_text("# mine\nquibblewick\n\n", encoding="utf-8")
    with pytest.raises(tool.Stop) as stop:
        tool.publish(source, target, "x", terms=tool.read_terms(terms), check_secrets=False)
    words = str(stop.value)
    assert "docs/guide.md:2" in words and "file's name" in words
    assert "uibblewick" not in words.lower() and "qu…" in words


def test_a_target_with_work_in_progress_or_no_repository_is_left_alone(tmp_path):
    source = repo(tmp_path / "dev", {"a.md": "a\n"})
    target = repo(tmp_path / "public", {"b.md": "b\n"})
    (target / "b.md").write_text("changed\n", encoding="utf-8")
    with pytest.raises(tool.Stop, match="not committed"):
        tool.publish(source, target, "x", check_secrets=False)
    assert (target / "b.md").read_text(encoding="utf-8") == "changed\n"
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(tool.Stop, match="not a git checkout"):
        tool.publish(source, plain, "x", check_secrets=False)
    with pytest.raises(tool.Stop, match="itself"):
        tool.publish(source, source, "x", check_secrets=False)


def test_this_repository_as_it_stands_passes_the_checks(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    tool.export(ROOT, "HEAD", tree)
    assert tool.problems(tree, []) == []


IGNORE = ("# kept at home\n\nprivate_app\ndocs/private_app.md\nweb/tests/private.*.test.ts\n"
          ".publicignore\n")
HOME_AND_PUBLIC = {
    ".publicignore": IGNORE, "README.md": "r\n", "docs/guide.md": "g\n",
    "docs/private_app.md": "p\n", "private_app/main.py": "x = 1\n",
    "private_app/tests/test_it.py": "y = 2\n", "private_apps_are_not/this.py": "z = 3\n",
    "web/tests/private.one.test.ts": "a\n", "web/tests/private.two.test.ts": "b\n",
    "web/tests/public.test.ts": "c\n",
}


def test_what_the_ignore_list_names_and_what_is_under_it_stays_out_of_the_copy(tmp_path):
    source = repo(tmp_path / "dev", HOME_AND_PUBLIC)
    target = repo(tmp_path / "public")

    tool.publish(source, target, "first", check_secrets=False)

    assert sorted(git(target, "ls-files").split()) == [
        "README.md", "docs/guide.md", "private_apps_are_not/this.py", "web/tests/public.test.ts"]
    assert not (target / "private_app").exists()
    assert not (target / ".publicignore").exists()


def test_the_ignore_list_is_read_from_the_commit_published_not_the_working_folder(tmp_path):
    source = repo(tmp_path / "dev", HOME_AND_PUBLIC)
    (source / ".publicignore").write_text("README.md\n", encoding="utf-8")  # not committed
    tree = tmp_path / "tree"
    tree.mkdir()
    tool.export(source, "HEAD", tree)
    assert (tree / "README.md").exists() and not (tree / "private_app").exists()


def test_a_line_of_the_ignore_list_that_matches_nothing_stops_the_copy(tmp_path):
    source = repo(tmp_path / "dev", {**HOME_AND_PUBLIC, ".publicignore": IGNORE + "renamed_app\n"})
    target = repo(tmp_path / "public")
    with pytest.raises(tool.Stop, match="renamed_app"):
        tool.publish(source, target, "x", check_secrets=False)
    assert git(target, "ls-files") == ""


def test_the_private_word_checks_still_run_on_what_is_left(tmp_path):
    files = {**HOME_AND_PUBLIC, "docs/guide.md": "write to someone@" + "mail-host.org\n"}
    source = repo(tmp_path / "dev", files)
    target = repo(tmp_path / "public")
    with pytest.raises(tool.Stop, match="docs/guide.md:1"):
        tool.publish(source, target, "x", check_secrets=False)


def test_a_private_word_inside_a_held_back_file_does_not_stop_the_copy(tmp_path):
    files = {**HOME_AND_PUBLIC, "private_app/main.py": "# /Users/" + "someone/notes\n"}
    source = repo(tmp_path / "dev", files)
    target = repo(tmp_path / "public")
    tool.publish(source, target, "x", check_secrets=False)
    assert not (target / "private_app").exists()


def test_without_an_ignore_list_everything_tracked_goes(tmp_path):
    source = repo(tmp_path / "dev", {"a.md": "a\n", "b/c.md": "c\n"})
    tree = tmp_path / "tree"
    tree.mkdir()
    tool.export(source, "HEAD", tree)
    assert sorted(p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()) == [
        "a.md", "b/c.md"]


def test_the_ignore_list_skips_comments_and_blank_lines_and_trailing_slashes():
    assert tool.read_ignore("# a\n\n  one/  \ntwo.md\n#three\n") == ["one", "two.md"]
    assert tool.held_back("one/x/y.py", ["one"]) == "one"
    assert tool.held_back("one.py", ["one"]) is None
    assert tool.held_back("a/one/x.py", ["one"]) is None


def test_text_the_owner_allows_may_stay_though_it_holds_one_of_their_words(tmp_path):
    source = repo(tmp_path / "dev", {"a.md": "code at example.org/quibblewick1/thing\n"})
    target = repo(tmp_path / "public")
    terms = ["quibblewick", "allow: example.org/quibblewick1/thing"]
    assert "Committed" in tool.publish(source, target, "x", terms=terms, check_secrets=False)
    (source / "b.md").write_text("Quibblewick wrote this\n", encoding="utf-8")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "more")
    with pytest.raises(tool.Stop, match="b.md:1"):
        tool.publish(source, target, "y", terms=terms, check_secrets=False)
