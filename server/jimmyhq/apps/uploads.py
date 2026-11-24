"""Pictures the owner gives an app: received by the shell, kept in the app's own folder.

    POST /api/apps/<id>/files            owner, from JimmyHQ's own page
         body: the picture's bytes; header X-JimmyHQ-File-Name: its name, percent-encoded
         -> {ref, name, size, type}
    GET  /api/apps/<id>/files/<file>     owner, from JimmyHQ's own page: the picture, to
                                         show it (a thumbnail is the picture, drawn small)

An app's page cannot reach ``/api/``. It lets the owner pick files in its own frame and
hands each to the shell (``hq.files.upload(file)``); the shell, in the owner's session,
sends it here with the app id it mounted, never one the frame names. The picture is
streamed to ``<data>/apps/<id>/data/uploads/<random>.<ext>`` and the page is given its
``ref``, ``uploads/<random>.<ext>``: a full app's program opens it by that ref
(``app.files.path(ref)``), and any app can send it on with ``net.fetch`` and
``"file": ref``.

**Rules.** Only a picture is kept, and what it is is read from its first bytes, never
from its name or the type the browser declared: PNG, JPEG, GIF, WebP, AVIF and HEIC. SVG
is not taken (it can hold script). One picture may weigh at most ``apps.upload_max_mb``,
and it counts towards the app's storage quota together with its values and records.
Over either limit, or when it turns out not to be a picture, the request is stopped and
the part already written is deleted: nothing is left on disk. The name is kept only to
be handed back: it never becomes a path. One picture at a time per app.

**Receipts.** ``apps.file_received`` says which app, where it is kept, how large and of
what type. Not the picture's own name.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import stat
import threading
import unicodedata
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from jimmyhq.apps import appdata, bridge, kinds
from jimmyhq.apps.appdata import UPLOADS, folder_bytes
from jimmyhq.auth import Principal
from jimmyhq.web.deps import owner

if TYPE_CHECKING:
    from jimmyhq.core import Core

MAX_MB = "apps.upload_max_mb"
NAME_HEADER = "x-jimmyhq-file-name"
MAX_NAME = 120
SNIFF_BYTES = 32  # enough of the start of a file to tell each kind apart

TYPES: dict[str, str] = {
    "png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
    "avif": "image/avif", "heic": "image/heic",
}
_FILE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp|avif|heic)\Z")
_REF = re.compile(r"^uploads/[0-9a-f]{32}\.(png|jpg|gif|webp|avif|heic)\Z")
_AVIF_BRANDS = (b"avif", b"avis")
_HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")
_WINDOWS_CANNOT = '<>:"|?*'
_DEVICE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?\Z", re.IGNORECASE)
_FAULT = ("JimmyHQ could not keep the picture because of a fault of its own. Nothing was "
          "kept. Try again; if it keeps happening, look at the server's log.")


class UploadRefused(Exception):
    """A picture that was not kept. ``detail`` says nothing was kept and what to do."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- the rules, one function each -----------------------------------------------------------


