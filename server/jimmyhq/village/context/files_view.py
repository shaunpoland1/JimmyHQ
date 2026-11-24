"""The agent's files and folders, shown as they are on disk right now.

With only a one-line memory of each action an agent rereads its own notes again and
again, or rewrites a file from a partial memory of it and loses what was there. Showing
the real, current text every beat removes both. Every rule here decides which files stay
in view and how much of each is shown; none of them hides anything silently.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from jimmyhq.village.rules import rule

READ_WINDOW = 30_000  # what one read_file with start returns
FILE_TOOLS = ("read_file", "write_file", "add_to_file", "edit_file")


# ------------------------------------------------------------------ the disk
def resolve(v: Any, agent_id: str, path: Any) -> Path | None:
    """Where a path the agent used is on disk: its own files, or ``shared/...``. None
    for a path that would leave them."""
    rel = PurePosixPath(str(path or "").replace("\\", "/").strip().lstrip("/"))
    if ".." in rel.parts:
        return None
    if rel.parts[:1] == ("shared",):
        return v.shared_dir.joinpath(*rel.parts[1:])
    return v.files_dir(agent_id).joinpath(*rel.parts)


def read_text(v: Any, agent_id: str, path: Any) -> str | None:
    p = resolve(v, agent_id, path)
    if p is None or not p.is_file():
        return None
    try:
        return p.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
    except OSError:
        return None


def listing(v: Any, agent_id: str, path: Any) -> list[str] | None:
    p = resolve(v, agent_id, path)
    if p is None or not p.is_dir():
        return None
    try:
        entries = sorted(p.iterdir(), key=lambda e: e.name)
        return [f"{e.name}/" if e.is_dir() else f"{e.name} ({e.stat().st_size} bytes)"
                for e in entries] or ["(empty)"]
    except OSError:
        return None


def _path_of(event: Any) -> str:
    return str((event.data.get("args") or {}).get("path", "") or "").strip()


# -------------------------------------------------------------- which files
@rule("working_files", "village.context.working_files")
def recent_paths(evs: list, n: int) -> list[str]:
    """The files last read or changed, newest first."""
    paths: list[str] = []
    for e in reversed(evs):
        if e.data.get("ok") and e.data.get("tool") in FILE_TOOLS:
            path = _path_of(e)
            if path and path not in paths:
                paths.append(path)
        if len(paths) >= n:
            break
    return paths


@rule("just_read_whole", "village.context.just_read_files")
def just_read(evs: list, window: int, n: int) -> list[str]:
    """Files read within the repeat window. Reading one again would count as a repeat
    and cost the agent, so for exactly that long it stays whole and in view."""
    out: list[str] = []
    for e in reversed(evs[-window:]):
        if e.data.get("ok") and e.data.get("tool") == "read_file":
            path = _path_of(e)
            if path and path not in out:
                out.append(path)
    return out[:n]


@rule("returning_file", "village.context.returning_reads")
def returning(evs: list, reads_needed: int, n: int, max_chars: int, shown: list[str],
              size_of: Any) -> list[str]:
    """Short files the agent keeps coming back to (a plan, a queue). Without this a
    plan drops out of view a few actions after each read and is read again all day."""
    reads: dict[str, int] = {}
    for e in evs:
        if e.data.get("ok") and e.data.get("tool") == "read_file" and _path_of(e):
            reads[_path_of(e)] = reads.get(_path_of(e), 0) + 1
    kept: list[str] = []
    for path, count in sorted(reads.items(), key=lambda kv: -kv[1]):
        if len(kept) >= n or count < reads_needed:
            break
        if path in shown:
            continue
        size = size_of(path)
        if size is None or size > max_chars:
            continue
        kept.append(path)
    return kept


@rule("focus_stays")
def focus_points(evs: list, window: int, text_of: Any) -> dict[str, int]:
    """Where in each file the agent last looked or changed something, inside the repeat
    window: the start of a partial read, or where an edit's new text now sits. That
    part of a long file stays in view, so checking an edit does not mean reading the
    file again."""
    out: dict[str, int] = {}
    for e in reversed(evs[-window:]):
        d = e.data
        if not d.get("ok"):
            continue
        args = d.get("args") or {}
        path = _path_of(e)
        if not path or path in out:
            continue
        if d.get("tool") == "read_file":
            try:
                out[path] = int(args["start"]) if args.get("start") not in (None, "") else -1
            except (TypeError, ValueError):
                out[path] = -1
        elif d.get("tool") == "edit_file":
            new, body = str(args.get("new_text") or ""), text_of(path) or ""
            at = body.find(new[:200]) if (new and body) else -1
            out[path] = max(0, at - 4_000) if at >= 0 else -1
        elif d.get("tool") in ("write_file", "add_to_file"):
            out[path] = -1
    return {p: f for p, f in out.items() if f >= 0}


# ------------------------------------------------------------- how much of each
def head_tail(text: str, cap: int) -> tuple[str, tuple[int, int] | None]:
    """A long file as its start and its end, with the middle left out and said so.
    What an agent adds lands at the end, so the end must stay in view. Returns the text
    to show and the (start, end) of the hidden middle, or None."""
    if len(text) <= cap:
        return text, None
    a, b = cap // 4, len(text) - (cap - cap // 4)
    return (text[:a] + f"\n\n[... {b - a} characters from the middle are not shown here "
            f"(characters {a} to {b}); read_file with start={a} shows them ...]\n\n"
            + text[b:]), (a, b)


def focused_view(text: str, cap: int, focus: int) -> tuple[str, bool]:
    """A long file as its start, the part around ``focus`` and its end, with the gaps
    said so. Returns the text and whether anything is left out."""
    n = len(text)
    if n <= cap:
        return text, False
    span = min(READ_WINDOW, cap * 3 // 4)
    s = max(0, min(focus, n - span))
    e = min(n, s + span)
    rest = cap - (e - s)
    h = min(s, rest // 4)
    t = min(n - e, rest - h)
    h = min(s, rest - t)

    def gap(a: int, b: int) -> str:
        return (f"\n\n[... characters {a} to {b} are not shown here; read_file with "
                f"start={a} shows them ...]\n\n") if b > a else ""

    return text[:h] + gap(h, s) + text[s:e] + gap(e, n - t) + text[n - t:], (h < s or e < n - t)


def working_files_text(v: Any, agent_id: str) -> str:
    c = v.ctx_s
    evs = v.tail(agent_id, ["action"], int(c("lookback")))
    window = max(3, int(v.S("guards.repeat_window")))

    def text_of(path: str) -> str | None:
        return read_text(v, agent_id, path)

    def size_of(path: str) -> int | None:
        text = text_of(path)
        return None if text is None else len(text)

    paths = recent_paths(evs, int(c("working_files")))
    fresh = just_read(evs, window, int(c("just_read_files")))
    paths += [p for p in fresh if p not in paths]
    paths += returning(evs, int(c("returning_reads")), int(c("returning_files")),
                       int(c("returning_file_chars")), paths, size_of)
    focus = focus_points(evs, window, text_of)
    paths += [p for p in focus if p not in paths]
    out, budget = [], int(c("just_read_total"))
    for path in paths:
        text = text_of(path)
        if text is None:
            continue
        size, cap = len(text), int(c("working_file_chars"))
        if (path in fresh or path in focus) and budget >= min(size, int(c("just_read_chars"))):
            cap = int(c("just_read_chars"))
        if path in focus and size > cap:
            text, cut = focused_view(text, cap, focus[path])
            whole = ("parts are left out below; the part you read or changed last is shown"
                     if cut else "shown whole, nothing left out")
        else:
            text, hidden = head_tail(text, cap)
            whole = ("shown whole, nothing left out" if hidden is None
                     else "its middle is left out below")
        budget -= len(text)
        out.append(f"## {path} ({size} characters, as it is now; {whole})\n{text}")
    if not out:
        return ""
    return ("# Files you are working with (their current text; no need to read them again)\n"
            + "\n\n".join(out) + "\n\n")


@rule("folders_seen", "village.context.folders_seen")
def folders_text(v: Any, agent_id: str) -> str:
    """The folders the agent listed recently, as they are now. A listing that shows for
    one beat only gets asked for again and again."""
    c = v.ctx_s
    folders: list[str] = []
    for e in reversed(v.tail(agent_id, ["action"], int(c("lookback")))):
        if e.data.get("ok") and e.data.get("tool") == "list_files":
            path = _path_of(e).strip("/")
            if path not in folders:
                folders.append(path)
        if len(folders) >= int(c("folders_seen")):
            break
    out, most = [], int(c("folder_lines"))
    for path in folders:
        lines = listing(v, agent_id, path)
        if lines is None:
            continue
        more = f"\n[... and {len(lines) - most} more]" if len(lines) > most else ""
        out.append(f"## {path or '(top folder)'}/\n" + "\n".join(lines[:most]) + more)
    if not out:
        return ""
    return ("# Folders you looked at (as they are now; no need to list them again)\n"
            + "\n\n".join(out) + "\n\n")


@rule("whole_answer_shown", "village.context.last_result_chars")
def last_result_text(last_result: tuple[str, str] | None, cap: int) -> str:
    """The whole of what the last action returned (the action list shows only a line).
    Past the cap the cut is stated, never silent."""
    if not last_result:
        return ""
    tool, text = last_result
    if len(text) > cap:
        text = text[:cap] + f"\n[... cut: {len(text) - cap} more characters not shown]"
    return f"# What came back from {tool}\n{text}\n\n"
