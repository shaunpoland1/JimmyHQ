"""Writing an app: a skeleton to start from, the store's checks, and a development loop.

    jimmyhq apps new <id> [--tier full|sandboxed] [--folder <where>]
    jimmyhq apps check <folder>
    jimmyhq apps dev <folder>

**new** writes a working skeleton into a new folder: the manifest, a page, and for a full
app a program with a job, a Bench card type and a call for its page, and a test that runs
it against ``jimmyhq_app.testing.FakeCore``. Nothing is installed.

**check** runs the checks an app must pass before it is offered anyone: the manifest and
every file it names, the size and shape of the folder (no links, nothing leaving it), and
for a full app that its Python reads (each file is compiled, never run) and that it ships
its locked dependencies. Each finding is a sentence, marked as a problem (the app cannot
be installed as it is) or a warning.

**dev** installs the folder, once the owner has agreed in the terminal, marks it
"Development" (its files are served uncached), and then looks at the folder every second.
When something changes it checks it again and puts the new copy in place the way an
update does (``update.switch``), even under the same version number: its data, secrets,
roles, device keys and everything else the owner gave it stay; the supervisor starts the
new program, and its open pages load again. A change that asks for more than the owner
agreed to stops the loop and says so: run ``apps dev`` again to be asked. Each new copy
is recorded as an update (``apps.updated``), its data copied first like any other.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jimmyhq.apps import manifest as mf
from jimmyhq.apps import store
from jimmyhq.apps.permissions import FULL, SANDBOXED, fingerprint

if TYPE_CHECKING:
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.dev")

DEV_FILE = "dev.json"
POLL_S = 1.0
_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}\Z")


@dataclass(frozen=True)
class Finding:
    level: str  # "problem": it cannot be installed as it is; "warning": it can, but look
    where: str  # the field or file, or "" for the app as a whole
    sentence: str

    def line(self) -> str:
        mark = "Problem" if self.level == "problem" else "Warning"
        return f"{mark}{f' ({self.where})' if self.where else ''}: {self.sentence}"


# --- check -----------------------------------------------------------------------------------


def check_manifest(folder: Path) -> tuple[mf.Manifest | None, list[Finding]]:
    """The manifest and every file it names, each mistake its own finding."""
    try:
        return mf.load(folder), []
    except mf.ManifestError as error:
        return None, [Finding("problem", p.field, p.sentence) for p in error.problems]


def check_tree(core: Core, folder: Path) -> list[Finding]:
    """The folder as the installer would take it: within the size limits, with no links
    and nothing outside it."""
    try:
        store.hash_folder(folder, store.limits(core))
    except store.AppError as error:
        return [Finding("problem", "", error.detail)]
    return []


def check_python(folder: Path, m: mf.Manifest) -> list[Finding]:
    """Every Python file of a full app reads: compiled to check it, never run."""
    if m.tier != FULL:
        return []
    out = []
    for path in sorted(folder.rglob("*.py")):
        rel = path.relative_to(folder).as_posix()
        if any(part.startswith(".") or part in ("__pycache__", ".venv") for part in
               path.relative_to(folder).parts):
            continue
        try:
            compile(path.read_bytes(), rel, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as error:
            line = getattr(error, "lineno", None)
            out.append(Finding("problem", rel, f"This Python file does not read"
                                               f"{f' at line {line}' if line else ''}: "
                                               f"{getattr(error, 'msg', error)}. Correct it."))
    return out


def check_dependencies(folder: Path, m: mf.Manifest) -> list[Finding]:
    """A full app that needs more than the SDK ships ``pyproject.toml`` and ``uv.lock``,
    so exactly the locked packages are installed."""
    if m.tier != FULL:
        return []
    has_project = (folder / "pyproject.toml").is_file()
    has_lock = (folder / "uv.lock").is_file()
    if has_project and not has_lock:
        return [Finding("problem", "uv.lock", "pyproject.toml is there but uv.lock is not, so "
                                              "its packages could not be installed exactly. "
                                              "Run \"uv lock\" in the app's folder.")]
    if not has_project:
        return [Finding("warning", "pyproject.toml", "There is no pyproject.toml, so the app "
                                                     "runs with only what JimmyHQ itself has "
                                                     "(the SDK, httpx and pydantic). Add one "
                                                     "with a uv.lock if it needs more.")]
    return []


def check_screens(m: mf.Manifest) -> list[Finding]:
    """An app the owner cannot see or use does nothing they can tell."""
    if m.tier == FULL and not (m.screens or m.dock or m.wall or m.cards or m.tools
                               or m.memory_sources):
        return [Finding("warning", "screens", "The app adds no screen, card type, tool or "
                                              "memory source, so the owner will see nothing "
                                              "of it but its entry on the Apps screen.")]
    return []


def check(core: Core, folder: str | Path) -> list[Finding]:
    """Every check, in order. An empty list means it passes."""
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        return [Finding("problem", "", f"There is no folder at {folder.name or folder}. Point "
                                       "at the folder that holds jimmyhq.app.json.")]
    m, found = check_manifest(folder)
    found.extend(check_tree(core, folder))
    if m is not None:
        for rule in (check_python, check_dependencies):
            found.extend(rule(folder, m))
        found.extend(check_screens(m))
    return found


def passes(findings: list[Finding]) -> bool:
    return not any(f.level == "problem" for f in findings)


# --- new -------------------------------------------------------------------------------------


def check_new_id(app_id: Any) -> str:
    if not isinstance(app_id, str) or not _ID.match(app_id):
        raise ValueError("An app's id is 2 to 31 lower-case letters, digits or underscores, "
                         "starting with a letter, such as \"plant_care\". Nothing was written.")
    return app_id


def skeleton(app_id: str, tier: str) -> dict[str, str]:
    """The files of a new app, by path inside its folder."""
    name = app_id.replace("_", " ").capitalize()
    manifest: dict[str, Any] = {
        "manifest": 1, "id": app_id, "name": name, "summary": f"What {name} does, in a line.",
        "author": "", "licence": "MIT", "version": "0.1.0", "tier": tier, "api": "1.0",
        "permissions": [],
        "screens": [{"id": "main", "label": name, "entry": "web/index.html"}],
    }
    page = ("<!doctype html><meta charset=\"utf-8\"><title>" + name + "</title>\n"
            "<h1>" + name + "</h1>\n<ul id=\"list\"></ul>\n"
            "<script type=\"module\" src=\"app.js\"></script>\n")
    files: dict[str, str] = {"web/index.html": page}
    if tier == SANDBOXED:
        files["web/app.js"] = (
            'import { hq } from "./_bridge.js";\n'
            "await hq.ready;\n"
            'const count = (await hq.storage.get("opened")) ?? 0;\n'
            'await hq.storage.set("opened", count + 1);\n'
            'const item = document.createElement("li");\n'
            "item.textContent = `Opened ${count + 1} times.`;\n"
            'document.getElementById("list").append(item);\n')
    else:
        manifest |= {
            "permissions": ["bench.file", "jobs"],
            "settings": [{"key": "check_minutes", "type": "int", "default": 60, "min": 5,
                          "label": "Check every (minutes)", "help": "How often it checks."}],
            "server": {"entry": f"{app_id}.main:app", "python": ">=3.12"},
            "cards": [{"kind": "check", "label": "Check"}],
        }
        files[f"{app_id}/__init__.py"] = ""
        files[f"{app_id}/main.py"] = (
            f'"""{name}: a job puts a card on the Bench, and the owner\'s answer is kept."""\n'
            "\n"
            "from jimmyhq_app import App\n"
            "\n"
            "app = App()\n"
            "\n"
            "\n"
            "def db():\n"
            "    conn = app.db()\n"
            '    conn.execute("CREATE TABLE IF NOT EXISTS answer (card TEXT PRIMARY KEY, '
            'action TEXT)")\n'
            "    return conn\n"
            "\n"
            "\n"
            '@app.job("check", every_setting="check_minutes")\n'
            "def check():\n"
            '    return app.bench.file("check", "Is everything all right?", actions=[\n'
            '        {"id": "yes", "label": "Yes", "style": "primary", "closes": True},\n'
            '        {"id": "later", "label": "Ask me later", "closes": True}])\n'
            "\n"
            "\n"
            '@app.on_decision("check")\n'
            "def decided(decision):\n"
            "    conn = db()\n"
            '    conn.execute("INSERT OR REPLACE INTO answer VALUES (?, ?)",\n'
            "                 (decision.card_id, decision.action))\n"
            "    conn.commit()\n"
            '    app.emit("answers.changed")\n'
            "\n"
            "\n"
            '@app.call("answers")\n'
            "def answers(args):\n"
            '    return [list(row) for row in db().execute("SELECT card, action FROM answer")]\n'
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            "    app.run()\n")
        files["web/app.js"] = (
            'import { hq } from "./_bridge.js";\n'
            "await hq.ready;\n"
            'const list = document.getElementById("list");\n'
            "async function show() {\n"
            '  const rows = await hq.app.call("answers", {});\n'
            "  list.replaceChildren(...rows.map(([card, action]) => {\n"
            '    const item = document.createElement("li");\n'
            "    item.textContent = `${card}: ${action}`;\n"
            "    return item;\n"
            "  }));\n"
            "}\n"
            'hq.on("answers.changed", show);\n'
            "show();\n")
        files[f"tests/test_{app_id}.py"] = (
            "from jimmyhq_app import App  # noqa: F401 - made inside the fake core\n"
            "from jimmyhq_app.testing import FakeCore\n"
            "\n"
            "\n"
            "def test_a_check_files_a_card_and_the_answer_is_kept(tmp_path):\n"
            f'    core = FakeCore("{app_id}", data_dir=tmp_path,\n'
            '                    permissions=["bench.file", "jobs"])\n'
            "    with core.installed():\n"
            f"        from {app_id} import main\n"
            "\n"
            '        card = core.run_job("check")\n'
            '        core.decide(card, "yes")\n'
            '        assert core.call("answers") == [[card, "yes"]]\n')
    files[mf.MANIFEST_FILE] = json.dumps(manifest, indent=2) + "\n"
    return files


