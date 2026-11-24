"""Sending files out of an agent's folder (or the shared folders) as downloads.

The caller has already resolved the path with ``routes.inside``, so ``target`` is a real
path under ``base``. This module adds what a download needs on top: a header that makes
the browser save the file and never run it, and a zip of a folder that leaves out hidden
names and links, and stops at a size the owner has set.
"""

from __future__ import annotations

import mimetypes
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO
from urllib.parse import quote

CAP_SETTING = "village.files.download_cap_mb"
MAX_ENTRIES = 20_000  # the most files one zip holds
CHUNK = 1 << 20

# Types a browser may be told about. Everything is sent as a download, so a file can
# never run as a page; anything not listed is sent as plain bytes.
SAFE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
              "text/plain", "text/markdown", "text/csv", "application/json"}


class TooBig(Exception):
    """The zip would pass the size the owner allowed. The message says what to do."""


def disposition(name: str) -> str:
    plain = "".join(ch if ch.isascii() and ch.isprintable() and ch not in '"\\/;' else "_"
                    for ch in name) or "file"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


def media_type(name: str) -> str:
    guessed = mimetypes.guess_type(name)[0]
    return guessed if guessed in SAFE_TYPES else "application/octet-stream"


def headers(name: str, size: int | None = None) -> dict[str, str]:
    out = {"Content-Disposition": disposition(name), "Cache-Control": "private",
           "X-Content-Type-Options": "nosniff"}
    if size is not None:
        out["Content-Length"] = str(size)
    return out


def _members(base: Path, folder: Path) -> Iterator[tuple[Path, str]]:
    """Every real file under ``folder`` with its name in the zip. Hidden names are
    skipped, links are not followed, and anything that resolves outside ``base`` is left
    out."""
    stack = [folder]
    while stack:
        here = stack.pop()
        try:
            entries = sorted(here.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            real = entry.resolve()
            if base not in real.parents:
                continue
            if real.is_dir():
                stack.append(entry)
            elif real.is_file():
                yield entry, entry.relative_to(folder).as_posix()


def build_zip(base: Path, folder: Path, cap_bytes: int) -> tuple[IO[bytes], int]:
    """A zip of ``folder`` in a temporary file, positioned at its start, and its size.
    Raises ``TooBig`` when the files add up to more than ``cap_bytes``."""
    base = base.resolve()
    total, count = 0, 0
    out = tempfile.SpooledTemporaryFile(max_size=16 << 20)  # noqa: SIM115 - closed by the sender
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, name in _members(base, folder):
                count += 1
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
                if total > cap_bytes or count > MAX_ENTRIES:
                    raise TooBig(
                        "These files are too big to download as one zip. Open a smaller "
                        "folder and download that, or download the files one at a time. "
                        "The limit is in Settings, under Village.")
                try:
                    archive.write(path, name)
                except OSError:
                    continue
        size = out.tell()
        out.seek(0)
    except BaseException:
        out.close()
        raise
    return out, size


def stream(handle: IO[bytes]) -> Iterator[bytes]:
    try:
        while chunk := handle.read(CHUNK):
            yield chunk
    finally:
        handle.close()
