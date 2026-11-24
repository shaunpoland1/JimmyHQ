"""Snapshots: plain copies of an agent's memory, so a bad night can be rolled back.

    <agent folder>/snapshots/<seq>/manifest.json
    <agent folder>/snapshots/<seq>/memory/ impressions/ diary/

One is taken after every sorted night and before every rollback. They are ordinary
folders and files, with no outside program involved. The manifest is written last, so
a folder without one is a copy that was interrupted and is ignored.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from jimmyhq.village.world.state import save_json

FOLDER = "snapshots"
MANIFEST = "manifest.json"
PATHS = ("memory", "impressions", "diary")  # what a snapshot covers
RESTORED = ("memory", "impressions")  # what a rollback puts back unless asked for more
KEPT = 30


class SnapshotError(Exception):
    """A snapshot could not be found or used. The message is written for the owner."""


def _root(agent_dir: Path) -> Path:
    return Path(agent_dir) / FOLDER


def _remove(path: Path) -> None:
    """Remove a folder. Another program may hold a file for a moment, so try again."""
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def manifest(agent_dir: Path, seq: int) -> dict[str, Any] | None:
    path = _root(agent_dir) / str(int(seq)) / MANIFEST
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _numbers(agent_dir: Path) -> list[int]:
    root = _root(agent_dir)
    if not root.exists():
        return []
    return sorted(int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit())


def list_all(agent_dir: Path) -> list[dict[str, Any]]:
    """Every whole snapshot, oldest first."""
    out = []
    for seq in _numbers(agent_dir):
        m = manifest(agent_dir, seq)
        if m is not None:
            out.append(m)
    return out


def prune(agent_dir: Path, kept: int = KEPT) -> list[int]:
    """Remove the oldest snapshots beyond ``kept`` and say which went."""
    whole = [m["seq"] for m in list_all(agent_dir)]
    gone = whole[:-kept] if kept > 0 else []
    for seq in gone:
        _remove(_root(agent_dir) / str(seq))
    return gone


def take(agent_dir: Path, ts_ms: int, reason: str, *, kept: int = KEPT,
         **extra: Any) -> int:
    """Copy memory, impressions and the diary into a new snapshot and return its number.
    ``extra`` goes into the manifest (the day, which model sorted it)."""
    agent_dir = Path(agent_dir)
    numbers = _numbers(agent_dir)
    seq = (numbers[-1] + 1) if numbers else 1
    dest = _root(agent_dir) / str(seq)
    dest.mkdir(parents=True)
    present, files = [], 0
    for name in PATHS:
        src = agent_dir / name
        if src.is_dir():
            shutil.copytree(src, dest / name)
            present.append(name)
            files += sum(1 for p in (dest / name).rglob("*") if p.is_file())
    save_json(dest / MANIFEST, {"seq": seq, "ts": int(ts_ms), "reason": reason,
                                "paths": present, "files": files, **extra})
    prune(agent_dir, kept)
    return seq


def restore(agent_dir: Path, seq: int, *, diary: bool = False) -> list[str]:
    """Put memory and impressions (and the diary, if asked) back as snapshot ``seq`` has
    them, and say which were restored. A folder that did not exist then is removed.
    Take a snapshot first if the present state should be recoverable."""
    agent_dir = Path(agent_dir)
    m = manifest(agent_dir, seq)
    if m is None:
        raise SnapshotError(f"There is no snapshot {seq} for this agent. Nothing was changed.")
    source = _root(agent_dir) / str(int(seq))
    done = []
    for name in RESTORED + (("diary",) if diary else ()):
        live, staged, old = agent_dir / name, agent_dir / f"{name}.restoring", \
            agent_dir / f"{name}.replaced"
        _remove(staged)
        _remove(old)
        if name in m.get("paths", []):
            shutil.copytree(source / name, staged)
        if live.exists():
            live.rename(old)
        if staged.exists():
            staged.rename(live)
        if old.exists() or live.exists():
            done.append(name)
        _remove(old)
    return done
