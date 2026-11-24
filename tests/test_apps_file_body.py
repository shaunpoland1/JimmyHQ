"""Sending a file from an app's own data folder with net.fetch: raw, as a form and inside
JSON. A made-up server checks that every byte arrived; the attacker's side tries to send
something else, or to somewhere else."""

from __future__ import annotations

import base64
import email
import hashlib
import json
import os

import httpx
import pytest

from jimmyhq import lookup
from jimmyhq.apps import appdata, file_body
from test_apps_bridge_fakes import WATER, call, events, installs, refused
from test_lookup_fakes import PUBLIC, Names, Web

HOST = "uploads.example.com"
PICTURE = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 40  # a JPEG's first bytes, then more


@pytest.fixture
def apps(monkeypatch, tmp_path):
    return installs(monkeypatch, tmp_path)


@pytest.fixture
def water(core, apps):
    apps.add(WATER, granted=[f"net:{HOST}"])
    return appdata.data_dir(core, "water")


@pytest.fixture
def server(core):
    """A made-up host that keeps every request it is sent and answers 200."""
    got: list[httpx.Request] = []

    def keep(request: httpx.Request) -> httpx.Response:
        got.append(request)
        return httpx.Response(200, json={"ok": True})

    service = lookup.service(core)
    service.transport = Web({(HOST, "/put"): keep, (HOST, "/post"): keep})
    service.resolver = Names({HOST: [PUBLIC]})
    return got


def put_file(folder, relative: str, data: bytes = PICTURE):
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def link_or_skip(link, target, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a link")


def parts_of(request: httpx.Request) -> list[email.message.Message]:
    raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    return email.message_from_bytes(raw + request.content).get_payload()


def receipt(core):
    [event] = events(core, "apps.file_sent")
    return event


# --- raw bytes ------------------------------------------------------------------------------


def test_a_file_is_put_as_raw_bytes_with_the_type_the_app_names_and_its_length(
        core, water, server):
    put_file(water, "uploads/a.jpg")
    got = call(core, "water", "net.fetch", {
        "url": f"https://{HOST}/put", "method": "PUT", "file": "uploads/a.jpg",
        "headers": {"Content-Type": "image/jpeg", "x-amz-acl": "private"}})
    assert got["status"] == 200
    [sent] = server
    assert sent.method == "PUT" and sent.content == PICTURE
    assert sent.headers["content-type"] == "image/jpeg"
    assert sent.headers["content-length"] == str(len(PICTURE))
    assert "transfer-encoding" not in sent.headers and sent.headers["x-amz-acl"] == "private"


def test_a_file_sent_with_an_empty_content_type_goes_with_none(core, water, server):
    put_file(water, "uploads/a.jpg")
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                      "file": "uploads/a.jpg",
                                      "headers": {"Content-Type": ""}})
    assert "content-type" not in server[0].headers and server[0].content == PICTURE


def test_a_file_sent_with_no_content_type_goes_as_plain_bytes(core, water, server):
    put_file(water, "notes/out.bin", b"\x00\x01\x02")
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                      "file": "notes/out.bin"})
    assert server[0].headers["content-type"] == "application/octet-stream"
    assert server[0].content == b"\x00\x01\x02"


def test_a_large_file_arrives_whole_and_unchanged(core, water, server):
    big = os.urandom(3 * 1024 * 1024 + 7)
    put_file(water, "uploads/big.jpg", big)
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                      "file": "uploads/big.jpg"})
    assert hashlib.sha256(server[0].content).digest() == hashlib.sha256(big).digest()


def test_the_receipt_names_the_file_and_its_size_and_holds_nothing_of_what_was_sent(
        core, water, server):
    put_file(water, "uploads/a.jpg")
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                      "file": "uploads/a.jpg",
                                      "headers": {"Content-Type": "image/jpeg",
                                                  "Authorization": "Bearer sk-test-x"}})
    event = receipt(core)
    assert event.payload == {
        "app_id": "water", "host": HOST, "method": "PUT", "form": "raw",
        "size": len(PICTURE), "type": "image/jpeg",
        "files": [{"ref": "uploads/a.jpg", "size": len(PICTURE)}]}
    assert event.actor == "app:water" and "sk-test" not in json.dumps(event.payload)


