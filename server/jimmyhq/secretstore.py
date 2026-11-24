"""Secrets: API keys and tokens for outside services.

Kept in one owner-only file, apart from settings, and never written to the
Tape, the Vault or a log. Values are only ever returned to server code that
needs to make a call; the API exposes names, never values.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
from pathlib import Path


class SecretStore:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def _exclusive(self):
        """Hold the store across processes for one read-change-write.

        Two processes (or two stores on one data folder) changing different
        secrets at once must not lose either change.
        """
        with self._lock, open(self._path.with_name(self._path.name + ".lock"), "a+b") as guard:
            if sys.platform == "win32":
                import msvcrt

                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    guard.seek(0)
                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                except OSError:
                    pass  # a file system that cannot lock: fall back to the in-process lock
                yield  # the lock is released when the file closes

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, values: dict[str, str]) -> None:
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(values, handle, indent=2, sort_keys=True)
        os.replace(tmp, self._path)

    def get(self, name: str) -> str | None:
        with self._lock:
            return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        with self._exclusive():
            values = self._read()
            values[name] = value
            self._write(values)

    def delete(self, name: str) -> bool:
        with self._exclusive():
            values = self._read()
            found = values.pop(name, None) is not None
            if found:
                self._write(values)
            return found

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._read())
