"""Installing from a git address at an exact commit, tried from the attacker's side.

No network: every repository is built in a temp folder, and ``use_local`` stands it in for
the https address after the address itself has been checked for real."""

from __future__ import annotations

import os
import subprocess

import pytest

from apps_install_helpers import (
    URL,
    commit_all,
    events,
    example,
    expected_tree,
    git,
    make_repo,
    staging,
    use_local,
    write_manifest,
)
from jimmyhq.apps import install, store
from jimmyhq.apps.store import AppError

COMMIT = "3f2a9c1e5b7d4a6c8e0f1a2b3c4d5e6f70819203"


def refused(fn, *args, **kwargs) -> AppError:
    with pytest.raises(AppError) as caught:
        fn(*args, **kwargs)
    return caught.value


@pytest.fixture
def no_git(monkeypatch):
    """Fail the test if git is started at all."""
    def boom(*args, **kwargs):
        raise AssertionError(f"git was run: {args}")
    monkeypatch.setattr(install.subprocess, "run", boom)


@pytest.fixture
def water(tmp_path, monkeypatch):
    repo = example(tmp_path, "water")
    commit = make_repo(repo)
    use_local(monkeypatch, repo)
    return repo, commit


# --- the ordinary flow ----------------------------------------------------------------------


def test_an_app_installs_from_a_git_address_pinned_to_the_exact_commit(core, water):
    repo, commit = water
    plan = install.plan_git(core, URL, commit=commit)
    assert plan["source"] == {
        "kind": "git", "repo": URL, "commit": commit, "ref": None,
        "tree": plan["source"]["tree"], "files": 3, "bytes": plan["source"]["bytes"],
        "left_out": 0}
    assert plan["reviewed"] is False and events(core) == []
    done = install.confirm(core, plan["plan"], plan["fingerprint"])
    assert (done["source"], done["repo"], done["commit"]) == ("git", URL, commit)
    [event] = events(core, "apps.installed")
    assert event.payload["commit"] == commit and event.payload["repo"] == URL
    assert event.payload["tree"] == plan["source"]["tree"]
    assert expected_tree(store.code_dir(core, "water")) == plan["source"]["tree"]
    assert staging(core) == []


@pytest.mark.parametrize("name", ["books_read", "water", "drafts"])
def test_the_same_files_have_the_same_tree_hash_from_git_as_from_a_folder(
        core, tmp_path, monkeypatch, name):
    repo = example(tmp_path, name)
    by_folder = install.plan_folder(core, repo)["source"]["tree"]
    commit = make_repo(repo)
    use_local(monkeypatch, repo)
    plan = install.plan_git(core, URL, commit=commit)
    assert plan["source"]["tree"] == by_folder == expected_tree_without_git(repo)
    done = install.confirm(core, plan["plan"], plan["fingerprint"],
                           typed_name=plan["app"]["name"])
    assert done["id"] == name


def expected_tree_without_git(repo):
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "copy")
        shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))
        from pathlib import Path
        return expected_tree(Path(copy))


def test_an_older_commit_installs_as_it_was_not_as_the_branch_is_now(core, water):
    repo, first = water
    write_manifest(repo, version="2.0.0", permissions=["tasks.write", "model.ask"])
    second = commit_all(repo, "more")
    plan = install.plan_git(core, URL, commit=first)
    assert plan["app"]["version"] == "1.0.0" and second != first
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert store.granted(core, "water") == ("tasks.write",)


def test_a_branch_or_tag_is_looked_up_once_and_the_commit_is_what_gets_pinned(core, water):
    repo, first = water
    git(repo, "tag", "-a", "v1.0.0", "-m", "one")
    write_manifest(repo, version="1.1.0")
    second = commit_all(repo, "next")
    by_tag = install.plan_git(core, URL, ref="v1.0.0")
    assert by_tag["source"]["commit"] == first and by_tag["source"]["ref"] == "v1.0.0"
    by_branch = install.plan_git(core, URL, ref="main")
    assert by_branch["source"]["commit"] == second
    write_manifest(repo, version="9.9.9")
    commit_all(repo, "moved on after the plan")
    install.confirm(core, by_branch["plan"], by_branch["fingerprint"])
    assert store.get(core, "water")["version"] == "1.1.0"
    assert store.get(core, "water")["commit"] == second


