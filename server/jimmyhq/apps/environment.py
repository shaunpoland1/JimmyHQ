"""A full app's own Python environment, built from exactly what it locked.

A full app that ships ``pyproject.toml`` and ``uv.lock`` gets an environment of its own at
``<data>/apps/<id>/venvs/<version>-<hash8>/``, made after the owner agreed and before the
version is put in use::

    uv sync --frozen --no-dev --no-build --no-install-project --project <version folder>

with ``UV_PROJECT_ENVIRONMENT`` set to that folder. ``--frozen`` installs exactly the locked
hashes; ``--no-build`` takes wheels only, so no build script of any library runs;
``--no-install-project`` leaves the app itself out (it runs from its version folder, and
building it would be a build script too). On Windows ``UV_LINK_MODE=copy``.

``uv`` is found from the setting ``apps.uv_path``, then ``PATH``. Without it the app runs
with JimmyHQ's own interpreter, as it did before, and the Apps screen says so in a
sentence (``note``). An environment is never changed in place: each version has its own,
made once into a folder that did not exist, and deleted when that version is.

The program that runs ``uv`` is ``runner`` (``subprocess.run`` by default), so tests stand
a fake in for it and nothing reaches the network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import runtime, store
from jimmyhq.apps.store import AppError

if TYPE_CHECKING:
    from jimmyhq.core import Core

LOCK_FILES = ("pyproject.toml", "uv.lock")
NO_UV = ("{name} runs with JimmyHQ's own Python, because uv was not found on this "
         "computer, so the libraries it locked are not installed. Install uv, or give its "
         "full path in Settings, Apps, then update or install the app again.")
NOT_BUILT = ("{name}'s own libraries are not installed, so it runs with JimmyHQ's own "
             "Python. Update or install the app again to install them.")
_ENV_KEEP = ("PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
             "LOCALAPPDATA", "PROGRAMDATA", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
             "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
             "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "https_proxy", "NO_PROXY",
             "no_proxy", "ALL_PROXY", "all_proxy", "XDG_CACHE_HOME", "UV_CACHE_DIR")

# Stood in for by tests: (argv, cwd=, env=, timeout=, capture_output=, check=, ...) -> done.
runner: Callable[..., Any] = subprocess.run


def needs_env(folder: Path) -> bool:
    """Whether a version ships what an environment is built from."""
    return all((Path(folder) / name).is_file() for name in LOCK_FILES)


def venv_dir(core: Core, app_id: str, version_name: str) -> Path:
    return store.app_dir(core, app_id) / "venvs" / version_name


def uv_program(core: Core) -> str | None:
    """The uv to use: the one the owner named, if it is there, otherwise the one on PATH."""
    chosen = str(core.settings.get("apps.uv_path") or "").strip()
    if chosen and Path(chosen).is_file():
        return chosen
    return shutil.which("uv")


def uv_command(uv: str, folder: Path) -> list[str]:
    """The command line: a program and its arguments, never a shell."""
    return [uv, "sync", "--frozen", "--no-dev", "--no-build", "--no-install-project",
            "--project", str(folder)]


def uv_env(venv: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """A rebuilt environment: nothing of JimmyHQ's own, and nothing that names another
    project or environment."""
    base = os.environ if base is None else base
    env = {name: base[name] for name in _ENV_KEEP if name in base}
    env |= {"UV_PROJECT_ENVIRONMENT": str(venv), "UV_NO_PROGRESS": "1"}
    if sys.platform == "win32":
        env["UV_LINK_MODE"] = "copy"
    return env


def build(core: Core, app_id: str, name: str, version_name: str, folder: Path) -> dict[str, Any]:
    """Build the environment for one version, if it ships a lock file and has none yet.

    Returns ``{"built": bool, "note": sentence | None}``: ``note`` says why the app will run
    with JimmyHQ's own Python. A failed build raises ``AppError`` and leaves nothing
    behind."""
    if not needs_env(folder):
        return {"built": False, "note": None}
    venv = venv_dir(core, app_id, version_name)
    if runtime.venv_python(venv).is_file():
        return {"built": True, "note": None}  # made before, and never changed in place
    uv = uv_program(core)
    if uv is None:
        return {"built": False, "note": NO_UV.format(name=name)}
    if venv.exists():
        store.delete_tree(venv)  # half-made by an earlier try
    venv.parent.mkdir(parents=True, exist_ok=True)
    timeout = float(core.settings.get("apps.uv_timeout_s"))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = runner(uv_command(uv, folder), cwd=str(folder), env=uv_env(venv),
                      stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                      creationflags=flags, check=False)
    except subprocess.TimeoutExpired:
        store.delete_tree(venv)
        raise AppError(504, (
            f"Installing the libraries {name} needs took longer than {int(timeout)} seconds, "
            "so JimmyHQ stopped and nothing was changed. Check your connection, or allow "
            "longer in Settings, Apps.")) from None
    except OSError:
        store.delete_tree(venv)
        raise AppError(503, (
            f"JimmyHQ could not start uv to install the libraries {name} needs, so nothing "
            "was changed. Check the path of uv in Settings, Apps.")) from None
    if done.returncode != 0 or not runtime.venv_python(venv).is_file():
        store.delete_tree(venv)
        said = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        last = (said[-1] if said else "nothing")[:200]
        raise AppError(502, (
            f"JimmyHQ could not install the libraries {name} locked, so nothing was "
            "changed. The app's author may need to lock wheels for this computer. uv said: "
            f"{last}"))
    return {"built": True, "note": None}


def note(core: Core, app_id: str, name: str, version_name: str, folder: Path) -> str | None:
    """Why this version runs with JimmyHQ's own Python, for the Apps screen, or None."""
    if not needs_env(folder) or runtime.venv_python(venv_dir(core, app_id, version_name)).is_file():
        return None
    return (NO_UV if uv_program(core) is None else NOT_BUILT).format(name=name)
