"""Shared by the app install tests: example apps, hand-made apps, and local git
repositories built in a temp folder. Nothing here touches the network."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from jimmyhq.apps import install

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "apps" / "examples"
URL = "https://git.example.com/apps/water.git"


def example(tmp_path: Path, name: str, to: str | None = None) -> Path:
    """A private copy of one example app, free to be changed by a test."""
    dest = tmp_path / "src" / (to or name)
    shutil.copytree(EXAMPLES / name, dest, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    return dest


def manifest_of(folder: Path) -> dict:
    return json.loads((folder / "jimmyhq.app.json").read_text(encoding="utf-8"))


def write_manifest(folder: Path, **changes) -> dict:
    data = manifest_of(folder) | changes
    (folder / "jimmyhq.app.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def expected_tree(folder: Path) -> str:
    """The tree hash worked out by hand, as the design words it."""
    lines = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        lines.append((rel, hashlib.sha256(path.read_bytes()).hexdigest()))
    text = "".join(f"{digest}  {rel}\n" for rel, digest in sorted(lines))
    return hashlib.sha256(text.encode()).hexdigest()


def git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    done = subprocess.run(  # noqa: S603, S607
        ["git", "-c", "user.name=Tester", "-c", "user.email=tester@example.com",
         "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-C", str(repo), *args],
        input=stdin, capture_output=True, check=True, env=env)
    return done.stdout.decode().strip()


def commit_all(repo: Path, message: str = "work") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(folder: Path) -> str:
    """Turn a folder into a git repository with one commit. Returns the commit."""
    git(folder, "init", "--quiet", "--initial-branch=main", ".")
    return commit_all(folder, "first")


def use_local(monkeypatch, repo: Path) -> None:
    """Stand a local repository in for the https address: the address is still checked
    for real, and git is then allowed the file protocol and nothing else."""
    monkeypatch.setattr(install, "_transport", lambda url: (str(repo), "file"))


def staging(core) -> list[Path]:
    base = core.config.data / "apps" / "_staging"
    return sorted(base.iterdir()) if base.is_dir() else []


def events(core, kind: str | None = None):
    found = [e for e in core.tape.after(0, limit=500) if e.kind.startswith("apps.")]
    return [e for e in found if kind is None or e.kind == kind]


def install_folder(core, folder: Path, **confirm) -> dict:
    plan = install.plan_folder(core, folder)
    if plan["needs_typed_name"]:
        confirm.setdefault("typed_name", plan["app"]["name"])
    return install.confirm(core, plan["plan"], plan["fingerprint"], **confirm)
