"""The Vault: a content-addressed blob store.

Anything large (prompts, completions, drafts, transcripts) is stored as a file
named by its sha256, and the event carries a ``sha256:...`` reference. The Tape
stays quick to scan, and every reference is tamper-evident.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import threading
import time
from pathlib import Path

PREFIX = "sha256:"
_REF = re.compile(r"sha256:[0-9a-f]{64}")


class Vault:
    def __init__(self, root: Path | str):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        return self._root / digest[:2] / digest[2:]

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        # A file already there is touched: that says it is in use again. One
        # that an erase left unnamed is only removed once it has lain untouched
        # for a while (``discard``), and the event naming it follows this call.
        with contextlib.suppress(OSError):
            os.utime(path)
            return PREFIX + digest
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # A temp name unique to this writer: two writers storing identical
            # content at the same moment must not share a temp file.
            tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            try:
                os.replace(tmp, path)  # atomic, and overwrites on every platform
            except PermissionError:
                # Windows refuses to replace a file another writer has just put
                # there. Same digest means same bytes, so theirs is as good as ours.
                if not path.exists():
                    raise
            finally:
                tmp.unlink(missing_ok=True)
        return PREFIX + digest

    def put_text(self, text: str) -> str:
        return self.put(text.encode("utf-8"))

    def get(self, ref: str) -> bytes:
        # Checked strictly: a reference becomes a file path, so anything other
        # than a plain digest could point outside the vault.
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            raise ValueError(f"not a vault reference: {ref!r}")
        return self._path(ref.removeprefix(PREFIX)).read_bytes()

    def get_text(self, ref: str) -> str:
        return self.get(ref).decode("utf-8")

    def discard(self, ref: str, quiet_ms: float) -> bool:
        """Delete a file for good unless it was stored, or put again, within
        the last ``quiet_ms``. True when it is gone (or was never there),
        False when it was left because someone has just used it or it could
        not be moved. Only ``Tape.release_orphans`` should call this: it has
        checked, under the Tape's write lock, that no event names the file.

        The file is first moved aside and its time read from there, so a
        ``put`` cannot slip between the look and the delete: one that came
        before the move shows in the time and the file is put back; one that
        comes after finds no file and writes a new one."""
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            return True
        path = self._path(ref.removeprefix(PREFIX))
        aside = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.gone")
        try:
            os.replace(path, aside)
        except FileNotFoundError:
            return True
        except OSError:
            return False  # open elsewhere (Windows): tried again later
        try:
            fresh = (time.time() - aside.stat().st_mtime) * 1000 < quiet_ms
            if fresh and not path.exists():
                os.replace(aside, path)
                return False
            aside.unlink()
            return not fresh
        except OSError:
            with contextlib.suppress(OSError):
                aside.unlink()
            return False

    def remove(self, ref: str) -> bool:
        """Delete one file for good, whenever it was stored. For a caller that
        knows nothing can be about to name it. True when a file went."""
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            return False
        try:
            self._path(ref.removeprefix(PREFIX)).unlink()
        except FileNotFoundError:
            return False
        return True

    def has(self, ref: str) -> bool:
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            return False
        return self._path(ref.removeprefix(PREFIX)).exists()