def new(app_id: str, folder: str | Path, *, tier: str = FULL) -> Path:
    """Write a new app's skeleton into ``folder`` (which must not exist yet)."""
    check_new_id(app_id)
    if tier not in (FULL, SANDBOXED):
        raise ValueError("The tier is \"full\" or \"sandboxed\". Nothing was written.")
    where = Path(folder).expanduser()
    if where.exists():
        raise FileExistsError(f"{where.name} is there already, so nothing was written. Choose "
                              "a new folder.")
    for rel, text in skeleton(app_id, tier).items():
        path = where / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return where


# --- dev -------------------------------------------------------------------------------------


def dev_file(core: Core) -> Path:
    return store.root(core) / DEV_FILE


def development(core: Core) -> dict[str, str]:
    """The apps linked by ``apps dev``: app id -> the folder it is developed in."""
    try:
        found = json.loads(dev_file(core).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(found, dict) else {}


def is_development(core: Core, app_id: str) -> bool:
    return app_id in development(core)


def _mark(core: Core, app_id: str, folder: Path | None) -> None:
    from jimmyhq.apps.runtime import write_json  # noqa: PLC0415

    found = development(core)
    if folder is None:
        found.pop(app_id, None)
    else:
        found[app_id] = str(folder)
    write_json(dev_file(core), found)


def snapshot(folder: Path) -> tuple[tuple[str, int, int], ...]:
    """What the folder holds now: each file's path, size and time of change."""
    out = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        try:
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                out.append((rel.as_posix(), stat.st_size, stat.st_mtime_ns))
        except OSError:
            continue
    return tuple(out)


def same_consent(old: mf.Manifest, new_: mf.Manifest) -> bool:
    """A new copy may be put in place without asking only when it is the same app asking
    for the same things."""
    return old.id == new_.id and old.tier == new_.tier and fingerprint(old) == fingerprint(new_)


class ConsentNeeded(Exception):
    """The folder now asks for more than the owner agreed to."""


def install_copy(core: Core, folder: Path, *, replacing: mf.Manifest | None,
                 agree: Callable[[dict[str, Any]], str | None]) -> mf.Manifest:
    """Put the folder's current copy in place. The first time, ``agree(plan)`` asks the
    owner and answers the name they typed ("" for a plain yes) or None for no. After
    that, a copy that asks for the same things replaces the last one (``replace_copy``)."""
    from jimmyhq.apps import install  # noqa: PLC0415

    m = mf.load(folder)
    if replacing is not None:
        if not same_consent(replacing, m):
            raise _consent_needed()
        return replace_copy(core, folder, m)
    plan = install.plan_folder(core, folder)
    typed: str | None = m.name
    if replacing is None:
        typed = agree(plan)
        if typed is None:
            install.cancel(core, plan["plan"])
            raise ConsentNeeded("Nothing was installed.")
    try:
        install.confirm(core, plan["plan"], plan["fingerprint"],
                        typed_name=typed if plan["needs_typed_name"] else None)
    except BaseException:
        install.cancel(core, plan["plan"])
        raise
    _mark(core, m.id, folder)
    return m


def _consent_needed() -> ConsentNeeded:
    return ConsentNeeded(
        "The app now asks for something you have not agreed to (its id, tier, permissions "
        "or what its agent tools may do changed), so the new copy was not put in place. "
        "Run \"jimmyhq apps dev\" again to be asked.")


def replace_copy(core: Core, folder: Path, m: mf.Manifest) -> mf.Manifest:
    """Rule ``a_dev_copy_is_an_update``: put a new copy of an app under development in
    place as an update is (``update.confirm_update``), not by removing and installing it
    again, so its secrets, roles, device keys, hosts, plugs and data all stay. It may keep
    the version number it had. A copy whose files did not change is left as it is; one
    that asks for anything the owner has not agreed to is refused, and nothing changes."""
    from jimmyhq.apps import install, update  # noqa: PLC0415 - they import this package

    row = store.need(core, m.id)
    plan = update.plan_folder(core, m.id, folder, development=True)
    if plan["update"]["needs_consent"] or plan["needs_typed_name"]:
        install.cancel(core, plan["plan"])
        raise _consent_needed()
    if (plan["source"]["tree"], plan["app"]["version"]) == (row["tree"], row["version"]):
        install.cancel(core, plan["plan"])
        return m
    try:
        update.confirm_update(core, plan["plan"], plan["fingerprint"])
    except BaseException:
        install.cancel(core, plan["plan"])
        raise
    _mark(core, m.id, folder)
    return m


def watch(core: Core, folder: str | Path, *, agree: Callable[[dict[str, Any]], str | None],
          say: Callable[[str], None] = print, stop: threading.Event | None = None,
          poll_s: float = POLL_S) -> int:
    """The development loop. Returns 0 when stopped, 1 when it could not go on."""
    folder = Path(folder).expanduser().resolve()
    stop = stop or threading.Event()
    findings = check(core, folder)
    for finding in findings:
        say(finding.line())
    if not passes(findings):
        say("Nothing was installed. Correct the problems above and run it again.")
        return 1
    from jimmyhq.apps.store import AppError  # noqa: PLC0415

    try:
        current = install_copy(core, folder, replacing=None, agree=agree)
    except (ConsentNeeded, AppError) as error:
        say(getattr(error, "detail", None) or str(error))
        return 1
    say(f"{current.name} is installed for development. Watching {folder.name} for changes; "
        "stop with Ctrl+C.")
    seen = snapshot(folder)
    try:
        while not stop.wait(poll_s):
            now = snapshot(folder)
            if now == seen:
                continue
            seen = now
            findings = check(core, folder)
            if not passes(findings):
                for finding in findings:
                    say(finding.line())
                say("The copy that is running was left as it was.")
                continue
            try:
                current = install_copy(core, folder, replacing=current, agree=agree)
            except ConsentNeeded as error:
                say(str(error))
                return 1
            except AppError as error:
                say(error.detail)
                continue
            say(f"Put the new copy of {current.name} in place.")
    finally:
        _mark(core, current.id, None)
    return 0
