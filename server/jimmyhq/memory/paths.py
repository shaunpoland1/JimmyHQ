"""Names inside the Library: what the API accepts, and what a scan looks at.

A note is addressed by its path relative to the Library, with forward slashes:
``shared/people.md``. Everything here is about keeping such a path inside the
Library on every operating system, whoever typed it.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from jimmyhq.memory.errors import LibraryError

MAX_PATH = 400  # characters in a whole path
MAX_SEGMENT = 120  # characters in one folder or file name
MAX_SEGMENT_BYTES = 240  # and bytes: most disks stop at 255, and a name can be all emoji
MAX_DEPTH = 12  # folders deep, counting the file

_DRIVE = re.compile(r"^[A-Za-z]:")
_FORBIDDEN = set('\\:*?"<>|')
# Names Windows keeps for devices, with or without an extension.
_RESERVED = {"con", "prn", "aux", "nul", "conin$", "conout$",
             *(f"com{d}" for d in "123456789¹²³"),
             *(f"lpt{d}" for d in "123456789¹²³")}
# What editors leave lying about while a file is open.
_TEMP_SUFFIXES = ("~", ".tmp", ".temp", ".swp", ".swx", ".swo", ".bak", ".part", ".crdownload")


def _refuse(why: str) -> LibraryError:
    return LibraryError(
        422, f"That note path cannot be used: {why}. Use a path such as shared/ideas.md.")


def clean_segments(value: object, *, what: str = "path") -> list[str]:
    """Check a Library-relative path and return its parts. Raises LibraryError(422)."""
    if not isinstance(value, str) or not value:
        raise _refuse(f"the {what} is empty")
    if len(value) > MAX_PATH:
        raise _refuse(f"it is longer than {MAX_PATH} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _refuse("it contains characters that cannot be saved") from None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise _refuse("it contains a control character")
    if "\\" in value:
        raise _refuse("it contains a backslash; use forward slashes")
    if value.startswith("/") or value.startswith("~"):
        raise _refuse("it must be relative to the Library, not start with / or ~")
    if _DRIVE.match(value):
        raise _refuse("it starts with a drive letter")
    bad = sorted(set(value) & _FORBIDDEN)
    if bad:
        raise _refuse(f"it contains {' '.join(bad)}")
    parts = value.split("/")
    if len(parts) > MAX_DEPTH:
        raise _refuse(f"it is more than {MAX_DEPTH} folders deep")
    for part in parts:
        if not part:
            raise _refuse("it has an empty folder name (two slashes together, or one at an end)")
        if part in (".", ".."):
            raise _refuse("it contains . or .. as a folder")
        if part.startswith("."):
            raise _refuse("a name starts with a dot, and hidden files are not part of the Library")
        if part != part.strip() or part.endswith("."):
            raise _refuse("a name starts or ends with a space, or ends with a dot")
        if len(part) > MAX_SEGMENT:
            raise _refuse(f"a name is longer than {MAX_SEGMENT} characters")
        if len(part.encode("utf-8")) > MAX_SEGMENT_BYTES:
            raise _refuse("a name is too long for a disk to store; shorten it")
        if part.split(".", 1)[0].strip().lower() in _RESERVED:
            raise _refuse(f"{part.split('.', 1)[0]} is a name Windows keeps for itself")
    return parts


def clean_path(value: object) -> str:
    """A note's Library-relative path, checked. Raises LibraryError(422)."""
    parts = clean_segments(value)
    name = parts[-1]
    if not name.lower().endswith(".md") or len(name) <= 3:
        raise _refuse("a note's name must end in .md")
    return "/".join(parts)


def clean_folder(value: object) -> str:
    """A folder inside the Library (a recall scope), checked."""
    if isinstance(value, str):
        value = value.strip().removesuffix("/")
    return "/".join(clean_segments(value, what="folder"))


def inside(root: Path, rel: str) -> Path:
    """The real file for a checked path, refusing anything that leaves the Library.

    No link is ever followed: a folder or file on the way that is a symbolic
    link is refused, and so is a spelling that differs only in case, or only in
    how an accented letter is stored (composed or decomposed), from a name
    already on disk. On a disk that treats those as one name they would be one
    file with two names in the index.
    """
    here = root
    for part in rel.split("/"):
        try:
            names = os.listdir(here) if here.is_dir() else []
        except OSError:
            names = []
        if part not in names:
            twin = next((n for n in names if same_name(n, part)), None)
            if twin is not None:
                raise LibraryError(
                    409, f"There is already something named {twin} there. Use that spelling, "
                         "or choose a different name.")
        here = here / part
        if is_link(here):
            raise _refuse(f"{part} is a link, and links are never followed")
    resolved = here.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise _refuse("it leads outside the Library")
    return here


def same_name(one: str, two: str) -> bool:
    """Whether a disk that ignores case or Unicode form would call these one name."""
    fold = lambda name: unicodedata.normalize("NFC", name).casefold()  # noqa: E731
    return fold(one) == fold(two)


def is_link(path: Path) -> bool:
    """A symbolic link, or a Windows junction, which is a link by another name."""
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True  # cannot tell: treat it as one


def ignorable(name: str) -> bool:
    """Hidden files and what editors leave behind: never part of the Library."""
    lower = name.lower()
    return (name.startswith(".") or name.startswith("#") or name.startswith("~$")
            or lower.endswith(_TEMP_SUFFIXES))


def is_note_name(name: str) -> bool:
    return name.lower().endswith(".md") and len(name) > 3 and not ignorable(name)
