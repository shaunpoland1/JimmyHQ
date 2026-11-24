"""An agent's files: its own folder, and the shared folders it is allowed.

This is a security boundary. An agent reads and writes inside its own files folder, reads
everything under the shared folder, and writes only in its own part of it. Nothing an
agent sends as a path may reach anywhere else, whatever it looks like: ``..``, an
absolute path, a drive letter, a network path, a link that points outside, odd case or a
very long name.

How an agent sees it:

- ``notes/plan.md``: a file of its own.
- ``shared/<its id>/``: its own shared folder. It writes there, coworkers read.
- ``shared/<another id>/``: a coworker's. Read-only.
- ``shared/left/<id>/``: what an agent who has gone left behind. Read-only.

No shell, no hidden files, honest limits. Every refusal is a ``FilesError`` that names the
rule and says what to do instead.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

MAX_WRITE = 200_000  # characters in one file
MAX_READ = MAX_WRITE  # a read returns the whole file; the context decides what to show
READ_WINDOW = 30_000  # characters one read with ``start`` returns
MAX_FILES = 2_000  # items in an agent's folder
MAX_NAME = 120  # characters in one file or folder name
MAX_PATH = 400  # characters in a whole path
SHRINK_OVER = 400  # a file longer than this ...
SHRINK_BELOW = 0.7  # ... shrinking under this share of itself needs a deliberate yes
DUPLICATE_FROM = 80  # an append this long that is already in the file is a repeat

SHARED = "shared"  # the name the shared place has inside an agent's files
LEFT = "left"  # under it: the work of agents who have gone

_DRIVE = re.compile(r"^[A-Za-z]:")
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\x7f]')
_DEVICES = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
            *(f"lpt{n}" for n in range(1, 10))}


class FilesError(ValueError):
    """A file action that did not happen. ``rule`` names why."""

    def __init__(self, rule: str, sentence: str):
        super().__init__(sentence)
        self.rule = rule
        self.sentence = sentence


# --- the rules, one function each ----------------------------------------------------


def clean(rel: object) -> str:
    """A path as an agent wrote it, reduced to plain ``a/b/c`` or refused.

    Rule ``files_confined``: drive letters, network paths, ``..`` and anything that is not
    an ordinary name are refused before the disk is touched. A leading ``/`` or ``files/``
    is how agents often write "my top folder", so it is read that way: the path stays
    inside the folder."""
    if rel is None:
        return ""
    if not isinstance(rel, str):
        raise FilesError("files_confined", "Nothing happened: a path must be text, like "
                                           "notes/plan.md. Send the path as text.")
    raw = rel.strip()
    if len(raw) > MAX_PATH:
        raise FilesError("path_too_long",
                         f"Nothing happened: that path is {len(raw)} characters and a path "
                         f"takes {MAX_PATH}. Use shorter folder and file names.")
    if _DRIVE.match(raw) or raw.startswith(("\\\\", "//")):
        raise FilesError("files_confined",
                         "Nothing happened: that path points at a drive or another computer, "
                         "and you can only reach your own files and the shared folders. Use "
                         "a path inside your files, like notes/plan.md.")
    raw = raw.replace("\\", "/").lstrip("/")
    if raw.startswith("~"):
        raw = raw.lstrip("~/")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if parts and parts[0] == "files":
        parts = parts[1:]
    for part in parts:
        if part == "..":
            raise FilesError("files_confined",
                             "Nothing happened: that path leaves your files folder, and you "
                             "can only reach your own files and the shared folders. Write "
                             "the path from your top folder, like notes/plan.md, with no "
                             "'..' in it.")
        if part.startswith("."):
            raise FilesError("hidden_files",
                             f"Nothing happened: {part} is a hidden name, and hidden files "
                             "are not available. Use a name that does not start with a dot.")
        if len(part) > MAX_NAME:
            raise FilesError("path_too_long",
                             f"Nothing happened: the name {part[:40]}... is {len(part)} "
                             f"characters and a name takes {MAX_NAME}. Use a shorter name.")
        if (_BAD_CHARS.search(part) or part != part.strip() or part.endswith(".")
                or part.split(".")[0].casefold() in _DEVICES):
            raise FilesError("files_confined",
                             f"Nothing happened: {part[:40]!r} cannot be used as a file or "
                             "folder name. Use letters, digits, spaces, dots, hyphens and "
                             "underscores, like notes/plan-2.md.")
    if parts and parts[0].casefold() == SHARED:
        parts[0] = SHARED
        if len(parts) > 1 and parts[1].casefold() == LEFT:
            parts[1] = LEFT
    return "/".join(parts)


def inside(base: Path, path: Path) -> bool:
    """Whether a resolved path is ``base`` or under it."""
    return path == base or base in path.parents


def shrink_guard(old_chars: int, new_chars: int) -> bool:
    """Rule ``shrink_guard``: True when a write would replace a real file with much less.

    A write from a partial memory of a long file wipes what was added since. Shrinking a
    file by a lot needs a deliberate yes, and even then the old text is kept beside it."""
    return old_chars > SHRINK_OVER and new_chars < old_chars * SHRINK_BELOW


def over_size(chars: int) -> bool:
    """Rule ``file_limits``, the size half."""
    return chars > MAX_WRITE


def append_duplicate(existing: str, content: str) -> bool:
    """Rule ``append_duplicate``: the same block added again, a beat or two later."""
    body = content.strip()
    return len(body) >= DUPLICATE_FROM and body in existing


def locate(text: str, old: str) -> tuple[str, int]:
    """Rule ``edit_exact_once``, the matching half: the passage as it stands in the file
    and how many times it is there.

    Spacing alone is not a different passage. A passage wrapped onto two lines in the
    file, written with a space by the agent, is still that passage."""
    n = text.count(old)
    if n:
        return old, n
    core = old.strip()
    if core and text.count(core) == 1:
        return core, 1
    if core:
        pattern = r"\s+".join(re.escape(w) for w in old.split())
        found = list(re.finditer(pattern, text))
        if len(found) == 1:
            return found[0].group(0), 1
    return old, 0


def edit_already_made(text: str, old: str, new: str, count: int) -> bool:
    """Rule ``edit_already_made``: an insertion (the old text kept inside the new) whose
    new text is already there. Applying it again would put the passage in twice."""
    return bool(count and old in new and new.strip() and new in text)


def closest_passage(text: str, old: str, new: str) -> str:
    """What to say when the passage is not in the file. A bare "not there" teaches
    nothing and the next guess is no better, so show the nearest lines."""
    if new.strip() and new.strip() in text:
        return ("Its new_text is already in the file, so this change has most likely been "
                "made already. Check before trying again.")
    lines = text.splitlines()
    want = next((ln for ln in old.splitlines() if ln.strip()), old).strip()
    best, score = -1, 0.0
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        r = difflib.SequenceMatcher(None, want[:300], ln.strip()[:300]).ratio()
        if r > score:
            best, score = i, r
    tail = "Copy the passage exactly from the file as it is now (read_file it first)."
    if best < 0 or score < 0.5:
        return tail
    span = max(1, len(old.splitlines()))
    near = "\n".join(lines[best:best + span])[:1500]
    return f"The closest passage in the file now (line {best + 1}) is:\n{near}\n{tail}"


def similar_names(root: Path, rel: str, n: int = 3) -> list[str]:
    """Files whose names share the most words with ``rel``. A draft named by the wrong
    folder is otherwise taken for one that was never written, and written again."""
    def words(name: str) -> set[str]:
        return {w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 2 and w != "md"}

    want = words(Path(rel).stem)
    scored = []
    try:
        found = list(root.rglob("*"))
    except OSError:
        return []
    for f in found:
        try:
            parts = f.relative_to(root).parts
            if f.is_symlink() or not f.is_file() or any(x.startswith(".") for x in parts):
                continue
        except (OSError, ValueError):
            continue
        shared = len(want & words(f.stem))
        if shared >= 2:
            scored.append((-shared, "/".join(parts)))
    return [name for _, name in sorted(scored)[:n]]


def _text(p: Path) -> str:
    """A file's text exactly as stored: no line-ending translation on any platform."""
    return p.read_bytes().decode("utf-8", errors="replace")


