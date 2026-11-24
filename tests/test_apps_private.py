"""A private app's store, proven by the bytes: a marker the app keeps privately is
searched for in everything that leaves its own folder (the Tape, Memory, the Vault, the
settings export, another app's answers) and is found in none of them.

The marker is made at run time, so it is in no file of the repo, and the search is over
the raw bytes of each file, so no reader can hide it."""

from __future__ import annotations

import base64
import sqlite3
import sys
from pathlib import Path

import pytest

from apps_full_helpers import AppClient, bearer, client_for, install_full
from apps_install_helpers import install_folder
from jimmyhq.apps import install, store
from jimmyhq.apps.manifest import ManifestError, parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app.app import PrivateDB  # noqa: E402

PASSWORD = "correct horse battery"


def marker() -> str:
    return "zq" + "private" + "marker" + "8842"


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def diary(core, tmp_path):
    install_full(core, tmp_path, "diary", private=True,
                 permissions=["tasks.write", "bench.file", "memory.recall"])
    return "diary"


@pytest.fixture
def nosy(core, tmp_path):
    install_full(core, tmp_path, "nosy", permissions=["tape.read", "tasks.read"])
    return "nosy"


def speak(web, core, app_id: str) -> AppClient:
    return AppClient(web, core.auth.mint_app_token(app_id))


def files_holding(folder: Path, needle: bytes, *, skip: tuple[Path, ...] = ()) -> list[Path]:
    """Every file under ``folder`` whose bytes hold ``needle``, outside ``skip``."""
    found = []
    for path in folder.rglob("*"):
        if not path.is_file() or any(path.is_relative_to(s) for s in skip):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if needle in data:
            found.append(path)
    return found


# --- the private store itself ---------------------------------------------------------------


def test_a_purge_leaves_no_trace_of_the_rows_in_the_database_or_its_log(tmp_path):
    secret = marker()
    db = PrivateDB(tmp_path / "journal.db")
    db.execute("CREATE TABLE entry (id INTEGER PRIMARY KEY, body TEXT)")
    for n in range(200):
        db.execute("INSERT INTO entry (body) VALUES (?)", (f"{secret} number {n}",))
    db.commit()
    db.execute("INSERT INTO entry (body) VALUES ('kept')")
    db.commit()
    assert db.purge("entry", "body LIKE ?", (f"{secret}%",)) == 200
    assert [r[0] for r in db.execute("SELECT body FROM entry")] == ["kept"]
    db.close()
    assert files_holding(tmp_path, secret.encode()) == []


def test_a_private_database_is_opened_with_secure_delete():
    db = PrivateDB(Path(":memory:"))
    assert db.execute("PRAGMA secure_delete").fetchone()[0] == 1
    db.close()


def test_a_purge_will_not_take_a_table_name_that_is_not_one(tmp_path):
    db = PrivateDB(tmp_path / "journal.db")
    with pytest.raises(ValueError):
        db.purge("entry; DROP TABLE x", "1=1")
    db.close()


# --- the private refusals -----------------------------------------------------------------


def test_a_private_app_puts_nothing_in_the_vault(web, core, diary):
    error = speak(web, core, diary).refused(
        "vault.put", {"base64": base64.b64encode(marker().encode()).decode()})
    assert error["code"] == "not_allowed" and "Nothing was written" in error["message"]


def test_a_private_apps_task_has_a_title_only(web, core, diary):
    app = speak(web, core, diary)
    error = app.refused("tasks.create", {"title": "Write", "notes": marker()})
    assert error["code"] == "invalid" and "Nothing was added" in error["message"]
    assert app.ok("tasks.create", {"title": "Write"})["title"] == "Write"


def test_a_private_apps_question_to_memory_leaves_no_trace_on_its_receipt(web, core, diary):
    speak(web, core, diary).ok("memory.recall", {"query": marker()})
    receipt = [e for e in core.tape.after(0, limit=1000) if e.kind == "memory.recalled"][-1]
    assert receipt.payload["query_chars"] == 0 and receipt.payload["query_hash"] == ""


def test_a_private_app_cannot_ask_to_offer_tools_to_agents():
    data = {"manifest": 1, "id": "hush", "name": "Hush", "summary": "A test.", "licence": "MIT",
            "version": "1.0.0", "tier": "full", "api": "1.0", "permissions": ["agents.tools"],
            "server": {"entry": "hush_main:app"}, "private": True,
            "tools": [{"name": "read", "description": "Read it.", "params": {},
                       "required": [], "effect": "read"}]}
    with pytest.raises(ManifestError) as caught:
        parse(data)
    assert any(p.field == "private" and "agents" in p.sentence for p in caught.value.problems)


