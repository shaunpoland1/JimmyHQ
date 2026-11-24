"""Who lives in the village, and the commands that change it: birth, retirement and
rolling an agent's memory back.

The web process never touches an agent's folder or body. It writes a row in the
``commands`` table of ``village.db``; the village process, which owns the bodies, picks
it up between beats, runs it with ``handle`` and stores the result. This module keeps
that table and the ``cursors`` table the village process keeps its place on the Tape in
(their shape is declared with the rest of ``village.db`` in ``dbfile.py``).

    <data>/village/agents/<id>/      a living agent's folder
    <data>/village/retired/<id>/     where the folder goes when the agent retires
    <data>/village/shared/<id>/      what the agent shares; coworkers may read it
    <data>/village/shared/left/<id>/ what a retired agent left, read-only
"""

from __future__ import annotations

import json
import logging
import random
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq import storeversion
from jimmyhq.tape.store import connect
from jimmyhq.village import docs, lens, record
from jimmyhq.village.dbfile import FILE_NAME, STORE
from jimmyhq.village.memory import snapshots
from jimmyhq.village.settings_night import night_setting
from jimmyhq.village.world import temperament
from jimmyhq.village.world.state import dump_agent, save_json

if TYPE_CHECKING:
    from jimmyhq.core import Core

log = logging.getLogger(__name__)

BIRTH, RETIRE, ROLLBACK = "birth", "retire", "rollback"
KINDS = (BIRTH, RETIRE, ROLLBACK)
AGENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FOLDERS = ("home/files", "diary", "memory", "impressions", "deliverables", "snapshots")



# --- where things live --------------------------------------------------------


def valid_id(agent_id: Any) -> bool:
    """Is this safe to use as an agent id, and so as a folder name?"""
    return isinstance(agent_id, str) and bool(AGENT_ID.fullmatch(agent_id))


def village_dir(core: Core) -> Path:
    return Path(core.config.data) / "village"


def agent_dir(core: Core, agent_id: str, retired: bool = False) -> Path:
    if not valid_id(agent_id):
        raise ValueError(f"not an agent id: {agent_id!r}")
    return village_dir(core) / ("retired" if retired else "agents") / agent_id


def shared_dir(core: Core) -> Path:
    return village_dir(core) / "shared"


# --- the commands and cursors tables -------------------------------------------


