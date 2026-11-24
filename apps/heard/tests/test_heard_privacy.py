"""The owner's rules: audio is never kept or fetched, none of this enters the record, and a
model reads only at home. Tested from the side of someone trying to find it elsewhere."""

from __future__ import annotations

import re
from pathlib import Path

from heard_rig import CHAT, PLAN, conversation

APP = Path(__file__).resolve().parents[1]
CODE = sorted((APP / "heard").glob("*.py"))
SENTINEL = "quincunx"
AUDIO_URL = "https://audio.example.com/recordings/abc123.wav"
AUDIO_B64 = "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA="

# Every call an app can make of the platform that reaches the record, the Bench, Memory,
# the network, another app or an agent.
FORBIDDEN = ("tape.", "bench.", "memory.", "net.", "feed.", "vault.", "web.", "secrets.",
             "storage.", "records.", "files.", "hosts.", "ui.", "app.emit", "ladder.",
             "roles.", "work.", "model.embed", "tasks.")


def run_everything(rig):
    rig.send(conversation(transcript_segments=[
        {"text": f"My {SENTINEL} is private. {PLAN}", "speaker": "SPEAKER_00", "is_user": True,
         "start": 0, "end": 2}]))
    rig.send({"session_id": "s", "segments": CHAT})
    rig.send({"text": "A single line"})
    rig.send({"bad": "message"})
    cid = rig.call("search", query="private")["results"][0]["conv_id"]
    rig.core.replies.append("A summary.")
    rig.summarise(cid)
    rig.answer({"text": "Send Sam the notes", "quote": "I will send Sam the notes"})
    for todo in rig.todos(cid)["todos"]:
        rig.call("todo.accept", id=todo["id"])
    rig.call("speaker.rename", id=cid, **{"from": "You", "to": "Alex"})
    rig.call("conversation.text", id=cid)
    for part in rig.call("export.parts")["parts"]:
        rig.call("export.part", index=part["index"])
    rig.call("overview")
    rig.core.run_job("retention")


def test_a_full_run_makes_no_tape_bench_memory_net_task_or_feed_call(make_rig):
    # Adding an accepted to-do to the owner's tasks is the one thing the owner's own click
    # may do by default; with that switched off, nothing at all reaches anywhere else.
    rig = make_rig(settings={"file_tasks": False})
    run_everything(rig)
    rig.call("delete.all", typed="DELETE")
    assert set(rig.calls) <= {"settings.get", "model.ask"}
    assert not [c for c in rig.calls if c.startswith(FORBIDDEN)]
    assert rig.wherever_it_could_be_written() == repr([[], {}, [], [], {}, {}, []])
    assert SENTINEL not in rig.wherever_it_could_be_written()


def test_audio_in_a_message_is_never_kept_and_its_address_is_never_fetched(rig):
    segment = {**CHAT[0], "audio_url": AUDIO_URL, "audio": AUDIO_B64}
    rig.send(conversation(audio_url=AUDIO_URL, audio_base64=AUDIO_B64,
                          transcript_segments=[segment, CHAT[1]]))
    rig.send({"session_id": "s", "audio_url": AUDIO_URL, "segments": [segment]})
    rig.send({"text": "A line", "audio_url": AUDIO_URL, "audio": AUDIO_B64})
    rows = repr([rig.ctx.store.all(f"SELECT * FROM {t}")  # noqa: S608
                 for t in ("conversation", "segment", "todo", "meta", "refused")])
    assert AUDIO_URL not in rows and AUDIO_B64 not in rows and "audio" not in rows.lower()
    on_disk = b"".join(p.read_bytes() for p in rig.data.glob("heard.db*"))
    assert AUDIO_B64.encode() not in on_disk and b"abc123" not in on_disk
    assert rig.core.fetched == [] and not [c for c in rig.calls if c.startswith(("net.", "web."))]


def test_a_message_that_is_only_audio_is_refused_and_nothing_is_fetched(rig):
    rig.send({"audio_url": AUDIO_URL, "audio": AUDIO_B64, "duration": 12})
    rig.send({"transcript_segments": [{"audio_url": AUDIO_URL, "start": 0, "end": 3}]})
    assert rig.call("days")["conversations"] == []
    assert {r["kind"] for r in rig.call("overview")["refused"]} == {"unknown_shape", "no_words"}
    assert rig.core.fetched == [] and rig.calls == [c for c in rig.calls if c == "settings.get"]


def test_the_program_has_no_way_to_reach_the_network_or_read_a_file_a_message_names():
    text = "\n".join(p.read_text(encoding="utf-8") for p in CODE)
    for needle in ("httpx", "requests", "urllib", "socket", "aiohttp", "subprocess",
                   ".fetch(", "net.fetch", "web.read", "web.search", "urlopen"):
        assert needle not in text, needle
    assert not re.search(r"(?<![\w.])open\(", text) and "read_bytes" not in text
    # the only calls out to JimmyHQ's app API are the model and (when asked) one task
    called = set(re.findall(r"app\.(model|tasks|bench|tape|memory|net|web|vault|feeds)\.", text))
    assert called == {"model", "tasks"}


def test_a_messages_words_never_reach_a_log_line(rig, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    rig.send({"bad": f"my {SENTINEL} is private"})
    rig.send({"text": f"my {SENTINEL} is private", "started_ms": "not a time"})
    rig.core.replies.append("Fine.")
    rig.summarise(rig.first_id())
    assert SENTINEL not in caplog.text


def test_every_question_to_a_model_must_stay_on_the_owners_machines_and_off_the_record(rig):
    rig.send(conversation())
    rig.core.replies.append("A summary.")
    rig.summarise(rig.first_id())
    rig.answer()
    rig.todos(rig.first_id())
    assert len(rig.core.asked) == 2
    assert all(a["stay_local"] is True and a["off_record"] is True for a in rig.core.asked)


def test_the_app_is_private_so_it_can_neither_offer_memory_a_source_nor_agents_a_tool():
    import json
    manifest = json.loads((APP / "jimmyhq.app.json").read_text(encoding="utf-8"))
    assert manifest["private"] is True
    for key in ("memory_sources", "tools", "feeds", "cards", "roles", "secrets", "owner_hosts"):
        assert key not in manifest, key
    assert not {"memory.source", "agents.tools", "tape.read", "bench.file"} & set(
        manifest["permissions"])