def test_a_fresh_connection_asks_the_host_to_close_it_after_the_upload(core, water, server):
    put_file(water, "uploads/a.jpg")
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                      "file": "uploads/a.jpg", "fresh_connection": True})
    assert server[0].headers["connection"] == "close"


# --- a form ---------------------------------------------------------------------------------


def test_a_form_carries_text_fields_and_file_parts_in_order_and_intact(core, water, server):
    put_file(water, "uploads/a.jpg")
    put_file(water, "uploads/b.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 300)
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/post", "method": "POST",
                                      "multipart": [
                                          {"name": "caption", "value": "Morning light é"},
                                          {"name": "media", "file": "uploads/a.jpg",
                                           "filename": "morning.jpg"},
                                          {"name": "media", "file": "uploads/b.png"}]})
    sent = server[0]
    assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert sent.headers["content-length"] == str(len(sent.content))
    caption, first, second = parts_of(sent)
    assert caption.get_param("name", header="content-disposition") == "caption"
    assert caption.get_payload(decode=True).decode("utf-8") == "Morning light é"
    assert first.get_filename() == "morning.jpg" and first.get_content_type() == "image/jpeg"
    assert first.get_payload(decode=True) == PICTURE
    assert second.get_filename() == "b.png" and second.get_content_type() == "image/png"
    assert second.get_payload(decode=True) == b"\x89PNG\r\n\x1a\n" + b"\x00" * 300
    assert receipt(core).payload["form"] == "multipart"
    assert [f["ref"] for f in receipt(core).payload["files"]] == ["uploads/a.jpg",
                                                                  "uploads/b.png"]


@pytest.mark.parametrize("parts", [
    [], [{"name": "a"}], [{"name": "a", "value": "x", "file": "uploads/a.jpg"}],
    [{"name": 'a"; filename="x', "value": "x"}], [{"name": "a\r\nX: y", "value": "x"}],
    [{"name": "a", "value": 5}], [{"name": "a", "file": "uploads/a.jpg", "type": "bad"}],
    [{"name": "a", "file": "uploads/a.jpg", "filename": 'x".jpg'}],
    [{"name": "a", "file": "uploads/a.jpg", "url": "https://x"}], "caption=x",
])
def test_a_form_written_wrongly_is_refused_and_nothing_is_sent(core, water, server, parts):
    put_file(water, "uploads/a.jpg")
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/post",
                                                 "method": "POST", "multipart": parts})
    assert error.code == "invalid" and "Nothing was sent" in error.message
    assert server == [] and events(core, "apps.file_sent") == []


def test_a_form_sets_its_own_content_type_and_the_app_cannot(core, water, server):
    put_file(water, "uploads/a.jpg")
    error = refused(core, "water", "net.fetch", {
        "url": f"https://{HOST}/post", "method": "POST",
        "headers": {"Content-Type": "multipart/form-data; boundary=x"},
        "multipart": [{"name": "media", "file": "uploads/a.jpg"}]})
    assert error.code == "invalid" and server == []


# --- a file inside JSON ---------------------------------------------------------------------


def test_a_file_marked_in_json_is_filled_in_as_a_data_url_or_as_base64(core, water, server):
    put_file(water, "uploads/a.jpg")
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/post", "method": "POST",
                                      "json": {"caption": "Hi", "nested": [{
                                          "media": {"$file": "uploads/a.jpg",
                                                    "as": "data_url"}}],
                                          "raw": {"$file": "uploads/a.jpg", "as": "base64"},
                                          "after": True}})
    sent = server[0]
    body = json.loads(sent.content)
    assert sent.headers["content-type"] == "application/json"
    assert sent.headers["content-length"] == str(len(sent.content))
    head, _, data = body["nested"][0]["media"].partition(",")
    assert head == "data:image/jpeg;base64" and base64.b64decode(data) == PICTURE
    assert base64.b64decode(body["raw"]) == PICTURE
    assert body["caption"] == "Hi" and body["after"] is True
    assert receipt(core).payload["form"] == "json"