def test_a_name_the_repository_does_not_have_is_refused(core, water):
    error = refused(install.plan_git, core, URL, ref="nope")
    assert error.status == 404 and "Nothing was installed" in error.detail
    assert staging(core) == []


def test_a_commit_the_repository_does_not_have_is_refused(core, water):
    error = refused(install.plan_git, core, URL, commit=COMMIT)
    assert error.status == 502 and "nothing was installed" in error.detail
    assert staging(core) == []


def test_dot_files_in_the_repository_are_left_out(core, water):
    repo, _ = water
    (repo / ".gitignore").write_text("x\n")
    (repo / ".github").mkdir()
    (repo / ".github" / "ci.yml").write_text("x\n")
    commit = commit_all(repo)
    plan = install.plan_git(core, URL, commit=commit)
    assert plan["source"]["files"] == 3 and plan["source"]["left_out"] == 2


def test_a_file_is_installed_byte_for_byte_whatever_gitattributes_asks_for(core, water):
    repo, _ = water
    raw = b"line one\r\nline two\n$Id$\n"
    (repo / ".gitattributes").write_text("*.txt text eol=lf ident filter=lfs export-subst\n")
    commit_all(repo)
    blob = git(repo, "hash-object", "-w", "--no-filters", "--stdin", stdin=raw)
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},data.txt")
    git(repo, "commit", "--quiet", "-m", "raw")
    commit = git(repo, "rev-parse", "HEAD")
    plan = install.plan_git(core, URL, commit=commit)
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert (store.code_dir(core, "water") / "data.txt").read_bytes() == raw


# --- the address ----------------------------------------------------------------------------


def _addresses() -> list[str]:
    at = "@"
    return [
        "--upload-pack=touch /tmp/owned",
        "-oProxyCommand=touch owned",
        "--upload-pack=sh https://git.example.com/apps/water.git",
        "ext::sh -c touch% owned",
        "ext::sh -c 'touch owned' https://git.example.com/a.git",
        "https://git.example.com/ext::sh",
        "fd::7",
        "http://git.example.com/apps/water.git",
        "HTTPS://git.example.com/apps/water.git",
        "git://git.example.com/apps/water.git",
        "ssh://git.example.com/apps/water.git",
        "git+ssh://git.example.com/apps/water.git",
        "file:///srv/apps/water",
        "/srv/apps/water",
        "../water",
        "C:\\apps\\water",
        f"git{at}git.example.com:apps/water.git",
        f"https://name:word{at}git.example.com/apps/water.git",
        f"https://token{at}git.example.com/apps/water.git",
        "https://git.example.com/apps/water.git?x=--upload-pack",
        "https://git.example.com/apps/water.git#frag",
        "https://git.example.com/apps/water.git --upload-pack=x",
        "https://git.example.com/apps/water.git\n--upload-pack=x",
        "https://git.example.com/apps/../../water.git",
        "https://git.example.com/apps/%2e%2e/water.git",
        "https://-oProxyCommand=x/apps/water.git",
        "https:///apps/water.git",
        "https://git.example.com",
        "https://git.example.com:99999/a.git",
        "https://git.example.com/a\\b.git",
        "https://git.exämple.com/a.git",
        "",
        "https://git.example.com/" + "a" * 600,
    ]


@pytest.mark.parametrize("url", _addresses())
def test_an_address_that_is_not_plain_https_is_refused_before_git_is_ever_run(
        core, no_git, url):
    error = refused(install.plan_git, core, url, commit=COMMIT)
    assert error.status == 422
    assert "Nothing was fetched or installed" in error.detail and "https" in error.detail
    assert staging(core) == []


def test_only_text_is_an_address(core, no_git):
    for url in (None, 7, ["https://git.example.com/a.git"], b"https://git.example.com/a.git"):
        assert refused(install.plan_git, core, url, commit=COMMIT).status == 422


@pytest.mark.parametrize("url", [
    "https://git.example.com/apps/water.git", "https://git.example.com/apps/water",
    "https://git.example.com:8443/team/sub/water.git", "https://example.com/w/",
])
def test_a_plain_https_address_is_accepted(url):
    assert install.check_git_url(url) == url


