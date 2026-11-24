"""Fixtures for Imagery's own tests. The helpers are in ``imagery_rig``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from imagery_rig import Rig  # noqa: E402


@pytest.fixture
def rig(tmp_path):
    made = Rig(tmp_path)
    yield made
    made.ctx.close()


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
        one.ctx.close()
