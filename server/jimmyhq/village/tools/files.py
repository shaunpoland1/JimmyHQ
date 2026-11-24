"""The file tools: list, read, write, add and edit, confined to the agent's own files and
the shared folders. The rules live in ``village/files.py``."""

from __future__ import annotations

from typing import Any

from jimmyhq.village import files as store
from jimmyhq.village.tools.seams import WEO, category_of, done, flag, refuse_as, text


def _files(ctx: Any) -> store.Files:
    return store.for_agent(ctx.v, ctx.agent_id)


def _path(args: dict[str, Any]) -> str:
    """The path for the log: cleaned when it can be, so one file is one name."""
    try:
        return store.clean(args.get("path"))
    except store.FilesError:
        return str(args.get("path") or "")[:200]


async def list_files(ctx: Any, args: dict[str, Any]) -> Any:
    try:
        result = _files(ctx).list(args.get("path") or "")
    except store.FilesError as e:
        refuse_as(e, {"path": _path(args)})
    return done(result, {"path": _path(args)}, category_of(ctx.mode))


async def read_file(ctx: Any, args: dict[str, Any]) -> Any:
    logged = {"path": _path(args)}
    if args.get("start") not in (None, ""):
        logged["start"] = args["start"]
    try:
        result = _files(ctx).read(args.get("path"), args.get("start"))
    except store.FilesError as e:
        refuse_as(e, logged)
    return done(result, logged, category_of(ctx.mode))


async def write_file(ctx: Any, args: dict[str, Any]) -> Any:
    content, replace = text(args, "content"), flag(args, "replace")
    logged = {"path": _path(args), "content": content}
    if replace:
        logged["replace"] = True
    try:
        result = _files(ctx).write(args.get("path"), content, replace)
    except store.FilesError as e:
        refuse_as(e, logged)
    return done(result, logged, category_of(ctx.mode))


async def add_to_file(ctx: Any, args: dict[str, Any]) -> Any:
    content = text(args, "content")
    logged = {"path": _path(args), "content": content}
    try:
        result = _files(ctx).append(args.get("path"), content)
    except store.FilesError as e:
        refuse_as(e, logged)
    return done(result, logged, category_of(ctx.mode))


async def edit_file(ctx: Any, args: dict[str, Any]) -> Any:
    old, new, every = text(args, "old_text"), text(args, "new_text"), flag(args, "every")
    logged = {"path": _path(args), "old_text": old, "new_text": new}
    if every:
        logged["every"] = True
    try:
        result = _files(ctx).edit(args.get("path"), old, new, every=every)
    except store.FilesError as e:
        refuse_as(e, logged)
    return done(result, logged, category_of(ctx.mode))


_S = {"type": "string"}

SPECS = [
    {"name": "list_files", "kind": "files", "modes": WEO, "run": list_files,
     "description": "List what is in a folder of your files, or of the shared folders "
                    "under shared/.",
     "params": {"path": {"type": "string",
                         "description": "Folder inside your files; empty for the top"}},
     "required": []},
    {"name": "read_file", "kind": "files", "modes": WEO, "run": read_file,
     "description": "Read one of your files, or a file under shared/. A long file's middle "
                    "is left out of the view; give start (a character number) to read "
                    "30,000 characters from there.",
     "params": {"path": _S,
                "start": {"type": "integer",
                          "description": "Optional: the character to start reading from, "
                                         "for the middle of a long file"}},
     "required": ["path"]},
    {"name": "write_file", "kind": "files", "modes": WEO, "run": write_file,
     "description": "Create or replace one of your files. Replacing a file with much "
                    "shorter text needs replace: true (so nothing is lost by accident); to "
                    "add to a file, use add_to_file. Under shared/ you can write only in "
                    "your own folder, shared/<your id>/.",
     "params": {"path": _S, "content": _S,
                "replace": {"type": "boolean",
                            "description": "Yes, replace the file even though the new text "
                                           "is much shorter"}},
     "required": ["path", "content"]},
    {"name": "add_to_file", "kind": "files", "modes": WEO, "run": add_to_file,
     "description": "Add text to the end of one of your files.",
     "params": {"path": _S, "content": _S},
     "required": ["path", "content"]},
    {"name": "edit_file", "kind": "files", "modes": WEO, "run": edit_file,
     "description": "Change one part of one of your files without rewriting it: old_text is "
                    "a passage copied exactly from the file (enough to match one place), "
                    "new_text replaces it. Use an empty new_text to delete the passage.",
     "params": {"path": _S,
                "old_text": {"type": "string",
                             "description": "The exact passage to replace, copied from the "
                                            "file"},
                "new_text": {"type": "string", "description": "What goes in its place"},
                "every": {"type": "boolean",
                          "description": "true to change every place old_text appears, not "
                                         "just one"}},
     "required": ["path", "old_text"]},
]

RULES = [
    ("files_confined", None, store.clean),
    ("shared_write_own_only", None, store.Files.own),
    ("shrink_guard", None, store.shrink_guard),
    ("file_limits", None, store.over_size),
    ("append_duplicate", None, store.append_duplicate),
    ("edit_exact_once", None, store.locate),
    ("edit_already_made", None, store.edit_already_made),
]