@pytest.mark.parametrize("commit", [
    "--upload-pack=touch owned", "-c", "main", "HEAD", COMMIT[:39], COMMIT + "0",
    COMMIT.upper(), COMMIT[:7], f"{COMMIT} --upload-pack=x", f"{COMMIT}\n", "", 7,
])
def test_a_commit_that_is_not_a_full_hash_is_refused_before_git_is_ever_run(
        core, no_git, commit):
    error = refused(install.plan_git, core, URL, commit=commit)
    assert error.status == 422 and "Nothing was fetched or installed" in error.detail


@pytest.mark.parametrize("ref", [
    "--upload-pack=touch owned", "-main", "a..b", "a b", "main\n", "refs/heads/../x",
    "x.lock", "a//b", "main/", "é", "a" * 300, "main^{}", "+main:main", "a:b", "*",
])
def test_a_branch_name_git_could_read_as_anything_else_is_refused_before_git_is_run(
        core, no_git, ref):
    error = refused(install.plan_git, core, URL, ref=ref)
    assert error.status == 422 and "Nothing was fetched or installed" in error.detail


def test_an_address_with_no_commit_or_branch_is_refused(core, no_git):
    assert refused(install.plan_git, core, URL).status == 422


# --- what git is allowed to do --------------------------------------------------------------


def test_git_is_run_as_an_argument_list_with_the_address_after_a_double_dash(
        core, water, monkeypatch):
    repo, commit = water
    seen = []
    real = subprocess.run

    def spy(argv, **kwargs):
        seen.append((argv, kwargs))
        return real(argv, **kwargs)

    monkeypatch.setattr(install.subprocess, "run", spy)
    monkeypatch.setenv("GIT_SSH_COMMAND", "touch owned")
    monkeypatch.setenv("GIT_DIR", "/nowhere")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    install.plan_git(core, URL, ref="main")
    assert len(seen) >= 4
    for argv, kwargs in seen:
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        assert not kwargs.get("shell") and kwargs["timeout"] == 120.0
        env = kwargs["env"]
        assert [k for k in env if k.startswith("GIT_")] == [
            "GIT_ALLOW_PROTOCOL", "GIT_TERMINAL_PROMPT", "GIT_LFS_SKIP_SMUDGE",
            "GIT_OPTIONAL_LOCKS"]
        assert env["GIT_ALLOW_PROTOCOL"] == "file" and env["GIT_TERMINAL_PROMPT"] == "0"
        joined = " ".join(argv)
        for option in ("protocol.allow=never", "core.hooksPath=", "fetch.fsckObjects=true",
                       "fetch.recurseSubmodules=false", "filter.lfs.smudge=",
                       "transfer.bundleURI=false"):
            assert f"-c {option}" in joined
    networked = [argv for argv, _ in seen if "fetch" in argv or "ls-remote" in argv]
    assert len(networked) == 2
    for argv in networked:
        assert argv[argv.index("--") + 1] == str(repo)
    fetch = next(argv for argv in networked if "fetch" in argv)
    assert {"--depth=1", "--no-tags", "--no-recurse-submodules"} <= set(fetch)
    assert not any("checkout" in argv or "clone" in argv or "archive" in argv
                   for argv, _ in seen)


def test_only_https_is_allowed_in_production():
    assert install._transport(URL) == (URL, "https")
    options = install.git_options("https", empty=__import__("pathlib").Path("e"))
    assert "protocol.allow=never" in options and "protocol.https.allow=always" in options
    assert not any(o.startswith("protocol.") and o not in (
        "protocol.allow=never", "protocol.https.allow=always") for o in options)
    assert install.git_env("https")["GIT_ALLOW_PROTOCOL"] == "https"


@pytest.mark.parametrize("remote", ["ext::sh -c 'touch owned'", "ext::touch owned"])
def test_git_itself_refuses_a_helper_program_even_if_one_reached_it(
        core, tmp_path, monkeypatch, remote):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "ext:file:https")
    work = tmp_path / "work"
    (work / "empty").mkdir(parents=True)
    monkeypatch.chdir(work)
    error = refused(install.fetch_commit, core, remote, COMMIT, work / "repo.git",
                    empty=work / "empty")
    assert error.status == 502
    found = [p for p in tmp_path.rglob("owned")]
    assert found == []


