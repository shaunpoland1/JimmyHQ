"""Pictures the owner gives an app: kept in its own folder, checked by their bytes, held to
the limits, and nothing left behind when one is refused."""

from __future__ import annotations

import urllib.parse

import pytest
from fastapi.testclient import TestClient

from jimmyhq.apps import appdata, bridge, quotas, uploads
from jimmyhq.apps.uploads import UploadRefused
from jimmyhq.settings import SettingError
from jimmyhq.web.app import create_app
from test_apps_bridge_fakes import BOOKS, WATER, call, events, installs, refused

PASSWORD = "correct horse battery"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(256)) * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture
def apps(monkeypatch, tmp_path):
    made = installs(monkeypatch, tmp_path)
    made.add(WATER)
    made.add(BOOKS)
    return made


@pytest.fixture
def web(core, apps):
    with TestClient(create_app(core)) as client:
        assert client.post("/api/auth/setup", json={"name": "Sam",
                                                    "password": PASSWORD}).status_code == 200
        yield client


def give(web, data: bytes = JPEG, name: str = "morning.jpg", app_id: str = "water",
         **headers):
    sent = {"x-jimmyhq-file-name": urllib.parse.quote(name), "content-type": "image/jpeg"}
    return web.post(f"/api/apps/{app_id}/files", content=data, headers=sent | headers)


def kept(core, app_id: str = "water") -> list[str]:
    folder = appdata.data_dir(core, app_id) / "uploads"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


# --- the owner gives a picture ----------------------------------------------------------------


def test_a_picture_is_kept_in_the_apps_own_folder_and_the_page_is_told_its_ref(web, core):
    answer = give(web)
    assert answer.status_code == 200, answer.text
    got = answer.json()
    assert set(got) == {"ref", "name", "size", "type"}
    assert got["name"] == "morning.jpg" and got["size"] == len(JPEG)
    assert got["type"] == "image/jpeg" and got["ref"].startswith("uploads/")
    uploads.check_ref(got["ref"])
    assert (appdata.data_dir(core, "water") / got["ref"]).read_bytes() == JPEG


def test_the_receipt_says_where_and_how_large_and_not_the_pictures_own_name(web, core):
    got = give(web, name="holiday at grandmas.jpg").json()
    [event] = events(core, "apps.file_received")
    assert event.payload == {"app_id": "water", "ref": got["ref"], "size": len(JPEG),
                             "type": "image/jpeg"}
    assert "grandma" not in str(event.payload) and event.subject == "app:water"


def test_what_a_picture_is_comes_from_its_bytes_not_its_name_or_declared_type(web, core):
    got = give(web, PNG, name="looks-like.jpg", **{"content-type": "image/jpeg"}).json()
    assert got["type"] == "image/png" and got["ref"].endswith(".png")


def test_a_picture_sent_in_pieces_with_no_length_is_kept_whole(web, core):
    def pieces():
        for start in range(0, len(JPEG), 7):
            yield JPEG[start:start + 7]

    got = give(web, pieces()).json()
    assert (appdata.data_dir(core, "water") / got["ref"]).read_bytes() == JPEG


def test_the_apps_program_and_its_fetches_can_use_the_ref(web, core, apps):
    got = give(web).json()
    apps.add(WATER, granted=["net:uploads.example.com"])
    error = refused(core, "water", "net.fetch", {"url": "https://uploads.example.com/x",
                                                 "method": "PUT", "file": got["ref"] + "x"})
    assert error.code == "not_found"  # a ref is checked like any path; the real one opens
    from jimmyhq.apps import file_body
    body = file_body.raw_body(core, "water", got["ref"], {})
    try:
        assert body.length == len(JPEG)
    finally:
        body.close()


# --- what is refused, and that nothing is left ------------------------------------------------


@pytest.mark.parametrize("data", [
    b"<html><script>alert(1)</script></html>",
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>x</script></svg>",
    b"%PDF-1.7 not a picture",
    b"MZ\x90\x00 a program",
    b"\xff\xd8",  # too short to be a JPEG
    b"",
])
def test_a_file_that_claims_to_be_a_picture_and_is_not_is_refused_and_not_kept(
        web, core, data):
    answer = give(web, data, name="photo.jpg", **{"content-type": "image/jpeg"})
    assert answer.status_code == 415 and "nothing was kept" in answer.json()["detail"]
    assert kept(core) == [] and events(core, "apps.file_received") == []


def test_a_picture_over_the_size_setting_is_refused_and_nothing_is_left(web, core):
    core.settings.set(uploads.MAX_MB, 1, actor="owner")
    big = JPEG + b"\x00" * (1024 * 1024)
    answer = give(web, big)
    assert answer.status_code == 413 and "1 MB" in answer.json()["detail"]

    def pieces():
        yield big[:500_000]
        yield big[500_000:]

    assert give(web, pieces()).status_code == 413
    assert kept(core) == []