class _Db:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = connect(path)
        with self.lock:
            storeversion.prepare(self.conn, STORE)

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def run(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()


def db(core: Core) -> _Db:
    """This process's handle on the two tables. Creating it creates them."""
    return core.service("village.commands", lambda c: _Db(Path(c.config.data) / FILE_NAME))


def _now_ms() -> int:
    return round(time.time() * 1000)


def _command(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "kind": row["kind"], "agent": row["agent"],
            "payload": json.loads(row["payload"]), "by": row["by"], "ts": row["ts"],
            "done_ts": row["done_ts"],
            "result": json.loads(row["result"]) if row["result"] else None}


def submit(core: Core, kind: str, agent: str | None, payload: dict[str, Any], *,
           by: str) -> str:
    """Ask the village process to do something. Returns the command's id."""
    if kind not in KINDS:
        raise ValueError(f"unknown command kind {kind!r}")
    command_id = uuid.uuid4().hex
    db(core).run('INSERT INTO commands (id, kind, agent, payload, "by", ts) VALUES (?,?,?,?,?,?)',
                 (command_id, kind, agent, json.dumps(payload, ensure_ascii=False), by,
                  _now_ms()))
    return command_id


def get(core: Core, command_id: str) -> dict[str, Any] | None:
    rows = db(core).run("SELECT * FROM commands WHERE id = ?", (command_id,))
    return _command(rows[0]) if rows else None


def pending(core: Core) -> list[dict[str, Any]]:
    """Commands not yet run, oldest first."""
    return [_command(r) for r in db(core).run(
        "SELECT * FROM commands WHERE done_ts IS NULL ORDER BY ts, rowid")]


def finish(core: Core, command_id: str, result: dict[str, Any]) -> None:
    """Store a command's result. A command is finished once; a second call is ignored."""
    db(core).run("UPDATE commands SET done_ts = ?, result = ? WHERE id = ? AND done_ts IS NULL",
                 (_now_ms(), json.dumps(result, ensure_ascii=False, default=str), command_id))


def run_pending(v: Any) -> list[dict[str, Any]]:
    """Run every waiting command, in order, and store each result."""
    done = []
    for command in pending(v.core):
        result = handle(v, command)
        finish(v.core, command["id"], result)
        done.append({**command, "result": result})
    return done


def cursor(core: Core, name: str) -> int:
    """How far along the Tape the named reader has got (0 if it has not started)."""
    rows = db(core).run("SELECT seq FROM cursors WHERE name = ?", (name,))
    return int(rows[0]["seq"]) if rows else 0


def set_cursor(core: Core, name: str, seq: int) -> None:
    """Move a cursor forward. It never moves back."""
    db(core).run("INSERT INTO cursors (name, seq) VALUES (?, ?) ON CONFLICT(name) DO UPDATE "
                 "SET seq = excluded.seq WHERE excluded.seq > cursors.seq", (name, int(seq)))


def rewind_cursor(core: Core, name: str, seq: int) -> None:
    """Put a cursor back to ``seq``. Only for a cursor past the end of the Tape, which
    happens when the Tape was put back from a backup made before the Village's file."""
    db(core).run("UPDATE cursors SET seq = ? WHERE name = ? AND seq > ?",
                 (int(seq), name, int(seq)))


# --- running a command ----------------------------------------------------------


def refused(sentence: str) -> dict[str, Any]:
    return {"ok": False, "error": sentence}


def next_id(taken: set[str]) -> str:
    """The next free id: agent-a to agent-z, then agent-27 and up. An id is never used
    twice, so a retired agent's record is never mistaken for a newcomer's."""
    for letter in "abcdefghijklmnopqrstuvwxyz":
        if f"agent-{letter}" not in taken:
            return f"agent-{letter}"
    n = 27
    while f"agent-{n}" in taken:
        n += 1
    return f"agent-{n}"


def headcount_full(living: int, cap: int) -> bool:
    return living >= cap


def handle(v: Any, command: dict[str, Any]) -> dict[str, Any]:
    """Run one command in the village process and return its result, ``{"ok": ...}``.
    Never raises: a fault is logged and comes back as a refusal the owner can read."""
    kind, payload = command.get("kind"), command.get("payload") or {}
    try:
        if kind == BIRTH:
            return _birth(v, command, payload)
        agent_id = command.get("agent")
        state = lens.read(v.core)
        known = state["agents"].get(agent_id) if valid_id(agent_id) else None
        if known is None or known["retired_ts"] is not None:
            return refused(f"There is no living agent {agent_id!r}. Nothing was changed.")
        if kind == RETIRE:
            return _retire(v, command, agent_id, payload)
        if kind == ROLLBACK:
            return _rollback(v, command, agent_id, payload)
        return refused(f"The village does not know the command {kind!r}. Nothing was changed.")
    except Exception as exc:  # noqa: BLE001  (a bad command must not stop the village)
        log.exception("village command %s failed", kind)
        v.log.append("platform_fault", command.get("agent"), v.clock.now(),
                     what=f"command {kind} failed", detail=f"{type(exc).__name__}: {exc}"[:300])
        return refused(f"The command failed inside the village ({type(exc).__name__}). "
                       "It is in the agent log as a platform fault.")


def _birth(v: Any, command: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    core = v.core
    state = lens.read(core)
    cap = int(core.settings.get("village.headcount_cap"))
    alive = lens.living(state)
    if headcount_full(len(alive), cap):
        return refused(f"The village is full: it holds {len(alive)} agents and the most "
                       f"allowed is {cap}. Nobody was born. Retire an agent, or raise "
                       "\"Most agents at once\" in the settings, then try again.")
    root = Path(v.agent_dir("x")).parent
    taken = set(state["agents"]) | set(v.world.agents)
    for folder in (root, root.parent / "retired"):
        if folder.is_dir():
            taken |= {p.name for p in folder.iterdir()}
    agent_id = next_id(taken)
    seed = payload.get("seed")
    seed = int(seed) if isinstance(seed, int) and not isinstance(seed, bool) \
        else random.SystemRandom().randrange(2**31)
    home = Path(v.agent_dir(agent_id))
    for folder in FOLDERS:
        (home / folder).mkdir(parents=True, exist_ok=True)
    (Path(v.shared_dir) / agent_id).mkdir(parents=True, exist_ok=True)
    body = v.world.add_agent(agent_id, temperament.roll(random.Random(seed)))
    save_json(home / "state.json", dump_agent(body))
    models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
    config = lens.clean_config({"role": payload.get("role"), "beat": models.get("beat"),
                                "night": models.get("night"),
                                "local_only": payload.get("local_only"),
                                "capabilities": payload.get("capabilities")})
    by = command.get("by") or record.SYSTEM
    born = record.born(core, agent_id, seed, config["role"], actor=by)
    record.configured(core, agent_id, config, actor=by, cause=born.id)
    docs.settle_in(v, agent_id)
    tasks = _seed_tasks(v, agent_id, has_coworkers=bool(alive))
    out = {"ok": True, "agent_id": agent_id, "seed": seed, "tasks": tasks}
    # --- roles: an agent made for an app's roles is given them now (apps.roles) ---
    if payload.get("app_roles"):
        from jimmyhq.apps import roles as app_roles

        out["roles"] = app_roles.fill_new_agent(core, agent_id, payload["app_roles"], actor=by)
    return out


def _seed_tasks(v: Any, agent_id: str, has_coworkers: bool) -> int:
    """Give a new agent its first tasks, so purpose has something real to fill it."""
    from jimmyhq.tasks import service
    made = 0
    for task in docs.onboarding(has_coworkers):
        try:
            service(v.core).create(task["title"], actor=record.SYSTEM,
                                   assignee=f"agent:{agent_id}")
            made += 1
        except Exception as exc:  # noqa: BLE001
            v.log.append("platform_fault", agent_id, v.clock.now(),
                         what="onboarding task not made",
                         detail=f"{task['id']}: {type(exc).__name__}: {exc}"[:300])
    return made


def _free(path: Path) -> Path:
    n, out = 2, path
    while out.exists():
        out = path.with_name(f"{path.name}-{n}")
        n += 1
    return out


def _retire(v: Any, command: dict[str, Any], agent_id: str,
            payload: dict[str, Any]) -> dict[str, Any]:
    """Keep what the agent made where coworkers can read it, then archive its folder."""
    home, shared = Path(v.agent_dir(agent_id)), Path(v.shared_dir)
    left = shared / "left" / agent_id
    left.mkdir(parents=True, exist_ok=True)
    copied = []
    for src, name in ((home / "home" / "files", "files"), (shared / agent_id, "shared")):
        if src.is_dir():
            shutil.copytree(src, left / name, dirs_exist_ok=True)
            copied.append(name)
    if (shared / agent_id).is_dir():
        shutil.rmtree(shared / agent_id, ignore_errors=True)
    v.world.agents.pop(agent_id, None)
    v.log.append("retired", agent_id, v.clock.now(), note=str(payload.get("note") or ""))
    archive = home.parent.parent / "retired"
    archive.mkdir(parents=True, exist_ok=True)
    dest = _free(archive / agent_id)
    if home.exists():
        shutil.move(str(home), str(dest))
    record.retired(v.core, agent_id, str(payload.get("note") or "")[:1000],
                   actor=command.get("by") or record.SYSTEM)
    return {"ok": True, "agent_id": agent_id, "left": copied}


def _rollback(v: Any, command: dict[str, Any], agent_id: str,
              payload: dict[str, Any]) -> dict[str, Any]:
    """Put memory back as a snapshot has it. The present state is snapshotted first, so
    the rollback can itself be undone; nothing about an agent changes silently."""
    home = Path(v.agent_dir(agent_id))
    to = payload.get("to")
    if isinstance(to, bool) or not isinstance(to, int) or snapshots.manifest(home, to) is None:
        have = [m["seq"] for m in snapshots.list_all(home)]
        return refused(f"There is no snapshot {to!r} for {agent_id}. Nothing was changed. "
                       + (f"The snapshots kept are {have[0]} to {have[-1]}." if have
                          else "This agent has no snapshots yet."))
    note, diary = str(payload.get("note") or "")[:1000], bool(payload.get("diary"))
    now = v.clock.now()
    undo = snapshots.take(home, round(now * 1000), "before rollback", kept=0, to=to)
    paths = snapshots.restore(home, to, diary=diary)
    snapshots.prune(home, night_setting(v.core, "snapshots_kept"))
    v.log.append("memory_rollback", agent_id, now, to=to, undo=undo, paths=paths, note=note)
    record.restored(v.core, agent_id, to=to, undo=undo, paths=paths, note=note,
                    actor=command.get("by") or record.SYSTEM)
    return {"ok": True, "agent_id": agent_id, "to": to, "undo": undo, "paths": paths}