def sniff_image(head: bytes) -> str | None:
    """The kind of picture these first bytes start, as a file ending, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _AVIF_BRANDS:
            return "avif"
        if brand in _HEIC_BRANDS:
            return "heic"
    return None


def check_ref(ref: Any) -> str:
    """A ref an upload handed out: ``uploads/<32 hex>.<ending>``, nothing else."""
    if not isinstance(ref, str) or not _REF.match(ref):
        raise UploadRefused(404, "That is not a picture this app was given, so nothing was "
                                 "shown. Use the ref the upload returned.")
    return ref


def kept_name(raw: Any) -> str:
    """The picture's name as the page is told it back, from the percent-encoded header.

    A name with a path separator or a control character is refused: a browser never
    sends one for a file the owner picked. Characters Windows cannot hold in a name are
    replaced, as is a name Windows keeps for a device, so the name can be used as a file
    name anywhere later. It is never used as one here."""
    bad = UploadRefused(422, "That picture's name cannot be read, so nothing was kept. "
                             "Rename the file with plain letters and choose it again.")
    if raw is None or raw == "":
        return "picture"
    if not isinstance(raw, str) or len(raw) > MAX_NAME * 12:
        raise bad
    try:
        name = urllib.parse.unquote(raw, errors="strict")
    except UnicodeDecodeError:
        raise bad from None
    if "/" in name or "\\" in name:
        raise UploadRefused(422, "A picture's name cannot hold a slash or a backslash, so "
                                 "nothing was kept. Choose the file itself, not a path.")
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in name):
        raise bad
    name = "".join("_" if ch in _WINDOWS_CANNOT else ch for ch in name)
    name = unicodedata.normalize("NFC", name).strip().rstrip(". ")
    if _DEVICE.match(name):
        name = "_" + name
    name = name[:MAX_NAME].rstrip(". ")
    return name or "picture"


def check_declared_size(declared: str | None, most: int) -> None:
    """Refuse at once a request that says it is larger than one picture may be."""
    if declared and declared.isdigit() and int(declared) > most:
        raise too_large(most)


def too_large(most: int) -> UploadRefused:
    return UploadRefused(413, f"A picture can be at most {most // (1024 * 1024)} MB, so "
                              "nothing was kept. Choose a smaller one, or raise \"Largest "
                              "picture you can give an app (MB)\" under Settings, Apps.")


def over_quota(limit_bytes: int) -> UploadRefused:
    mb = f"{limit_bytes / (1024 * 1024):.1f}".removesuffix(".0")
    return UploadRefused(507, f"This app may keep at most {mb} MB in all, and this picture "
                              "would take it over, so nothing was kept. Delete something in "
                              "the app first, or raise its storage under Settings, Apps "
                              "(\"Limits for one app\").")


def not_a_picture() -> UploadRefused:
    return UploadRefused(415, "That file is not a picture JimmyHQ can keep (PNG, JPEG, GIF, "
                              "WebP, AVIF or HEIC), whatever its name says, so nothing was "
                              "kept. Choose a picture.")


def uploads_dir(core: Core, app_id: str) -> Path:
    """``<data>/apps/<id>/data/uploads``, made if missing, with no link on the way."""
    data = appdata.data_dir(core, app_id)
    for step in (data.parent, data, data / UPLOADS):
        if step.is_symlink():
            raise UploadRefused(409, "This app's storage folder is a link, which JimmyHQ "
                                     "does not follow, so nothing was kept.")
    folder = data / UPLOADS
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def room(core: Core, app: bridge.Installed) -> tuple[int, int]:
    """(bytes the app may still keep, its whole storage limit): values, records and
    pictures count together."""
    limits = bridge.storage_limits(core, app.manifest.id)
    held = appdata.open_data(core, app.manifest.id, app.manifest.tables).usage()["bytes"]
    held += folder_bytes(appdata.data_dir(core, app.manifest.id) / UPLOADS)
    return max(0, limits.storage_bytes - held), limits.storage_bytes


def find_picture(core: Core, app_id: str, ref: Any) -> Path:
    """The kept picture a ref names, as an ordinary file inside the app's uploads."""
    ref = check_ref(ref)
    data = appdata.data_dir(core, app_id)
    here = data / ref
    missing = UploadRefused(404, "There is no such picture, so nothing was shown. It may "
                                 "have been deleted.")
    for step in (data.parent, data, data / UPLOADS, here):
        if step.is_symlink():
            raise missing
    try:
        if not stat.S_ISREG(here.lstat().st_mode):
            raise missing
    except OSError:
        raise missing from None
    if not here.resolve().is_relative_to(data.resolve()):
        raise missing
    return here


# --- receiving one picture ------------------------------------------------------------------

_busy: set[str] = set()
_busy_lock = threading.Lock()