def test_a_picture_that_would_go_over_the_apps_storage_is_refused_and_nothing_is_left(
        web, core):
    core.settings.set(quotas.OVERRIDES, ["water.storage_mb=1"], actor="owner")
    assert give(web).status_code == 200
    answer = give(web, JPEG + b"\x00" * (1024 * 1024))
    assert answer.status_code == 507 and "1 MB" in answer.json()["detail"]
    assert len(kept(core)) == 1


def test_pictures_count_towards_the_storage_the_apps_values_may_use(web, core):
    core.settings.set(quotas.OVERRIDES, ["water.storage_mb=1"], actor="owner")
    assert give(web, JPEG + b"\x00" * 900_000).status_code == 200
    error = refused(core, "water", "storage.set", {"key": "k", "value": "x" * 200_000})
    assert error.code == "quota"


@pytest.mark.parametrize("name", ["../../evil.jpg", "a/b.jpg", "a\\b.jpg", "C:\\x.jpg",
                                  "a\x00.jpg", "a\nb.jpg", "%ff%fe.jpg"])
def test_a_name_with_a_path_separator_or_a_control_character_is_refused(web, core, name):
    answer = web.post("/api/apps/water/files", content=JPEG,
                      headers={"x-jimmyhq-file-name": urllib.parse.quote(name, safe="%")})
    assert answer.status_code == 422 and kept(core) == []


@pytest.mark.parametrize("name, shown", [
    ('a:b?"c".jpg', "a_b__c_.jpg"), ("con.jpg", "_con.jpg"), ("LPT1", "_LPT1"),
    ("dots...", "dots"), ("  spaced.jpg  ", "spaced.jpg"), ("café.jpg", "café.jpg"),
    ("", "picture"), ("x" * 300 + ".jpg", "x" * 120),
])
def test_a_name_windows_cannot_hold_is_made_one_it_can_and_never_used_as_a_path(
        web, core, name, shown):
    got = give(web, name=name).json()
    assert got["name"] == shown
    assert kept(core) == [got["ref"].removeprefix("uploads/")]


def test_a_picture_for_an_app_that_is_not_installed_or_is_off_is_refused(web, core, apps):
    assert give(web, app_id="nobody").status_code == 404
    apps.add(WATER, enabled=False)
    assert give(web).status_code == 403
    assert kept(core) == []


@pytest.mark.parametrize("headers", [
    {"sec-fetch-dest": "iframe"}, {"sec-fetch-site": "cross-site"}, {"origin": "null"},
])
def test_a_frame_or_another_site_cannot_send_a_picture_itself(web, core, headers):
    assert give(web, **headers).status_code == 403 and kept(core) == []


def test_a_token_cannot_send_or_read_a_picture(web, core):
    ref = give(web).json()["ref"]
    secret = core.auth.create_token("scout", ["read", "write"])[1]
    with TestClient(create_app(core), headers={"authorization": f"Bearer {secret}"}) as other:
        assert give(other).status_code == 403
        assert other.get(f"/api/apps/water/files/{ref.removeprefix('uploads/')}"
                         ).status_code == 403


def test_only_one_picture_at_a_time_is_received_for_one_app():
    uploads._claim("water")  # noqa: SLF001
    try:
        with pytest.raises(UploadRefused) as caught:
            uploads._claim("water")  # noqa: SLF001
        assert caught.value.status == 409
        uploads._claim("books_read")  # noqa: SLF001 - another app is not held up
        uploads._release("books_read")  # noqa: SLF001
    finally:
        uploads._release("water")  # noqa: SLF001


# --- showing a picture ------------------------------------------------------------------------


def test_the_owners_page_is_shown_the_picture_as_an_image_and_nothing_more(web, core):
    ref = give(web).json()["ref"]
    answer = web.get(f"/api/apps/water/files/{ref.removeprefix('uploads/')}")
    assert answer.status_code == 200 and answer.content == JPEG
    assert answer.headers["content-type"] == "image/jpeg"
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in answer.headers["content-security-policy"]


def test_a_picture_is_shown_only_for_its_own_app_and_only_by_its_ref(web, core):
    ref = give(web).json()["ref"]
    file = ref.removeprefix("uploads/")
    for path in (f"/api/apps/books_read/files/{file}", "/api/apps/water/files/app.db",
                 f"/api/apps/water/files/{file[:-4]}.svg", "/api/apps/water/files/..%2fapp.db",
                 f"/api/apps/water/files/{file.upper()}", "/api/apps/nobody/files/" + file):
        assert web.get(path).status_code == 404, path


def test_a_frame_cannot_load_a_picture_by_its_address(web, core):
    file = give(web).json()["ref"].removeprefix("uploads/")
    for headers in ({"sec-fetch-dest": "iframe"}, {"sec-fetch-site": "cross-site"},
                    {"origin": "null"}):
        assert web.get(f"/api/apps/water/files/{file}", headers=headers).status_code == 403


