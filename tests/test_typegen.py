from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_generated_typescript_event_types_match_the_registry():
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "gen_types.py"), "--check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
