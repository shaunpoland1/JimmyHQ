"""``jimmyhq settings``: read and change settings from a terminal or a script.

These commands open the same stores a running server does and write ordinary
``setting.updated`` events on the Tape as the owner, so they work whether or
not JimmyHQ is running. SQLite lets several processes write one Tape, and a
running server reads the Tape's newest events on every request, so it sees a
change from here on its next read without a restart.

Output is plain: one setting per line, values as JSON. Anything refused is
explained on stderr and the exit status is 1; a file that cannot be read is 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from jimmyhq.settings import (
    MAX_FILE_BYTES,
    Plan,
    Setting,
    SettingError,
    SettingsFileError,
    declared,
)

if TYPE_CHECKING:
    from jimmyhq.core import Core

ACTOR = "owner"
REFUSED = 1
UNREADABLE = 2


def add_parser(sub: Any) -> None:
    """Register ``settings`` and its sub-commands on the main parser."""
    parser = sub.add_parser(
        "settings", help="read, change, export and import settings",
        description="Read and change settings, or move them in and out as JSON files. "
                    "Works whether or not JimmyHQ is running.")
    parser.set_defaults(settings_parser=parser)
    commands = parser.add_subparsers(dest="settings_command", metavar="command")
    listing = commands.add_parser("list", help="show every setting and its value")
    listing.add_argument("--changed", action="store_true", help="only settings that are not at "
                         "their default")
    get = commands.add_parser("get", help="print one setting's value")
    get.add_argument("key")
    set_ = commands.add_parser(
        "set", help="change a setting (the value is JSON; plain text is fine for text)")
    set_.add_argument("key")
    set_.add_argument("value")
    reset = commands.add_parser("reset", help="put a setting back to its default")
    reset.add_argument("key")
    export = commands.add_parser("export", help="write settings as a JSON file")
    export.add_argument("--profile", action="store_true",
                        help="leave out personal and machine-specific settings, to share")
    export.add_argument("--changed", action="store_true", help="only settings that differ "
                        "from their default")
    export.add_argument("--name", default="", help="a name for a shared profile")
    export.add_argument("--description", default="", help="what a shared profile is for")
    export.add_argument("-o", "--output", default="-", help="the file to write (default: print)")
    load = commands.add_parser("import", help="apply a settings file")
    load.add_argument("file")
    load.add_argument("--dry-run", action="store_true", help="show what would change, change "
                      "nothing")
    load.add_argument("--skip-invalid", action="store_true",
                      help="apply what is valid and leave out the entries that are refused")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _shown(value: Any) -> str:
    """A value for ``get``: text as it is, anything else as JSON."""
    return value if isinstance(value, str) else _json(value)


def parse_value(spec: Setting, text: str) -> Any:
    """Read the value typed on the command line.

    It is JSON, so ``true``, ``12`` and ``["a","b"]`` mean what they look like.
    For text and choices, anything that is not JSON text is taken as it is, so
    ``set appearance.theme dark`` works without quotes.
    """
    try:
        value = json.loads(text)
    except ValueError:
        value = text
        if spec.type not in ("str", "choice"):
            raise SettingError(
                f"{spec.label} takes {_kind(spec)}, but {text!r} is not valid JSON. "
                f"Try something like {_example(spec)}."
            ) from None
    if spec.type in ("str", "choice") and not isinstance(value, str):
        value = text  # 123 or true typed for a text setting is the text "123" or "true"
    return value


def _kind(spec: Setting) -> str:
    return {"bool": "true or false", "int": "a whole number", "float": "a number",
            "list": "a JSON list"}.get(spec.type, "text")


def _example(spec: Setting) -> str:
    if spec.type == "bool":
        return "true"
    if spec.type in ("int", "float"):
        return _json(spec.default)
    if spec.type == "list":
        return "'[\"first\",\"second\"]'"
    return "plain text"


def _spec(key: str) -> Setting:
    for spec in declared():
        if spec.key == key:
            return spec
    raise SettingError(
        f"There is no setting called {key!r}. Run `jimmyhq settings list` to see them all.")


def _print_plan(plan: Plan, out: TextIO) -> None:
    if plan.name:
        out.write(f"Profile: {plan.name}\n")
    if plan.description:
        out.write(f"{plan.description}\n")
    for entry in plan.changes:
        target = "default" if entry.reset else _json(entry.new)
        out.write(f"change  {entry.key}: {_json(entry.old)} -> {target}\n")
    for entry in plan.refused:
        out.write(f"refused {entry.key}: {entry.reason}\n")
    verb = "changed" if plan.applied else "would change"
    out.write(f"{len(plan.changes)} {verb}, {len(plan.unchanged)} already as the file says, "
              f"{len(plan.refused)} refused.\n")


def run(args: argparse.Namespace, core: Core, out: TextIO, err: TextIO) -> int:
    """Run one ``settings`` command against an open core. Returns the exit status."""
    settings = core.settings
    command = args.settings_command
    try:
        if command == "list":
            for spec in declared():
                value = settings.get(spec.key)
                if args.changed and value == spec.default:
                    continue
                out.write(f"{spec.key} = {_json(value)}\n")
            return 0
        if command == "get":
            _spec(args.key)
            out.write(_shown(settings.get(args.key)) + "\n")
            return 0
        if command == "set":
            value = settings.set(args.key, parse_value(_spec(args.key), args.value), actor=ACTOR)
            out.write(f"{args.key} = {_json(value)}\n")
            return 0
        if command == "reset":
            _spec(args.key)
            value = settings.reset(args.key, actor=ACTOR)
            out.write(f"{args.key} = {_json(value)} (the default)\n")
            return 0
        if command == "export":
            document = settings.export(
                portable_only=args.profile, changed_only=args.changed,
                name=args.name.strip(), description=args.description.strip())
            text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            if args.output == "-":
                out.write(text)
            else:
                Path(args.output).write_text(text, encoding="utf-8")
                out.write(f"Wrote {len(document['settings'])} settings to {args.output}\n")
            return 0
        if command == "import":
            return _import(args, core, out, err)
    except SettingError as exc:
        err.write(f"{exc}\n")
        return REFUSED
    except OSError as exc:
        err.write(f"Could not use the file: {exc.strerror or exc}. Check the path and try again.\n")
        return UNREADABLE
    err.write("Say what to do: list, get, set, reset, export or import. "
              "Try `jimmyhq settings --help`.\n")
    return UNREADABLE


def _import(args: argparse.Namespace, core: Core, out: TextIO, err: TextIO) -> int:
    path = Path(args.file)
    if path.stat().st_size > MAX_FILE_BYTES:
        err.write(f"{path} is larger than {MAX_FILE_BYTES // 1024} KB, which is too big for a "
                  "settings file. Check that it is the right file.\n")
        return UNREADABLE
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, RecursionError) as exc:
        err.write(f"{path} is not valid JSON ({exc}). Check it for a missing comma or quote.\n")
        return UNREADABLE
    try:
        if args.dry_run:
            plan = core.settings.plan(document)
        else:
            plan = core.settings.apply(document, actor=ACTOR, skip_invalid=args.skip_invalid)
    except SettingsFileError as exc:
        err.write(f"{exc}\n")
        return REFUSED
    _print_plan(plan, out)
    if plan.refused and not (args.skip_invalid and not args.dry_run):
        if args.dry_run:
            err.write("An import would be refused as it stands. Fix those entries, or import "
                      "with --skip-invalid to leave them out.\n")
        else:
            err.write("Nothing was changed. Fix those entries, or import again with "
                      "--skip-invalid to leave them out.\n")
        return REFUSED
    return 0


def main_for(args: argparse.Namespace, core: Core) -> int:
    return run(args, core, sys.stdout, sys.stderr)
