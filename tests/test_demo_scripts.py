"""The demo and screenshot scripts refuse to touch anything they were not told about, and the
pretend model they run gives answers JimmyHQ can read."""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
import urllib.request
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = _load("_demo_common")
shots = _load("take_screenshots")


def _env(monkeypatch, data=None, port=None):
    for key, value in (("JIMMYHQ_DATA", data), ("JIMMYHQ_PORT", port)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, str(value))


def test_the_demo_will_not_run_without_a_named_data_folder(monkeypatch):
    _env(monkeypatch, None, 8478)
    with pytest.raises(common.Refused, match="JIMMYHQ_DATA"):
        common.environment(fresh=True)


def test_the_demo_will_not_run_without_a_named_port_or_on_the_default_port(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path / "new", None)
    with pytest.raises(common.Refused, match="JIMMYHQ_PORT"):
        common.environment(fresh=True)
    _env(monkeypatch, tmp_path / "new", 8440)
    with pytest.raises(common.Refused, match="8440"):
        common.environment(fresh=True)


def test_the_demo_will_not_fill_a_folder_that_already_holds_something(monkeypatch, tmp_path):
    (tmp_path / "mine.txt").write_text("keep me")
    _env(monkeypatch, tmp_path, 8478)
    with pytest.raises(common.Refused, match="not empty"):
        common.environment(fresh=True)
    assert (tmp_path / "mine.txt").read_text() == "keep me"


def test_the_demo_fills_a_folder_that_does_not_exist_or_is_empty(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path / "later", 8478)
    assert common.environment(fresh=True) == ((tmp_path / "later").resolve(), 8478)
    (tmp_path / "empty").mkdir()
    _env(monkeypatch, tmp_path / "empty", 8478)
    assert common.environment(fresh=True)[1] == 8478


def test_screenshots_are_taken_only_of_a_folder_the_demo_filled(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, 8478)
    with pytest.raises(common.Refused, match="demo_data.py"):
        common.environment(fresh=False)
    (tmp_path / common.MARKER).write_text(common.MARKER_TEXT)
    assert common.environment(fresh=False)[0] == tmp_path.resolve()


def test_the_folder_jimmyhq_uses_by_default_is_never_accepted(monkeypatch):
    from platformdirs import user_data_dir

    _env(monkeypatch, user_data_dir("JimmyHQ", False), 8478)
    with pytest.raises(common.Refused, match="your own data"):
        common.environment(fresh=True)


def test_a_json_answer_asked_for_is_the_smallest_value_that_fits_its_schema():
    schema = {"type": "object", "required": ["facts", "note", "n", "kind"], "properties": {
        "facts": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"},
        "n": {"type": "integer"}, "kind": {"enum": ["a", "b"]}}}
    assert common.empty_for(schema) == {"facts": [], "note": "", "n": 0, "kind": "a"}


def test_the_pretend_model_lists_its_model_and_answers_a_question_the_same_way_each_time():
    with common.pretend_model(_free_port()) as base:
        listed = json.load(urllib.request.urlopen(base + "/models"))
        assert listed["data"][0]["id"] == common.MODEL_NAME
        body = json.dumps({"model": "m", "messages": [
            {"role": "user", "content": "Is the boiler service due?"}]}).encode()

        def ask():
            request = urllib.request.Request(
                base + "/chat/completions", body, {"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(request))["choices"][0]["message"]["content"]

        assert ask() == ask()
        assert "boiler" in ask()


def test_a_turn_that_must_call_a_tool_is_answered_with_a_diary_entry():
    with common.pretend_model(_free_port()) as base:
        body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "go"}],
                           "tools": [{"type": "function", "function": {
                               "name": "write_diary", "parameters": {}}}]}).encode()
        request = urllib.request.Request(
            base + "/chat/completions", body, {"Content-Type": "application/json"})
        message = json.load(urllib.request.urlopen(request))["choices"][0]["message"]
        call = message["tool_calls"][0]["function"]
        assert call["name"] == "write_diary"
        assert json.loads(call["arguments"])["text"]


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_recompressed_picture_has_the_same_pixels_and_no_extra_chunks():
    width, height = 4, 3
    pixels = bytes((x * 7 + y * 13) % 251 for y in range(height) for x in range(width * 3))
    raw = b"".join(b"\x00" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body)))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"tEXt", b"Comment\0hi")
           + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))
    smaller = shots.recompress(png)
    assert b"tEXt" not in smaller
    start = smaller.index(b"IDAT") + 4
    size = struct.unpack(">I", smaller[start - 8:start - 4])[0]
    assert zlib.decompress(smaller[start:start + size]) == raw


def test_the_screenshot_list_has_every_name_other_pages_rely_on():
    names = {s.name for s in shots.SHOTS}
    assert {"today", "bench", "tape", "tasks", "chat", "village", "memory", "apps", "settings",
            "wall", "app-books", "app-posts", "app-heard", "app-imagery", "app-packages",
            "today-light", "bench-light", "phone-today", "phone-bench"} <= names


def test_an_app_can_ask_for_pictures_of_its_own_in_a_file_in_its_folder(tmp_path):
    folder = tmp_path / "plant_care"
    folder.mkdir()
    (folder / "screenshots.json").write_text(json.dumps([
        {"name": "app-plant_care", "route": "/app/plant_care/plant_care", "dock": None,
         "frame_steps": ["press('Earlier')"]}]), encoding="utf-8")
    (tmp_path / "other").mkdir()
    found = shots.app_shots(tmp_path)
    assert [(s.name, s.dock, s.frame_steps) for s in found] == [
        ("app-plant_care", None, ["press('Earlier')"])]
    assert shots.app_shots(tmp_path / "nowhere") == []