def test_a_picture_that_was_swapped_for_a_link_is_not_shown(web, core, tmp_path):
    import os
    ref = give(web).json()["ref"]
    path = appdata.data_dir(core, "water") / ref
    path.unlink()
    (tmp_path / "secret.txt").write_text("not a picture")
    try:
        os.symlink(tmp_path / "secret.txt", path)
    except (OSError, NotImplementedError):
        pytest.skip("this system does not let a test make a link")
    assert web.get(f"/api/apps/water/files/{ref.removeprefix('uploads/')}").status_code == 404


# --- the rules on their own -------------------------------------------------------------------


@pytest.mark.parametrize("head, ending", [
    (JPEG, "jpg"), (PNG, "png"), (b"GIF89a" + b"\x00" * 20, "gif"),
    (b"GIF87a" + b"\x00" * 20, "gif"), (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
    (b"\x00\x00\x00\x1cftypavif\x00\x00", "avif"), (b"\x00\x00\x00\x18ftypheic", "heic"),
    (b"<svg></svg>", None), (b"RIFF\x00\x00\x00\x00WAVE", None),
    (b"\x00\x00\x00\x18ftypmp42", None), (b"", None),
])
def test_sniff_image_reads_the_kind_of_picture_from_its_first_bytes(head, ending):
    assert uploads.sniff_image(head) == ending


@pytest.mark.parametrize("ref", ["uploads/" + "a" * 32 + ".jpg", "uploads/" + "0" * 32 + ".heic"])
def test_check_ref_takes_a_ref_an_upload_hands_out(ref):
    assert uploads.check_ref(ref) == ref


@pytest.mark.parametrize("ref", ["uploads/../app.db", "uploads/" + "a" * 32 + ".svg",
                                 "/uploads/" + "a" * 32 + ".jpg", "a" * 32 + ".jpg",
                                 "uploads/" + "A" * 32 + ".jpg", None, 5])
def test_check_ref_refuses_anything_else(ref):
    with pytest.raises(UploadRefused):
        uploads.check_ref(ref)


def test_the_mount_answer_tells_the_shell_the_largest_picture(core, apps):
    from jimmyhq.apps import files
    core.settings.set(uploads.MAX_MB, 7, actor="owner")
    (apps.apps["water"].folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    assert files.mint_mount(core, "water")["upload_max_mb"] == 7


def test_the_mount_answer_tells_the_shell_how_small_a_thumbnail_is(core, apps):
    from jimmyhq.apps import files
    (apps.apps["water"].folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    assert files.mint_mount(core, "water")["thumbnail_px"] == 320
    core.settings.set("apps.thumbnail_px", 128, actor="owner")
    assert files.mint_mount(core, "water")["thumbnail_px"] == 128


def test_a_thumbnail_size_outside_the_limits_is_refused(core):
    for bad in (10, 5000):
        with pytest.raises(SettingError):
            core.settings.set("apps.thumbnail_px", bad, actor="owner")
    assert core.settings.get("apps.thumbnail_px") == 320


def test_the_mount_answer_tells_the_shell_the_limits_of_a_full_view(core, apps):
    from jimmyhq.apps import files
    (apps.apps["water"].folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    made = files.mint_mount(core, "water")
    assert (made["picture_px"], made["picture_max_mb"], made["pictures_per_minute"]) == (
        2048, 25, 60)
    assert made["thumbnail_px"] == 320  # the small version is not changed by the full view
    core.settings.set("apps.picture_px", 4096, actor="owner")
    core.settings.set("apps.picture_max_mb", 10, actor="owner")
    core.settings.set("apps.pictures_per_minute", 5, actor="owner")
    made = files.mint_mount(core, "water")
    assert (made["picture_px"], made["picture_max_mb"], made["pictures_per_minute"]) == (
        4096, 10, 5)


def test_a_full_view_size_outside_the_limits_is_refused(core):
    for key, bad in (("apps.picture_px", 10), ("apps.picture_px", 9000),
                     ("apps.picture_max_mb", 0), ("apps.picture_max_mb", 201),
                     ("apps.pictures_per_minute", 0), ("apps.pictures_per_minute", 601)):
        with pytest.raises(SettingError):
            core.settings.set(key, bad, actor="owner")
    assert core.settings.get("apps.picture_px") == 2048


def test_storage_held_counts_pictures_and_values_together(core, apps):
    appdata.open_data(core, "water").set("k", "v" * 1000)
    folder = uploads.uploads_dir(core, "water")
    (folder / ("0" * 32 + ".jpg")).write_bytes(b"x" * 5000)
    left, whole = uploads.room(core, bridge.find_app(core, "water"))
    assert whole - left >= 6000
    assert call(core, "water", "storage.get", {"key": "k"}) == "v" * 1000
