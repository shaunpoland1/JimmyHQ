"""Where the built web app is, and what to say while there is none.

Kept apart from ``web.app`` so the supervisor can ask without loading the web server.
"""

from __future__ import annotations

import os
from pathlib import Path

NOT_BUILT = ("The web app has not been built yet. In the JimmyHQ folder, run "
             "`cd web && npm install && npm run build`. It is picked up as soon as it is "
             "built: there is no need to restart JimmyHQ.")


def web_dist() -> Path | None:
    """Where the built web app is, if it has been built. Asked each time it is needed, so
    a build made while JimmyHQ runs is found without a restart."""
    candidates = [
        os.environ.get("JIMMYHQ_WEB_DIST"),
        Path(__file__).resolve().parent / "static",
        Path(__file__).resolve().parents[3] / "web" / "dist",
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "index.html").exists():
            return Path(candidate).resolve()
    return None


def not_built_page() -> str:
    """A small page for a browser that arrives before the web app is built. It asks
    again by itself, so the app appears once the build is done."""
    before, _, rest = NOT_BUILT.partition("`")
    command, _, after = rest.partition("`")
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="5"><title>JimmyHQ</title></head>\n'
        '<body style="font-family: sans-serif; max-width: 34em; margin: 4em auto; '
        'padding: 0 1em; line-height: 1.5">\n'
        "<h1>JimmyHQ is running</h1>\n"
        f"<p>{before}</p>\n<pre>{command}</pre>\n<p>{after.strip()}</p>\n"
        "<p>This page looks again every few seconds.</p>\n</body></html>\n"
    )
