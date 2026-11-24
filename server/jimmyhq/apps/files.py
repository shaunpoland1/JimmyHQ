"""Serving an installed app's own files into its frame, and nothing else.

    POST /api/apps/<id>/mount            owner's session -> {"mount": <token>, "base": ...}
    GET  /appfiles/<mount>/<path>        no credential: the token is the whole key

A mount is a random token for one app at one version. The shell asks for one and points
a sandboxed frame at ``/appfiles/<mount>/<entry>``; the frame never holds a credential
for ``/api/``. A token stops working when the app is switched off, removed or changed
to another version.

**Isolation.** Every answer under ``/appfiles/``, a refusal included, carries the
policy in ``CSP``. Its ``sandbox`` gives the page an opaque origin even if it is opened
outside the shell's frame, so it never shares JimmyHQ's origin, cookies or storage;
``connect-src 'none'`` leaves the bridge as its only way to reach anything. ``'self'``
in ``img-src`` and the rest lets a page ask for any address here, ``/api/`` included, but
never as the owner: from an opaque origin every such request is cross-site, so the
browser leaves out the session cookie, which is ``SameSite=Lax`` (``web.app``), and
``/api/`` answers it as it would anyone signed out.

Because the origin is opaque, the page's own module scripts and fonts are fetched as
cross-origin requests, and a browser will not hand them over without
``Access-Control-Allow-Origin``. So these files, and only these, are sent with ``*``:
they are the app's public code, they need a mount token to reach, and the request
carries no credential.

**Confinement.** A path is checked by the manifest's own path rule before the disk is
touched (no ``..``, no backslash, no drive or address, no empty or dotted step, no
device name), then walked one step at a time refusing any link, then resolved and
proven to lie inside the version folder. Only the file types in ``TYPES`` are served,
each with a fixed content type and ``nosniff``.

``frames_only_get_app_files`` is the other half of the design's rule that a frame can
be shown nothing but app files; ``FrameGuard`` applies it to a whole web app.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from jimmyhq import storeversion
from jimmyhq.apps import bridge, dbfile, quotas
from jimmyhq.apps.manifest import APP_API, ManifestError, inside
from jimmyhq.auth import Principal
from jimmyhq.ids import now_ms
from jimmyhq.web.deps import owner

if TYPE_CHECKING:
    from jimmyhq.core import Core

PREFIX = "/appfiles/"
BRIDGE_SCRIPT = "_bridge.js"
UI_SCRIPT = "_ui.js"  # the page helper: text nodes only, the page's own dialogs

CSP = ("sandbox allow-scripts allow-forms; default-src 'none'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
       "media-src 'self' blob:; connect-src 'none'; form-action 'none'; frame-src 'none'; "
       "worker-src 'none'; base-uri 'none'; webrtc 'block'")

CACHE_INSTALLED = "private, max-age=3600, immutable"  # a mount never changes what it serves
CACHE_NEVER = "no-store"

# The only kinds of file an app's frame is given, each with one fixed type.
TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}

NOT_FOUND = "There is nothing at this address."
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,64}\Z")
_KEEP_MOUNTS = 50  # for one app; older ones stop working
_FRAME_DESTS = frozenset({"iframe", "frame", "embed", "object", "fencedframe"})



# --- the headers ----------------------------------------------------------------------------


def headers(*, development: bool = False, found: bool = True) -> dict[str, str]:
    """The headers every answer under ``/appfiles/`` carries."""
    return {
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "SAMEORIGIN",
        "Access-Control-Allow-Origin": "*",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Cache-Control": CACHE_NEVER if development or not found else CACHE_INSTALLED,
    }


def content_type(path: str) -> str | None:
    """The fixed type for a path's ending, or None for a kind of file that is not served."""
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return TYPES.get(name[dot:].lower()) if dot > 0 else None


def frames_only_get_app_files(path: str, request_headers: Mapping[str, str]) -> bool:
    """False for a request that would put anything but an app's files in a frame. The
    browser says where a page is going in ``Sec-Fetch-Dest``; a frame that navigates
    itself to the sign-in page, or to any other address here, is answered with nothing."""
    dest = (request_headers.get("sec-fetch-dest") or "").strip().lower()
    return dest not in _FRAME_DESTS or path.startswith(PREFIX)