def _put(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8", errors="replace"))


def _visible(p: Path) -> bool:
    return not p.name.startswith(".") and not p.is_symlink()


# --- the folder ----------------------------------------------------------------------


class Files:
    """One agent's view of the disk."""

    def __init__(self, root: Path, shared: Path | None = None, me: str | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.shared = Path(shared).resolve() if shared else None
        self.me = me

    # --- where a path really is --------------------------------------------------

    def _resolve(self, base: Path, parts: list[str]) -> Path:
        """The real path, with every link followed, or a refusal when it is not under
        ``base``. Checked on the resolved path, so a link that points outside is caught
        whatever its name says."""
        try:
            p = (base.joinpath(*parts)).resolve() if parts else base
        except (OSError, RuntimeError, ValueError):
            raise FilesError("files_confined", "Nothing happened: that path cannot be used. "
                                               "Use a plain path inside your files, like "
                                               "notes/plan.md.") from None
        if not inside(base, p):
            raise FilesError("files_confined",
                             "Nothing happened: that path leads outside your files and the "
                             "shared folders, and you can only reach those. Use a path "
                             "inside your files, like notes/plan.md.")
        if any(part.startswith(".") for part in p.relative_to(base).parts):
            raise FilesError("hidden_files", "Nothing happened: hidden files are not "
                                             "available. Use a name that does not start "
                                             "with a dot.")
        return p

    def _split(self, rel: object) -> tuple[str, list[str] | None]:
        """The clean path, and its parts under ``shared/`` when it points there."""
        path = clean(rel)
        parts = path.split("/") if path else []
        if self.shared is not None and parts and parts[0] == SHARED:
            if len(parts) > 1 and self.me and parts[1].casefold() == self.me.casefold():
                parts[1] = self.me
            return "/".join(parts), parts[1:]
        return path, None

    def path(self, rel: object) -> Path:
        """The real path behind a path as the agent writes it, to read from."""
        _, p = self._locate(rel)
        return p

    def _locate(self, rel: object) -> tuple[str, Path]:
        path, shared = self._split(rel)
        if shared is not None:
            return path, self._resolve(self.shared, shared)
        return path, self._resolve(self.root, path.split("/") if path else [])

    def own(self, rel: object) -> Path:
        """Rule ``shared_write_own_only``: a path that may be written. The agent's own
        files, or its own folder under ``shared/``."""
        path, shared = self._split(rel)
        if shared is None:
            p = self._resolve(self.root, path.split("/") if path else [])
            if p == self.root:
                raise FilesError("file_name_needed", "Nothing was written: that is your top "
                                                     "folder. Give a file name, like "
                                                     "notes/plan.md.")
            return p
        mine = f"{SHARED}/{self.me}/" if self.me else ""
        where = f" Your own shared folder is {mine}: write there." if mine else \
            " Put your own version in your own files."
        if shared and self.me and shared[0] == self.me:
            if len(shared) < 2:
                raise FilesError("file_name_needed",
                                 f"Nothing was written: {mine} is a folder. Give a file name "
                                 f"inside it, like {mine}draft.md.")
            p = self._resolve(self.shared, shared)
            home = self._resolve(self.shared, [self.me])
            if not inside(home, p) or p == home:
                raise FilesError("shared_write_own_only",
                                 f"Nothing was written: that leads outside your shared "
                                 f"folder, and {mine} is the 1 shared folder you can write "
                                 "in. Write there.")
            return p
        if shared and shared[0] != LEFT:
            raise FilesError("shared_write_own_only",
                             f"Nothing was written: {SHARED}/{shared[0]}/ belongs to a "
                             f"coworker. You can read it, and only they can write there."
                             f"{where}")
        raise FilesError("shared_write_own_only",
                         f"Nothing was written: that part of {SHARED}/ is read-only. It holds "
                         f"the work of agents who have gone, as they left it.{where}")

    def name(self, p: Path) -> str:
        """A real path as the agent writes it."""
        if inside(self.root, p):
            return p.relative_to(self.root).as_posix()
        return SHARED + "/" + p.relative_to(self.shared).as_posix()

    def file(self, rel: object) -> Path | None:
        """The real file behind a path, or None when there is none the agent may read."""
        try:
            p = self.path(rel)
        except FilesError:
            return None
        try:
            return p if p.is_file() else None
        except OSError:
            return None

    # --- limits ------------------------------------------------------------------

    def _base(self, p: Path) -> Path:
        return self.root if inside(self.root, p) else self.shared

    def _check_room(self, p: Path, chars: int, verb: str) -> None:
        """Rule ``file_limits``."""
        if over_size(chars):
            raise FilesError("file_limits",
                             f"Nothing was {verb}: that would make the file {chars} "
                             f"characters and a file takes {MAX_WRITE}. Split it into parts, "
                             "one file each.")
        if not p.exists():
            base = self._base(p)
            count = sum(1 for _ in base.rglob("*")) if base == self.root else \
                sum(1 for _ in (base / (self.me or "")).rglob("*"))
            if count >= MAX_FILES:
                raise FilesError("file_limits",
                                 f"Nothing was {verb}: that folder already holds {count} "
                                 f"items and it takes {MAX_FILES}. Add to a file you have, or "
                                 "put several notes in one file.")

    # --- listing -----------------------------------------------------------------

    def _shared_tops(self) -> list[str]:
        names: set[str] = set()
        if self.shared is not None and self.shared.is_dir():
            names |= {x.name for x in self.shared.iterdir() if _visible(x) and x.is_dir()}
        if self.me:
            names.add(self.me)
        return sorted(names)

    def _mark(self, name: str) -> str:
        if name == self.me:
            return f"{name}/ (yours: you can write here; your coworkers can read it)"
        if name == LEFT:
            return f"{name}/ (read-only: work left by agents who have gone)"
        return f"{name}/ (a coworker's: read-only for you)"

    @staticmethod
    def _entries(folder: Path) -> list[str]:
        items = sorted((x for x in folder.iterdir() if _visible(x)),
                       key=lambda x: (x.is_file(), x.name.lower()))
        return [f"{x.name}/" if x.is_dir() else f"{x.name} ({x.stat().st_size} bytes)"
                for x in items]

    def _folders_at(self, base: Path, missing: Path) -> tuple[str, list[str]]:
        """The nearest folder that exists above a missing one, and the folders in it."""
        parent = missing.parent
        while parent != base and not parent.is_dir():
            parent = parent.parent
        if base == self.shared and parent == base:
            return SHARED + "/", [n + "/" for n in self._shared_tops()]
        there = sorted(x.name + "/" for x in parent.iterdir() if _visible(x) and x.is_dir())
        if parent == self.root:
            if self.shared is not None:
                there = sorted([*there, SHARED + "/"])
            return "your top folder", there
        return self.name(parent) + "/", there

    def list(self, rel: object = "") -> str:
        """What is in a folder. A missing folder names the folders that are there: a
        bare "no such folder" is asked again and again."""
        path, shared = self._split(rel)
        if shared == []:
            return "\n".join(self._mark(n) for n in self._shared_tops()) or "(empty)"
        _, p = self._locate(rel)
        base = self.shared if shared is not None else self.root
        try:
            if p.is_file():
                raise FilesError("not_a_folder", f"Nothing was listed: {path} is a file, not "
                                                 "a folder. Use read_file to read it.")
            if not p.is_dir():
                if shared is not None and len(shared) == 1 and shared[0] == self.me:
                    return "(empty)"
                where, there = self._folders_at(base, p)
                names = ", ".join(there) or "(none)"
                raise FilesError("folder_missing",
                                 f"Nothing was listed: there is no folder {path}. The "
                                 f"{len(there)} folders in {where} are: {names}. List one of "
                                 "those, or your top folder with an empty path.")
            lines = self._entries(p)
        except OSError:
            raise FilesError("folder_missing", f"Nothing was listed: {path or 'that folder'} "
                                               "could not be opened. List your top folder "
                                               "with an empty path.") from None
        if p == self.root and self.shared is not None:
            lines.append(f"{SHARED}/ (a folder each for you and your coworkers, and work left "
                         "by agents who have gone)")
        return "\n".join(lines) if lines else "(empty)"

    # --- reading -----------------------------------------------------------------

    def read(self, rel: object, start: int | str | None = None) -> str:
        path, p = self._locate(rel)
        try:
            if p.is_dir():
                # Asking to read a folder is a fair way to ask what is in it.
                return f"{path or 'your top folder'} is a folder, not a file. What's in it:\n" \
                    + self.list(rel)
            if not p.is_file():
                near = similar_names(self._base(p), path.removeprefix(SHARED + "/"))
                hint = f" Files with similar names: {', '.join(near)}." if near else ""
                raise FilesError("file_missing",
                                 f"Nothing was read: there is no file {path}.{hint} Use "
                                 "list_files to see what is in the folder.")
            data = p.read_bytes()
        except OSError:
            raise FilesError("file_missing", f"Nothing was read: {path} could not be opened. "
                                             "Use list_files to see what is in the "
                                             "folder.") from None
        if start not in (None, ""):
            try:
                at = int(start)
            except (TypeError, ValueError):
                raise FilesError("read_start",
                                 "Nothing was read: start must be a character number, like "
                                 f"12000, and you sent {str(start)[:40]!r}. Send a whole "
                                 "number, or leave start out to read from the top.") from None
            # A long file shows only its head and tail in the view. Without a way to
            # reach the middle, what is recorded there cannot be used.
            full = data.decode("utf-8", errors="replace")
            a = max(0, min(at, len(full)))
            b = min(len(full), a + READ_WINDOW)
            more = f"; the rest starts at start={b}" if b < len(full) else \
                "; that is the end of the file"
            return f"[{path}: characters {a} to {b} of {len(full)}{more}]\n" + full[a:b]
        text = data[:MAX_READ].decode("utf-8", errors="replace")
        if len(data) > MAX_READ:
            text += (f"\n[... cut: the file is {len(data)} bytes; only the first {MAX_READ} "
                     "are shown]")
        return text

    # --- writing -----------------------------------------------------------------

    def _target(self, rel: object, verb: str) -> Path:
        p = self.own(rel)
        if p.is_dir():
            raise FilesError("file_name_needed",
                             f"Nothing was {verb}: {self.name(p)} is a folder. Give a file "
                             f"name inside it, like {self.name(p)}/notes.md.")
        return p

    def write(self, rel: object, content: str, replace: bool = False) -> str:
        p = self._target(rel, "written")
        try:
            self._check_room(p, len(content), "written")
            existed = p.exists()
            kept = ""
            if existed:
                old = _text(p)
                if shrink_guard(len(old), len(content)):
                    if not replace:
                        raise FilesError(
                            "shrink_guard",
                            f"Nothing was written: {self.name(p)} holds {len(old)} characters "
                            f"and this would replace it with {len(content)}, losing most of "
                            "what is there. To add to it, use add_to_file. To change one "
                            "part of it, use edit_file. To rewrite it on purpose, call "
                            "write_file again with replace: true.")
                    # Even a deliberate replace can come from an older picture of the
                    # file, so the old text stays next to it.
                    prev = p.with_name(f"{p.stem}.previous{p.suffix}")
                    _put(prev, old)
                    kept = (f"; the old text ({len(old)} characters) is kept in "
                            f"{self.name(prev)}")
            _put(p, content)
        except OSError:
            raise FilesError("file_limits", f"Nothing was written: {self.name(p)} could not "
                                            "be saved under that name. Use a shorter, plain "
                                            "name.") from None
        return (f"{'replaced' if existed else 'created'} {self.name(p)} "
                f"({len(content)} characters){kept}")

    def append(self, rel: object, content: str) -> str:
        p = self._target(rel, "added")
        try:
            existing = _text(p) if p.is_file() else ""
            if append_duplicate(existing, content):
                raise FilesError(
                    "append_duplicate",
                    f"Nothing was added: that exact text ({len(content.strip())} characters) "
                    f"is already in {self.name(p)}, so it was added before. Carry on with "
                    "the next thing.")
            self._check_room(p, len(existing) + len(content), "added")
            _put(p, existing + content)
        except OSError:
            raise FilesError("file_limits", f"Nothing was added: {self.name(p)} could not be "
                                            "saved under that name. Use a shorter, plain "
                                            "name.") from None
        return f"added {len(content)} characters to {self.name(p)}"

    def edit(self, rel: object, old: str, new: str, every: bool = False) -> str:
        """Replace one exact passage of a file. Without this a long file can only be
        fixed by rewriting it whole, which does not fit in one reply."""
        path, _ = self._split(rel)
        p = self.own(rel)
        try:
            if not p.is_file():
                raise FilesError("file_missing",
                                 f"Nothing was changed: there is no file {path}. Use "
                                 "list_files to see what is in the folder, or write_file to "
                                 "create it.")
            if not old:
                raise FilesError("edit_exact_once",
                                 "Nothing was changed: old_text is empty (0 characters). Copy "
                                 "the exact passage to change from the file.")
            text = _text(p)
            name = self.name(p)
            old, n = locate(text, old)
            if edit_already_made(text, old, new, n):
                raise FilesError(
                    "edit_already_made",
                    f"Nothing was changed: that new_text is already in {name}, so this edit "
                    "was made before. Applying it again would put the passage in 2 times. "
                    "Carry on with the next thing.")
            if n == 0:
                raise FilesError("edit_exact_once",
                                 f"Nothing was changed: that old_text is in {name} 0 times. "
                                 + closest_passage(text, old, new))
            if n > 1 and not every:
                raise FilesError(
                    "edit_exact_once",
                    f"Nothing was changed: that old_text appears {n} times in {name}. "
                    "Include more of the text around it so it matches only one place, or "
                    "set every to true to change all of them.")
            out = text.replace(old, new) if every else text.replace(old, new, 1)
            self._check_room(p, len(out), "changed")
            _put(p, out)
        except OSError:
            raise FilesError("file_missing", f"Nothing was changed: {path} could not be "
                                             "opened. Use list_files to see what is "
                                             "there.") from None
        places = f" in {n} places" if n > 1 else ""
        return (f"changed {name}: replaced {len(old)} characters with {len(new)}{places} "
                f"(the file is now {len(out)} characters)")


def for_agent(v, agent_id: str) -> Files:
    """The files of one agent of a running village."""
    return Files(v.agent_dir(agent_id) / "home" / "files", shared=v.shared_dir, me=agent_id)