def _claim(app_id: str) -> None:
    with _busy_lock:
        if app_id in _busy:
            raise UploadRefused(409, "Another picture is being kept for this app right now, "
                                     "so this one was not. Send them one after another.")
        _busy.add(app_id)


def _release(app_id: str) -> None:
    with _busy_lock:
        _busy.discard(app_id)


async def receive(core: Core, app_id: str, name: Any, chunks: Any,
                  declared: str | None = None, *, actor: str = "owner") -> dict[str, Any]:
    """Stream one picture into the app's uploads and return ``{ref, name, size, type}``.
    On any refusal or failure the part written is deleted before the answer."""
    app = await run_in_threadpool(bridge.check_installed, core, bridge.check_app_id(app_id))
    shown = kept_name(name)
    most = int(core.settings.get(MAX_MB)) * 1024 * 1024
    check_declared_size(declared, most)
    _claim(app_id)
    try:
        folder = await run_in_threadpool(uploads_dir, core, app_id)
        left, whole = await run_in_threadpool(room, core, app)
        token = secrets.token_hex(16)
        part = folder / f"{token}.part"
        handle = await run_in_threadpool(open, part, "xb")
        size, head, ending = 0, b"", None
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > most:
                    raise too_large(most)
                if size > left:
                    raise over_quota(whole)
                if ending is None:
                    head += chunk[:SNIFF_BYTES]
                    if len(head) >= SNIFF_BYTES:
                        ending = sniff_image(head)
                        if ending is None:
                            raise not_a_picture()
                await asyncio.to_thread(handle.write, chunk)
            if ending is None:
                ending = sniff_image(head)
                if ending is None:
                    raise not_a_picture()
            await asyncio.to_thread(handle.close)
            final = folder / f"{token}.{ending}"
            await run_in_threadpool(part.rename, final)  # closed first: Windows needs it
        except BaseException:
            handle.close()
            part.unlink(missing_ok=True)
            raise
    finally:
        _release(app_id)
    ref = f"{UPLOADS}/{token}.{ending}"
    await run_in_threadpool(_received, core, app, ref, size, TYPES[ending], actor)
    return {"ref": ref, "name": shown, "size": size, "type": TYPES[ending]}


def _received(core: Core, app: bridge.Installed, ref: str, size: int, kind: str,
              actor: str) -> None:
    core.tape.print("apps.file_received", {"app_id": app.manifest.id, "ref": ref,
                                           "size": size, "type": kind},
                    actor=actor, subject=kinds.subject(app.manifest.id))


# --- the routes -----------------------------------------------------------------------------

_NOT_OWN = ("Only JimmyHQ's own screen can give an app a picture or show one. Nothing was "
            "done. Choose the picture on the app's screen.")
_PICTURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Content-Disposition": "inline",
    "Cache-Control": "private, max-age=3600",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _refused(error: UploadRefused) -> JSONResponse:
    return JSONResponse({"detail": error.detail}, error.status)


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/apps", tags=["apps"])

    @router.post("/{app_id}/files")
    async def upload(app_id: str, request: Request,
                     who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            return await receive(core, app_id, request.headers.get(NAME_HEADER),
                                 request.stream(), request.headers.get("content-length"),
                                 actor=who.actor)
        except UploadRefused as error:
            return _refused(error)
        except bridge.BridgeError as error:
            status = 404 if error.code == "not_found" else 403
            return JSONResponse({"detail": error.message}, status)
        except OSError:
            return JSONResponse({"detail": _FAULT}, 500)

    @router.get("/{app_id}/files/{file}")
    def picture(app_id: str, file: str, request: Request,
                _: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            if bridge.find_app(core, app_id) is None or not _FILE.match(file):
                check_ref(None)  # raises: the one refusal, whichever part is wrong
            found = find_picture(core, app_id, f"{UPLOADS}/{file}")
        except UploadRefused as error:
            return _refused(error)
        return FileResponse(found, media_type=TYPES[file.rsplit(".", 1)[1]],
                            headers=_PICTURE_HEADERS)

    return router