class FrameGuard:
    """ASGI middleware that applies ``frames_only_get_app_files`` to every request:
    ``app.add_middleware(FrameGuard)``."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            sent = {k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in scope.get("headers", ())}
            if not frames_only_get_app_files(scope.get("path", ""), sent):
                refusal = JSONResponse(
                    {"detail": "JimmyHQ shows only an app's own files inside a frame."},
                    403, headers={"Cache-Control": CACHE_NEVER,
                                  "X-Content-Type-Options": "nosniff"})
                await refusal(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --- files the core supplies to every app ---------------------------------------------------

_PROVIDED: dict[str, Path | bytes] = {}


def provide(name: str, source: Path | bytes) -> None:
    """Serve a file of the core's own under every mount, at a name that starts with an
    underscore (``_bridge.js``, the described-app renderer). It wins over an app's file
    of the same name."""
    if not re.fullmatch(r"_[A-Za-z0-9._/-]{1,80}", name) or ".." in name or "//" in name \
            or content_type(name) is None:
        raise ValueError(f"not a name the core can supply: {name!r}")
    _PROVIDED[name] = source


def provided(name: str) -> Path | bytes | None:
    found = _PROVIDED.get(name)
    if found is None and name in (BRIDGE_SCRIPT, UI_SCRIPT):
        from jimmyhq.apps import store  # noqa: PLC0415

        return store.bridge_script() if name == BRIDGE_SCRIPT else store.ui_script()
    return found


# --- mounts ---------------------------------------------------------------------------------


class Mounts:
    """Which token opens which app version. Kept in ``apps.db`` so a restart does not
    break the frames that are open."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None,
                                   timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        storeversion.prepare(self._db, dbfile.STORE)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def add(self, app_id: str, version: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute("DELETE FROM mounts WHERE app_id=? AND version<>?",
                             (app_id, version))
            self._db.execute(
                "DELETE FROM mounts WHERE app_id=? AND n NOT IN (SELECT n FROM mounts "
                "WHERE app_id=? ORDER BY n DESC LIMIT ?)",
                (app_id, app_id, _KEEP_MOUNTS - 1))
            self._db.execute(
                "INSERT INTO mounts (token_hash, app_id, version, created) VALUES (?,?,?,?)",
                (_hash(token), app_id, version, now_ms()))
        return token

    def find(self, token: str) -> tuple[str, str] | None:
        """(app id, version folder) for a token, or None."""
        if not isinstance(token, str) or not _TOKEN.match(token):
            return None
        with self._lock:
            row = self._db.execute("SELECT app_id, version FROM mounts WHERE token_hash=?",
                                   (_hash(token),)).fetchone()
        return (row[0], row[1]) if row else None

    def forget(self, app_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM mounts WHERE app_id=?", (app_id,))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def mounts(core: Core) -> Mounts:
    return core.service("apps.mounts", lambda c: Mounts(quotas.state_db(c)))


def screen_entry(manifest: Any, screen: Any) -> str | None:
    """The file a screen, tab or tile of this app opens in its frame; the first screen's
    when none is named. A described one has no file: JimmyHQ draws it itself."""
    places = [*manifest.screens, *manifest.dock, *manifest.wall]
    if screen is None:
        places = list(manifest.screens[:1])
    found = next((p for p in places if screen is None or p.id == screen), None)
    if found is None:
        raise bridge.BridgeError("not_found", f"The app \"{manifest.id}\" has no screen of "
                                              "that name. Nothing was opened.")
    return None if found.described else found.entry


def owner_calendar(core: Core) -> dict[str, str]:
    """The two things an app is told about the owner: their time zone and the day their
    week starts on. Nothing else about them is ever sent to a frame."""
    return {"timezone": str(core.settings.get("owner.timezone") or ""),
            "week_start": str(core.settings.get("owner.week_starts") or "")}


def mint_mount(core: Core, app_id: str, screen: Any = None) -> dict[str, Any]:
    """A new mount for the version of the app in use now, with what the shell tells the
    page when it is ready. Raises ``bridge.BridgeError`` if the app cannot be shown."""
    app = bridge.check_installed(core, bridge.check_app_id(app_id))
    manifest = app.manifest
    entry = screen_entry(manifest, screen)
    token = mounts(core).add(app_id, app.folder.name)
    return {"mount": token, "base": f"{PREFIX}{token}/", "entry": entry,
            "app": manifest.id, "name": manifest.name, "version": manifest.version,
            "tier": manifest.tier, "permissions": list(app.granted),
            # Addresses on the owner's own network he let it reach (local_reach), for the
            # shell's early check only; the server checks every request again.
            "local_addresses": list(app.local_addresses),
            # The hosts he gave the app himself on its page, for the same early check.
            "owner_hosts": list(app.owner_hosts),
            "development": app.development, "api": f"{APP_API[0]}.{APP_API[1]}",
            # The shell refuses a larger picture before sending it; the server checks again.
            "upload_max_mb": int(core.settings.get("apps.upload_max_mb")),
            # The longest side of the small version the shell makes for `files.thumbnail`.
            "thumbnail_px": int(core.settings.get("apps.thumbnail_px")),
            # For `files.picture`, the full view: the longest side, the largest kept picture
            # it reads, and how many one open screen may ask for in a minute.
            "picture_px": int(core.settings.get("apps.picture_px")),
            "picture_max_mb": int(core.settings.get("apps.picture_max_mb")),
            "pictures_per_minute": int(core.settings.get("apps.pictures_per_minute")),
            # For hq.speech.dictate: the longest recording, and whether the app is private
            # (its words may then only go to an engine on the owner's own machines).
            "dictate_max_s": int(core.settings.get("apps.dictate_max_s")),
            "private": bool(manifest.private),
            # For hq.ui.save: the largest file, how many one click may hand over, how many
            # one app may hand over in a minute, and whether a web page may be saved at all.
            "save_max_mb": int(core.settings.get("apps.save_max_mb")),
            "save_parts": int(core.settings.get("apps.save_parts")),
            "save_per_minute": int(core.settings.get("apps.save_per_minute")),
            "save_html": bool(core.settings.get("apps.save_html")),
            **owner_calendar(core)}


def resolve_mount(core: Core, token: str) -> bridge.Installed | None:
    """The app a token opens, if it is still installed, switched on and at the version
    the token was made for."""
    found = mounts(core).find(token)
    if found is None:
        return None
    app_id, version = found
    app = bridge.find_app(core, app_id)
    if app is None or not app.enabled or app.manifest.id != app_id \
            or app.folder.name != version:
        return None
    return app


def forget_mounts(core: Core, app_id: str) -> None:
    """Stop every open frame of an app from loading more of it (on remove or disable)."""
    mounts(core).forget(app_id)


# --- finding the file -----------------------------------------------------------------------


def locate(folder: Path, path: str) -> Path | None:
    """The ordinary file inside ``folder`` that a request path names, or None. Never
    follows a link and never leaves the folder."""
    if not isinstance(path, str) or content_type(path) is None:
        return None
    try:
        return inside(folder, path)
    except (ManifestError, OSError, ValueError):
        return None


def serve(core: Core, token: str, path: str) -> Response:
    """The answer to one request under ``/appfiles/``."""
    app = resolve_mount(core, token)
    kind = content_type(path) if isinstance(path, str) else None
    if app is None or kind is None:
        return _missing()
    send = headers(development=app.development)
    # A page imports the core's files from beside itself ("./_bridge.js"), whatever folder
    # of the app it sits in, so they are supplied in every folder.
    own = provided(path) or provided(path.rsplit("/", 1)[-1])
    if own is not None:
        if isinstance(own, bytes):
            return Response(own, media_type=kind, headers=send)
        return FileResponse(own, media_type=kind, headers=send) if own.is_file() \
            else _missing()
    found = locate(app.folder, path)
    if found is None:
        return _missing()
    return FileResponse(found, media_type=kind, headers=send)


def _missing() -> Response:
    # One answer for every reason, so a probe learns nothing from the difference.
    return JSONResponse({"detail": NOT_FOUND}, 404, headers=headers(found=False))


class MountBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screen: str | None = Field(default=None, max_length=40)


def build_router(core: Core) -> APIRouter:
    router = APIRouter(tags=["apps"])

    @router.post("/api/apps/{app_id}/mount")
    def mount(app_id: str, body: MountBody | None = None,
              _: Principal = Depends(owner)):  # noqa: B008
        try:
            return mint_mount(core, app_id, body.screen if body else None)
        except bridge.BridgeError as error:
            status = 404 if error.code == "not_found" else 409
            return JSONResponse({"detail": error.message}, status)

    @router.api_route(PREFIX + "{token}/{path:path}", methods=["GET", "HEAD"],
                      include_in_schema=False)
    def app_file(token: str, path: str):
        return serve(core, token, path)

    @router.api_route(PREFIX + "{rest:path}", include_in_schema=False,
                      methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def nothing(rest: str):
        return _missing()

    return router
