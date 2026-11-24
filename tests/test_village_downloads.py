"""Downloading files: one file, one zip, and no path that leaves its folder."""

from __future__ import annotations

import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from jimmyhq.village import downloads, record, roster
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
API = "/api/village"
A = "agent-a"


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver",
                    raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}
                       ).status_code == 200
    return client


def token_client(owner, core, *scopes):
    headers = {"authorization": f"Bearer {core.auth.create_token('helper', list(scopes))[1]}"}
    return TestClient(owner.app, base_url="http://testserver", headers=headers,
                      raise_server_exceptions=False)


@pytest.fixture
def home(core):
    """One living agent with files, and a file in the shared place."""
    record.born(core, A, 7, "researcher")
    folder = roster.agent_dir(core, A)
    files = folder / "home" / "files"
    (files / "drafts").mkdir(parents=True)
    (files / "notes.md").write_text("my notes", encoding="utf-8")
    (files / "drafts" / "one.md").write_text("draft one", encoding="utf-8")
    (files / ".hidden").write_text("dotfile", encoding="utf-8")
    (folder / "secret.txt").write_text("outside the files folder", encoding="utf-8")
    shared = roster.shared_dir(core)
    (shared / A).mkdir(parents=True)
    (shared / A / "draft.md").write_text("shared draft", encoding="utf-8")
    return folder


def _zip_names(response):
    return sorted(zipfile.ZipFile(io.BytesIO(response.content)).namelist())


def test_one_file_downloads_as_an_attachment_with_its_own_bytes(owner, home):
    r = owner.get(f"{API}/agents/{A}/files/download", params={"path": "drafts/one.md"})
    assert r.status_code == 200 and r.content == b"draft one"
    assert r.headers["content-disposition"].startswith('attachment; filename="one.md"')
    assert r.headers["cache-control"] == "private"
    assert r.headers["content-type"].startswith("text/markdown") or \
        r.headers["content-type"] == "application/octet-stream"


def test_a_file_that_could_run_as_a_page_is_sent_as_plain_bytes(owner, home):
    (home / "home" / "files" / "page.html").write_text("<script>1</script>", encoding="utf-8")
    r = owner.get(f"{API}/agents/{A}/files/download", params={"path": "page.html"})
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_a_file_name_cannot_break_out_of_the_download_header():
    # Asked of the rule itself: Windows cannot hold a file with a quote in its name.
    head = downloads.disposition('a"b;c\u00e9\r\n.txt')
    assert head.startswith('attachment; filename="a_b_c_')
    assert "\n" not in head and "\r" not in head and "%C3%A9" in head


def test_all_of_an_agents_files_download_as_one_zip_without_hidden_names(owner, home):
    r = owner.get(f"{API}/agents/{A}/files/zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert f'filename="{A}-files.zip"' in r.headers["content-disposition"]
    assert _zip_names(r) == ["drafts/one.md", "notes.md"]
    inner = owner.get(f"{API}/agents/{A}/files/zip", params={"path": "drafts"})
    assert _zip_names(inner) == ["one.md"]
    assert 'filename="drafts.zip"' in inner.headers["content-disposition"]


def test_the_shared_folders_download_a_file_and_a_zip(owner, home):
    one = owner.get(f"{API}/shared/download", params={"path": f"{A}/draft.md"})
    assert one.content == b"shared draft"
    whole = owner.get(f"{API}/shared/zip")
    assert _zip_names(whole) == [f"{A}/draft.md"]
    assert 'filename="shared-folders.zip"' in whole.headers["content-disposition"]


def test_downloads_are_for_the_owner_only(client, owner, core, home):
    token = token_client(owner, core, "read")
    for path in (f"agents/{A}/files/download?path=notes.md", f"agents/{A}/files/zip",
                 "shared/download?path=x", "shared/zip"):
        assert token.get(f"{API}/{path}").status_code == 403, path
    signed_out = TestClient(owner.app, base_url="http://testserver")
    assert signed_out.get(f"{API}/agents/{A}/files/zip").status_code == 401


@pytest.mark.parametrize("path", [
    "../secret.txt", "drafts/../../secret.txt", "/etc/hosts", "..\\secret.txt",
    "C:\\Windows\\win.ini", ".hidden", "drafts/.git/config", "notes.md\x00.txt", "..",
    "//server/share/x", "%2e%2e/secret.txt",
])
def test_no_download_path_leaves_the_folder_it_serves(owner, home, path):
    for route in (f"agents/{A}/files/download", f"agents/{A}/files/zip", "shared/download",
                  "shared/zip"):
        r = owner.get(f"{API}/{route}", params={"path": path})
        assert r.status_code in (400, 404), (route, path, r.status_code)
        assert b"outside the files folder" not in r.content


def test_a_download_cannot_reach_another_agents_folder(owner, core, home):
    record.born(core, "agent-b", 8, "editor")
    other = roster.agent_dir(core, "agent-b") / "home" / "files"
    other.mkdir(parents=True)
    (other / "theirs.md").write_text("their notes", encoding="utf-8")
    for path in ("../../../agent-b/" + "/".join(("home", "files")) + "/theirs.md",
                 "../agent-b/theirs.md"):
        r = owner.get(f"{API}/agents/{A}/files/download", params={"path": path})
        assert r.status_code == 400 and b"their notes" not in r.content
    assert owner.get(f"{API}/agents/..%2Fagent-b/files/zip").status_code in (404, 400)
    assert owner.get(f"{API}/agents/nobody/files/zip").status_code == 404


def test_links_that_lead_out_are_neither_downloaded_nor_put_in_the_zip(owner, core, home):
    files = home / "home" / "files"
    try:
        os.symlink(home / "secret.txt", files / "link.txt")
        os.symlink(home, files / "up", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow making links")
    for path in ("link.txt", "up/secret.txt"):
        assert owner.get(f"{API}/agents/{A}/files/download", params={"path": path}
                         ).status_code == 400
    assert owner.get(f"{API}/agents/{A}/files/zip", params={"path": "up"}).status_code == 400
    names = _zip_names(owner.get(f"{API}/agents/{A}/files/zip"))
    assert names == ["drafts/one.md", "notes.md"]


def test_a_missing_file_or_folder_is_a_plain_not_found(owner, home):
    assert owner.get(f"{API}/agents/{A}/files/download", params={"path": "nope.md"}
                     ).status_code == 404
    assert owner.get(f"{API}/agents/{A}/files/download", params={"path": "drafts"}
                     ).status_code == 404
    assert owner.get(f"{API}/agents/{A}/files/zip", params={"path": "notes.md"}
                     ).status_code == 404


def test_a_zip_over_the_size_setting_is_refused_with_a_sentence_that_says_what_to_do(
        owner, core, home):
    (home / "home" / "files" / "big.bin").write_bytes(b"0" * (1024 * 1024 + 1))
    core.settings.set(downloads.CAP_SETTING, 1, actor="owner")
    r = owner.get(f"{API}/agents/{A}/files/zip")
    assert r.status_code == 413
    assert "smaller folder" in r.json()["detail"] and "Settings" in r.json()["detail"]
    ok = owner.get(f"{API}/agents/{A}/files/zip", params={"path": "drafts"})
    assert ok.status_code == 200


def test_the_zip_size_limit_is_a_setting_with_a_default():
    from jimmyhq.settings import declared
    spec = {s.key: s for s in declared()}[downloads.CAP_SETTING]
    assert spec.default == 200 and spec.min == 1
