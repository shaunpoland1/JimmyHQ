"""Fixtures for Posts' own tests. The helpers are in ``posts_rig``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from posts_rig import Rig  # noqa: E402


def _close(rig: Rig) -> None:
    if rig.ctx._store is not None:  # noqa: SLF001 - opened only when a test used it
        rig.ctx._store.conn.close()  # noqa: SLF001


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    _close(made)


@pytest.fixture
def make_rig(tmp_path):
    made: list[Rig] = []

    def make(**more):
        folder = tmp_path / f"rig{len(made)}"
        folder.mkdir()
        made.append(Rig(folder, **more))
        return made[-1]
    yield make
    for one in made:
        _close(one)