def test_an_update_that_drops_the_private_promise_needs_consent(core, tmp_path, diary):
    from apps_full_helpers import full_app
    from jimmyhq.apps import update

    folder = full_app(tmp_path / "next", "diary", version="1.1.0",
                      permissions=["tasks.write", "bench.file", "memory.recall"])
    plan = update.plan_folder(core, diary, folder)
    assert plan["update"]["needs_consent"] is True
    assert [line["id"] for line in plan["update"]["ask"]] == ["private.off"]
    with pytest.raises(store.AppError):
        update.confirm_update(core, plan["plan"], plan["fingerprint"], actor="system")


# --- what leaves, searched byte by byte --------------------------------------------------------


def test_nothing_a_private_app_keeps_reaches_anything_outside_its_own_folder(
        core, tmp_path, web, diary, nosy):
    secret = marker()
    needle = secret.encode()
    app = speak(web, core, diary)
    app.ok("storage.set", {"key": "entry", "value": f"Today: {secret}"})
    db = PrivateDB(store.data_dir(core, diary) / "journal.db")
    db.execute("CREATE TABLE IF NOT EXISTS entry (body TEXT)")
    db.execute("INSERT INTO entry VALUES (?)", (secret,))
    db.commit()
    db.close()
    # Every way out it might try is refused, and says so.
    assert app.refused("tape.print", {"kind": "noted", "payload": {"t": secret}})["code"] \
        == "not_allowed"
    assert app.refused("bench.file", {"kind": "draft", "title": "New entry",
                                      "body": secret})["code"] == "invalid"
    assert app.refused("tasks.create", {"title": "Write", "notes": secret})["code"] == "invalid"
    assert app.refused("vault.put", {"base64": base64.b64encode(needle).decode()})["code"] \
        == "not_allowed"
    app.ok("memory.recall", {"query": secret})
    app.ok("bench.file", {"kind": "draft", "title": "New entry"})

    # Another app, even one that may read the whole record, gets none of it.
    other = speak(web, core, nosy)
    answers = [
        other.call("tape.read", {"after": 0, "limit": 500}),
        other.call("storage.get", {"key": "entry"}),
        other.call("storage.keys", {}),
        other.call("tasks.list", {}),
    ]
    assert other.ok("storage.get", {"key": "entry"}) is None
    for answer in answers:
        assert secret not in str(answer)
    token = core.auth.mint_app_token(nosy)
    for path in ("/api/apps/diary/bridge/storage.get", "/api/apps/diary"):
        stolen = web.post(path, json={"args": {"key": "entry"}}, headers=bearer(token))
        assert stolen.status_code in (401, 405) and secret not in stolen.text

    # The owner's export of settings carries none of it.
    assert web.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    exported = web.get("/api/settings/export")
    assert exported.status_code == 200 and secret not in exported.text
    assert secret not in web.get("/api/apps/_installed").text

    # And byte by byte: the Tape, Memory, the Vault, the logs and every other store.
    data = Path(core.config.data)
    own = store.app_dir(core, diary)
    assert files_holding(own, needle)  # it is really there, so the search can find it
    assert files_holding(data, needle, skip=(own,)) == []


def test_removing_a_private_app_with_its_data_deleted_leaves_its_marker_nowhere(
        core, web, diary):
    secret = marker()
    speak(web, core, diary).ok("storage.set", {"key": "entry", "value": secret})
    db = PrivateDB(store.data_dir(core, diary) / "journal.db")
    db.execute("CREATE TABLE entry (body TEXT)")
    db.execute("INSERT INTO entry VALUES (?)", (secret,))
    db.commit()
    db.close()
    install.remove(core, diary, keep_data=False)
    assert files_holding(Path(core.config.data), secret.encode()) == []


def test_a_private_apps_data_is_kept_on_remove_by_default_and_found_again(core, web, tmp_path,
                                                                          diary):
    from apps_full_helpers import full_app

    speak(web, core, diary).ok("storage.set", {"key": "entry", "value": "kept"})
    install.remove(core, diary)
    install_folder(core, full_app(tmp_path / "again", "diary", private=True,
                                  permissions=["tasks.write", "bench.file", "memory.recall"]))
    assert speak(web, core, diary).ok("storage.get", {"key": "entry"}) == "kept"


def test_the_private_store_is_plain_sqlite_that_any_tool_can_read_back(tmp_path):
    db = PrivateDB(tmp_path / "plain.db")
    db.execute("CREATE TABLE t (v TEXT)")
    db.execute("INSERT INTO t VALUES ('x')")
    db.commit()
    db.close()
    with sqlite3.connect(tmp_path / "plain.db") as conn:
        assert conn.execute("SELECT v FROM t").fetchall() == [("x",)]