def test_git_itself_refuses_a_local_path_when_only_https_is_allowed(core, tmp_path):
    repo = example(tmp_path, "water")
    commit = make_repo(repo)
    work = tmp_path / "work"
    (work / "empty").mkdir(parents=True)
    for remote in (str(repo), repo.as_uri()):
        error = refused(install.fetch_commit, core, remote, commit, work / f"{len(remote)}.git",
                        empty=work / "empty")
        assert error.status == 502


def test_a_hook_in_the_source_repository_never_runs(
        core, water, tmp_path, monkeypatch):
    repo, commit = water
    marker = tmp_path / "hook-ran"
    for name in ("post-checkout", "reference-transaction", "post-merge", "pre-auto-gc"):
        hook = repo / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        hook.chmod(0o755)
    plan = install.plan_git(core, URL, commit=commit)
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert not marker.exists()


def test_fetching_that_takes_too_long_is_stopped_and_says_so(core, monkeypatch):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(install.subprocess, "run", slow)
    core.settings.set("apps.fetch_timeout_s", 5, actor="owner")
    error = refused(install.plan_git, core, URL, commit=COMMIT)
    assert error.status == 504 and "after 5 seconds" in error.detail
    assert "Nothing was installed" in error.detail and staging(core) == []


def test_a_computer_without_git_says_so(core, monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    error = refused(install.plan_git, core, URL, commit=COMMIT)
    assert error.status == 503 and "could not find git" in error.detail


# --- what is in the repository --------------------------------------------------------------


def test_a_repository_with_a_link_out_of_the_tree_is_refused_and_nothing_is_written(
        core, water, tmp_path):
    repo, _ = water
    secret = tmp_path / "secret.txt"
    secret.write_text("the owner's own file")
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=str(secret).encode())
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},notes.txt")
    git(repo, "commit", "--quiet", "-m", "link")
    commit = git(repo, "rev-parse", "HEAD")
    assert "120000" in git(repo, "ls-tree", commit)
    error = refused(install.plan_git, core, URL, commit=commit)
    assert error.status == 422 and "\"notes.txt\" is a link" in error.detail
    assert "nothing was installed" in error.detail
    assert staging(core) == [] and store.installed(core) == {}


def test_a_link_to_a_folder_above_the_tree_is_refused_too(core, water):
    repo, _ = water
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=b"../../..")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},lib")
    git(repo, "commit", "--quiet", "-m", "link")
    error = refused(install.plan_git, core, URL, commit=git(repo, "rev-parse", "HEAD"))
    assert "is a link" in error.detail and staging(core) == []


def test_a_submodule_is_refused_and_never_fetched(core, water):
    repo, first = water
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{first},vendor")
    git(repo, "commit", "--quiet", "-m", "submodule")
    (repo / ".gitmodules").write_text(
        "[submodule \"vendor\"]\n\tpath = vendor\n\turl = ext::sh -c 'touch owned'\n")
    git(repo, "add", ".gitmodules")
    git(repo, "commit", "--quiet", "-m", "modules")
    error = refused(install.plan_git, core, URL, commit=git(repo, "rev-parse", "HEAD"))
    assert error.status == 422 and "submodule" in error.detail and staging(core) == []


