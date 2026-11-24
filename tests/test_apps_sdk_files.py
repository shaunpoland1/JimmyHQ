"""The SDK's side of pictures and file bodies: a full app's program opens a picture by its
ref and sends files with app.net.fetch, against the fake core and against the real one."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, CallError  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import AppClient, client_for, install_full  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 500
HOST = "uploads.example.com"


@pytest.fixture
def fake(tmp_path):
    core = FakeCore("posts", data_dir=tmp_path, permissions=[f"net:{HOST}"])
    core.responses[f"https://{HOST}/put"] = {"status": 200, "headers": {}, "text": ""}
    return core


def test_a_picture_the_owner_gave_opens_by_its_ref(fake):
    app = App(transport=fake)
    given = fake.upload(JPEG, "morning.jpg")
    assert given["type"] == "image/jpeg" and given["size"] == len(JPEG)
    with app.files.open(given["ref"]) as picture:
        assert picture.read() == JPEG
    assert app.files.path(given["ref"]).parent.name == "uploads"


@pytest.mark.parametrize("ref", ["../secrets.json", "uploads/../app.db", "app.db",
                                 "/etc/hosts", "uploads/" + "a" * 32 + ".svg", None])
def test_files_path_refuses_anything_but_an_upload_ref(fake, ref):
    with pytest.raises(ValueError):
        App(transport=fake).files.path(ref)


def test_files_path_does_not_follow_a_link(fake, tmp_path):
    app = App(transport=fake)
    ref = fake.upload(JPEG)["ref"]
    path = tmp_path / ref
    path.unlink()
    (tmp_path / "elsewhere.txt").write_text("private")
    try:
        os.symlink(tmp_path / "elsewhere.txt", path)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a link")
    with pytest.raises(ValueError):
        app.files.path(ref)


def test_the_fake_core_refuses_what_is_not_a_picture(fake):
    with pytest.raises(CallError):
        fake.upload(b"<html></html>", "photo.jpg")
    with pytest.raises(CallError):
        fake.upload(JPEG, "a/b.jpg")


def test_a_fetch_with_a_file_records_the_bytes_sent_in_the_fake(fake):
    app = App(transport=fake)
    ref = fake.upload(JPEG)["ref"]
    app.net.fetch(f"https://{HOST}/put", method="PUT", file=ref,
                  headers={"Content-Type": "image/jpeg"}, fresh_connection=True)
    assert fake.sent_files == [{"url": f"https://{HOST}/put", "ref": ref, "data": JPEG}]
    assert fake.fetched[0]["fresh_connection"] is True


def test_a_form_and_a_json_file_mark_record_each_file_in_the_fake(fake):
    app = App(transport=fake)
    ref = fake.upload(JPEG)["ref"]
    app.net.fetch(f"https://{HOST}/put", method="POST",
                  multipart=[{"name": "caption", "value": "Hi"},
                             {"name": "media", "file": ref}])
    app.net.fetch(f"https://{HOST}/put", method="POST",
                  json={"media": {"$file": ref, "as": "data_url"}})
    assert [s["ref"] for s in fake.sent_files] == [ref, ref]


@pytest.mark.parametrize("path", ["../x", "uploads/../../x", "/etc/hosts", "app.db"])
def test_the_fake_core_holds_file_paths_to_the_real_rules(fake, path):
    with pytest.raises(CallError):
        App(transport=fake).net.fetch(f"https://{HOST}/put", method="PUT", file=path)
    assert fake.sent_files == []


def test_the_fake_core_refuses_a_file_for_a_host_not_granted(fake):
    ref = fake.upload(JPEG)["ref"]
    with pytest.raises(CallError) as caught:
        App(transport=fake).net.fetch("https://other.example.com/put", method="PUT", file=ref)
    assert caught.value.code == "not_allowed" and fake.sent_files == []


def test_the_real_app_api_takes_a_file_fetch_and_holds_it_to_the_same_rules(core, tmp_path):
    install_full(core, tmp_path, "posts", permissions=[f"net:{HOST}"])
    with client_for(core) as web:
        program = AppClient(web, core.auth.mint_app_token("posts"))
        error = program.refused("net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                              "file": "../outside.jpg"})
        assert error["code"] == "invalid"
        error = program.refused("net.fetch", {"url": "https://other.example.com/put",
                                              "method": "PUT", "file": "uploads/a.jpg"})
        assert error["code"] == "not_allowed"


class _Waits:
    """A transport that remembers how long each request was willing to wait."""

    def __init__(self):
        self.waits: list[float | None] = []

    def request(self, method, path, *, body=None, params=None, timeout=None):
        self.waits.append(timeout)
        return {"status": 200, "headers": {}, "text": ""}


def test_the_sdk_waits_for_a_slow_fetch_at_least_as_long_as_jimmyhq_may():
    from jimmyhq_app import app as sdk_app

    seen = _Waits()
    app = App(transport=seen)
    app.net.fetch(f"https://{HOST}/make", method="POST", json={}, timeout_s=180)
    app.net.fetch(f"https://{HOST}/make")
    assert seen.waits == [180 + sdk_app.FETCH_WAIT_MARGIN_S,
                          sdk_app.FETCH_WAIT_MOST_S + sdk_app.FETCH_WAIT_MARGIN_S]
    # The owner's longest wait for a fetch can never outlast the SDK's.
    from jimmyhq.settings import declared

    most = max(s.max for s in declared() if s.key in (
        "apps.net_fetch_timeout_s", "apps.net_fetch_file_timeout_s"))
    assert most <= sdk_app.FETCH_WAIT_MOST_S