def test_a_data_url_takes_the_type_the_app_names(core, water, server):
    put_file(water, "notes/x.bin", b"abc")
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/post", "method": "POST",
                                      "json": {"f": {"$file": "notes/x.bin", "as": "data_url",
                                                     "type": "text/plain"}}})
    assert json.loads(server[0].content)["f"] == "data:text/plain;base64,YWJj"


def test_json_without_a_file_mark_is_sent_as_before(core, water, server):
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/post", "method": "POST",
                                      "json": {"$file": "x", "note": "not a mark"}})
    assert json.loads(server[0].content) == {"$file": "x", "note": "not a mark"}
    assert events(core, "apps.file_sent") == []


@pytest.mark.parametrize("mark", [{"$file": "uploads/a.jpg"},
                                  {"$file": "uploads/a.jpg", "as": "hex"},
                                  {"$file": "../a.jpg", "as": "base64"},
                                  {"$file": "uploads/a.jpg", "as": "base64", "type": "x"}])
def test_a_json_file_mark_written_wrongly_is_refused(core, water, server, mark):
    put_file(water, "uploads/a.jpg")
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/post",
                                                 "method": "POST", "json": {"f": mark}})
    assert error.code == "invalid" and server == []


# --- what is refused, from the attacker's side ----------------------------------------------


@pytest.mark.parametrize("path", [
    "../secrets.json", "uploads/../../x", "/etc/hosts", "C:/Windows/win.ini",
    "uploads\\a.jpg", "uploads//a.jpg", "./uploads/a.jpg", "con.txt", "uploads/nul",
    "", 5, ["uploads/a.jpg"],
])
def test_a_file_path_that_climbs_out_or_could_mean_something_else_sends_nothing(
        core, water, server, path):
    put_file(water, "uploads/a.jpg")
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put",
                                                 "method": "PUT", "file": path})
    assert error.code == "invalid" and "Nothing was sent" in error.message
    assert server == []


def test_a_file_that_is_a_link_to_outside_the_folder_sends_nothing(core, water, server,
                                                                   tmp_path):
    outside = tmp_path / "private.txt"
    outside.write_bytes(b"not the app's")
    (water / "uploads").mkdir(parents=True)
    link_or_skip(water / "uploads" / "a.jpg", outside)
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put",
                                                 "method": "PUT", "file": "uploads/a.jpg"})
    assert error.code == "not_allowed" and "link" in error.message and server == []


def test_a_folder_on_the_way_that_is_a_link_sends_nothing(core, water, server, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    put_file(elsewhere, "a.jpg")
    water.mkdir(parents=True, exist_ok=True)
    link_or_skip(water / "uploads", elsewhere, directory=True)
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put",
                                                 "method": "PUT", "file": "uploads/a.jpg"})
    assert error.code == "not_allowed" and server == []


@pytest.mark.parametrize("name", ["app.db", "app.db-wal", "App.db", "APP.DB-WAL",
                                  "App.Db-Shm"])
def test_the_cores_own_store_of_the_apps_values_is_never_sent_as_a_file(
        core, water, server, name):
    appdata.open_data(core, "water").set("k", "v")
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put",
                                                 "method": "PUT", "file": name})
    assert error.code == "not_allowed" and server == []


def test_a_file_that_is_not_there_or_is_a_folder_sends_nothing(core, water, server):
    (water / "uploads" / "dir.jpg").mkdir(parents=True)
    for path in ("uploads/missing.jpg", "uploads/dir.jpg"):
        error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put",
                                                     "method": "PUT", "file": path})
        assert error.code == "not_found"
    assert server == []