def test_a_file_too_large_is_refused_from_the_listing_before_any_file_is_written(
        core, water, monkeypatch):
    repo, _ = water
    core.settings.set("apps.max_file_mb", 1, actor="owner")
    (repo / "big.bin").write_bytes(os.urandom(64) * (store.MB // 64 + 1))
    commit = commit_all(repo)
    wrote = []
    monkeypatch.setattr(store, "write_file", lambda *a: wrote.append(a))
    error = refused(install.plan_git, core, URL, commit=commit)
    assert error.status == 413 and "big.bin" in error.detail
    assert wrote == [] and staging(core) == []


def test_a_repository_larger_than_the_owner_allows_is_refused(core, water):
    repo, _ = water
    core.settings.set("apps.max_tree_mb", 1, actor="owner")
    for n in range(3):
        (repo / f"part{n}.bin").write_bytes(os.urandom(64) * (store.MB // 128))
    assert refused(install.plan_git, core, URL, commit=commit_all(repo)).status == 413
    core.settings.set("apps.max_files", 3, actor="owner")
    core.settings.set("apps.max_tree_mb", 50, actor="owner")
    assert refused(install.plan_git, core, URL, commit=commit_all(repo)).status == 413
    assert staging(core) == []


def _listing(*rows: tuple[str, str, str, str]) -> bytes:
    oid = "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"
    return b"".join(f"{mode} {kind} {oid} {size:>7}\t{path}".encode() + b"\0"
                    for mode, kind, size, path in rows)


@pytest.mark.parametrize("row", [
    ("100644", "blob", "3", "../evil.txt"),
    ("100644", "blob", "3", "a/../../evil.txt"),
    ("100644", "blob", "3", "/etc/evil"),
    ("100644", "blob", "3", "C:/evil"),
    ("100644", "blob", "3", "a\\..\\evil"),
    ("100644", "blob", "3", "aux.txt"),
    ("120000", "blob", "3", "link"),
    ("160000", "commit", "-", "sub"),
    ("040000", "tree", "-", "dir"),
    ("100664", "blob", "3", "odd-mode.txt"),
    ("100644", "blob", "-", "nosize.txt"),
    ("100644", "blob", "99999999999", "huge.bin"),
])
def test_a_listing_with_a_path_or_entry_that_is_not_a_plain_file_inside_is_refused(row):
    good = ("100644", "blob", "3", "index.html")
    with pytest.raises(AppError):
        install.read_listing(_listing(good, row), store.Limits())
    entries, skipped = install.read_listing(_listing(good), store.Limits())
    assert [(path, size) for _, path, size in entries] == [("index.html", 3)] and skipped == 0


def test_a_listing_with_two_names_differing_only_by_capitals_is_refused():
    with pytest.raises(AppError):
        install.read_listing(_listing(("100644", "blob", "1", "App.js"),
                                      ("100644", "blob", "1", "app.js")), store.Limits())


def test_a_repository_whose_manifest_is_refused_installs_nothing(core, water):
    repo, _ = water
    write_manifest(repo, permissions=["tape.read"])
    error = refused(install.plan_git, core, URL, commit=commit_all(repo))
    assert error.status == 422 and "tape.read" in error.detail and staging(core) == []


# --- the owner's switch for full apps -------------------------------------------------------


def test_an_unreviewed_full_app_from_a_url_carries_the_warning_and_needs_its_name(
        core, tmp_path, monkeypatch):
    repo = example(tmp_path, "drafts")
    commit = make_repo(repo)
    use_local(monkeypatch, repo)
    plan = install.plan_git(core, URL, commit=commit)
    assert plan["needs_typed_name"] and plan["review"].startswith("Not reviewed.")
    assert "JimmyHQ cannot limit this" in plan["warning"]
    assert refused(install.confirm, core, plan["plan"], plan["fingerprint"]).status == 422
    install.confirm(core, plan["plan"], plan["fingerprint"], typed_name="Drafts")
    assert store.get(core, "drafts")["tier"] == "full"


def test_with_the_setting_off_a_full_app_from_a_url_is_refused_but_a_folder_still_works(
        core, tmp_path, monkeypatch):
    repo = example(tmp_path, "drafts")
    commit = make_repo(repo)
    use_local(monkeypatch, repo)
    waiting = install.plan_git(core, URL, commit=commit)
    core.settings.set("apps.unreviewed_full_from_url", False, actor="owner")
    error = refused(install.plan_git, core, URL, commit=commit)
    assert error.status == 403 and "Nothing was installed" in error.detail
    late = refused(install.confirm, core, waiting["plan"], waiting["fingerprint"],
                   typed_name="Drafts")
    assert late.status == 403 and store.installed(core) == {}
    plan = install.plan_folder(core, repo)
    install.confirm(core, plan["plan"], plan["fingerprint"], typed_name="Drafts")
    assert store.is_enabled(core, "drafts")


def test_with_the_setting_off_a_sandboxed_app_from_a_url_still_installs(core, water):
    _, commit = water
    core.settings.set("apps.unreviewed_full_from_url", False, actor="owner")
    plan = install.plan_git(core, URL, commit=commit)
    install.confirm(core, plan["plan"], plan["fingerprint"])
    assert store.is_enabled(core, "water")
