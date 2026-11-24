"""Build an installable JimmyHQ: the web app, packed inside the Python package.

    uv run python scripts/build_release.py [--out DIR]

Runs the web build, copies it to server/jimmyhq/web/static (where the server
looks first), then builds the wheel and source archive with ``uv build``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
STATIC = ROOT / "server" / "jimmyhq" / "web" / "static"


def run(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True, shell=sys.platform == "win32")  # noqa: S603


def main() -> int:
    out = ROOT / "dist"
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1]).resolve()
    run(["npm", "ci"], WEB)
    run(["npm", "run", "build"], WEB)
    shutil.rmtree(STATIC, ignore_errors=True)
    shutil.copytree(WEB / "dist", STATIC)
    try:
        run(["uv", "build", "--out-dir", str(out)], ROOT)
    finally:
        shutil.rmtree(STATIC, ignore_errors=True)  # the repo runs from web/dist
    print(f"Built into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