def test_a_file_for_a_host_that_was_not_granted_is_never_even_opened(
        core, apps, water, server, monkeypatch):
    apps.add(WATER, granted=["net:api.example.com"])
    put_file(water, "uploads/a.jpg")

    def never(*_args, **_kw):
        raise AssertionError("the file was opened")

    monkeypatch.setattr(file_body, "open_file", never)
    for url in (f"https://{HOST}/put", "https://*.example.com/put"):
        error = refused(core, "water", "net.fetch", {"url": url, "method": "PUT",
                                                     "file": "uploads/a.jpg"})
        assert error.code in ("not_allowed", "invalid")
    assert server == [] and events(core, "apps.file_sent") == []


def test_a_file_larger_than_the_setting_is_refused_before_anything_is_sent(
        core, water, server):
    core.settings.set(file_body.MAX_MB, 1, actor="owner")
    put_file(water, "uploads/big.jpg", b"\xff\xd8\xff" + b"x" * (1024 * 1024))
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put",
                                                 "method": "PUT", "file": "uploads/big.jpg"})
    assert error.code == "too_large" and "1 MB" in error.message and server == []


def test_a_whole_request_larger_than_the_setting_is_refused_though_each_file_fits(
        core, water, server):
    core.settings.set(file_body.BODY_MAX_MB, 1, actor="owner")
    put_file(water, "uploads/a.jpg", b"\xff\xd8\xff" + b"x" * 600_000)
    error = refused(core, "water", "net.fetch", {
        "url": f"https://{HOST}/post", "method": "POST",
        "json": {"a": {"$file": "uploads/a.jpg", "as": "base64"},
                 "b": {"$file": "uploads/a.jpg", "as": "base64"}}})
    assert error.code == "too_large" and server == []


@pytest.mark.parametrize("args", [
    {"method": "GET", "file": "uploads/a.jpg"},
    {"method": "DELETE", "file": "uploads/a.jpg"},
    {"method": "PUT", "file": "uploads/a.jpg", "body": "x"},
    {"method": "PUT", "file": "uploads/a.jpg", "json": {"a": 1}},
    {"method": "POST", "file": "uploads/a.jpg",
     "multipart": [{"name": "a", "value": "b"}]},
    {"method": "PUT", "file": "uploads/a.jpg", "fresh_connection": "yes"},
    {"method": "PUT", "file": "uploads/a.jpg", "headers": {"Content-Length": "1"}},
    {"method": "PUT", "file": "uploads/a.jpg", "headers": {"Connection": "keep-alive"}},
])
def test_a_file_fetch_asked_wrongly_is_refused_and_sends_nothing(core, water, server, args):
    put_file(water, "uploads/a.jpg")
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put", **args})
    assert error.code == "invalid" and server == []


def test_a_refused_file_fetch_counts_against_no_rate_and_leaves_no_file_open(
        core, water, server):
    core.settings.set("apps.quota.fetches_per_hour", 1, actor="owner")
    put_file(water, "uploads/a.jpg")
    refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                         "file": "../x"})
    call(core, "water", "net.fetch", {"url": f"https://{HOST}/put", "method": "PUT",
                                      "file": "uploads/a.jpg"})
    error = refused(core, "water", "net.fetch", {"url": f"https://{HOST}/put",
                                                 "method": "PUT", "file": "uploads/a.jpg"})
    assert error.code == "quota" and len(server) == 1
    (water / "uploads" / "a.jpg").unlink()  # nothing holds it open, on Windows too


# --- the rules on their own -----------------------------------------------------------------


def test_base64_length_is_the_length_of_the_encoded_text():
    for size in range(0, 20):
        assert file_body.base64_length(size) == len(base64.b64encode(b"x" * size))


def test_a_file_mark_is_only_an_object_of_its_own_keys():
    assert file_body.is_file_mark({"$file": "a", "as": "base64"})
    assert not file_body.is_file_mark({"$file": "a", "as": "base64", "more": 1})
    assert not file_body.is_file_mark({"file": "a"}) and not file_body.is_file_mark("$file")
