"""Request bodies built by JimmyHQ from files in an app's own data folder, for ``net.fetch``.

An app never holds a file as text or passes megabytes through a call. It names files by
a forward-slash path inside ``<data>/apps/<id>/data`` (the folder an uploaded picture's
``ref`` points into), and JimmyHQ reads them while it sends. Three forms:

**Raw bytes** (``file``): the file is the whole body.

    {url, method: "PUT", file: "uploads/0f1e...e1f0.jpg", headers: {"Content-Type": "image/jpeg"}}

With no ``Content-Type`` header it is sent as ``application/octet-stream``; with
``"Content-Type": ""`` it is sent with none, for a signed address that was signed without
one.

**A form** (``multipart``): ``multipart/form-data`` with text fields and file parts, in
the order given; a name may repeat.

    {url, method: "POST", multipart: [
        {"name": "caption", "value": "Morning light"},
        {"name": "media", "file": "uploads/0f1e...e1f0.jpg", "type": "image/jpeg",
         "filename": "morning.jpg"}]}

``type`` defaults to what a picture's bytes say, else ``application/octet-stream``;
``filename`` to the file's own name.

**A file inside JSON** (``json``): any value written as ``{"$file": <path>, "as":
"data_url" | "base64", "type"?: <content type>}`` is replaced by the file as a base64
``data:`` URL or as plain base64 text, filled in while the body is streamed.

    {url, method: "POST", json: {"caption": "Morning light",
        "media": {"$file": "uploads/0f1e...e1f0.jpg", "as": "data_url"}}}

**The same rules for all three.** Each path is checked by the manifest's path rule before
the disk is touched (no ``..``, no backslash, no drive, no empty or dotted step, no name
Windows keeps for a device), then walked one step at a time refusing any link, proven to
resolve inside the folder and opened as an ordinary file; the core's own ``app.db`` is
never sent. Each file may weigh at most ``apps.net_fetch_file_max_mb`` and the whole body,
as sent, at most ``apps.net_fetch_body_max_mb``. The length of the whole body is worked out
before anything is sent and sent as ``Content-Length``: a file that changes length on
the way stops the request. The host is checked exactly as for any fetch, and first. With
``"fresh_connection": true`` the request asks the host to close the connection after it
(``Connection: close``); JimmyHQ never reuses a connection between two fetches anyway.

The receipt (``apps.file_sent``) names the form, each file's path and size, and the size
of the whole body: never what was in the files or the other fields.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import stat
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from jimmyhq.apps import appdata, kinds
from jimmyhq.apps.bridge import BridgeError
from jimmyhq.apps.manifest import ManifestError, check_path

if TYPE_CHECKING:
    from jimmyhq.core import Core

MAX_MB = "apps.net_fetch_file_max_mb"
BODY_MAX_MB = "apps.net_fetch_body_max_mb"
TIMEOUT_S = "apps.net_fetch_file_timeout_s"
CHUNK = 3 * 21_846  # a multiple of 3, so each piece but the last encodes to whole base64
MAX_FILES = 20
MAX_PARTS = 50
# The core's own files in the data folder: the app reaches them through storage.* and
# records.*, never as bytes.
CORE_FILES = frozenset({"app.db", "app.db-wal", "app.db-shm", "app.db-journal"})
SENDS_A_BODY = ("POST", "PUT", "PATCH")
FORMS = ("body", "json", "file", "multipart")
_PART_NAME = re.compile(r'^[^\x00-\x1f\x7f"\\]{1,100}\Z')
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}"
                         r"(;[ ]?[A-Za-z0-9!#$&^_.+-]{1,40}=[A-Za-z0-9!#$&^_.+-]{1,80}){0,3}\Z")
_MARK_KEYS = frozenset({"$file", "as", "type"})


# --- the rules, one function each -----------------------------------------------------------


def check_one_body(args: dict[str, Any]) -> None:
    """A request has one body at most: text, JSON, a file or a form."""
    given = [name for name in FORMS if args.get(name) is not None]
    if len(given) > 1:
        raise BridgeError("invalid", "net.fetch takes one of \"body\", \"json\", \"file\" "
                                     "or \"multipart\", not more. Nothing was sent.")


def check_method(method: str) -> str:
    """A body built from files goes with a POST, a PUT or a PATCH."""
    if method not in SENDS_A_BODY:
        raise BridgeError("invalid", "A file can be sent only with POST, PUT or PATCH. "
                                     "Nothing was sent. Set \"method\" to one of them.")
    return method


def check_fresh(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise BridgeError("invalid", "\"fresh_connection\" for net.fetch is true or false. "
                                     "Nothing was sent.")
    return value


def check_body_path(value: Any) -> str:
    """The path as written, if it stays inside a folder on every system and is not the
    core's own store."""
    if not isinstance(value, str):
        raise BridgeError("invalid", "A file for net.fetch is named by a path inside your "
                                     "app's own data folder, such as \"uploads/0f1e2d.jpg\". "
                                     "Nothing was sent.")
    try:
        path = check_path(value, "file")
    except ManifestError:
        raise BridgeError("invalid", "A file for net.fetch must be a path inside your app's "
                                     "own data folder, with forward slashes and no \"..\", "
                                     "such as \"uploads/0f1e2d.jpg\". Nothing was sent."
                          ) from None
    if path.split("/")[0].casefold() in CORE_FILES:  # one file on a disk that ignores case
        raise BridgeError("not_allowed", "That file is JimmyHQ's own store of your app's "
                                         "values and records, which is not sent as a file. "
                                         "Nothing was sent. Read what you need with "
                                         "storage.get or records.list.")
    return path


def check_media_type(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _MEDIA_TYPE.match(value):
        raise BridgeError("invalid", f"{what} must be a content type such as "
                                     "\"image/jpeg\". Nothing was sent.")
    return value


def check_part_name(value: Any, what: str) -> str:
    """A form field's name or a file part's file name: one short line, no quote or
    backslash, so it cannot break out of its header."""
    if not isinstance(value, str) or not _PART_NAME.match(value):
        raise BridgeError("invalid", f"{what} must be 1 to 100 characters with no quote, "
                                     "backslash or control character. Nothing was sent.")
    return value


def check_parts(value: Any) -> list[dict[str, Any]]:
    """A ``multipart`` list: each item ``{name, value}`` or ``{name, file, type?,
    filename?}``."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_PARTS:
        raise BridgeError("invalid", f"\"multipart\" is a list of 1 to {MAX_PARTS} parts, "
                                     "each {name, value} or {name, file}. Nothing was sent.")
    out = []
    for part in value:
        if not isinstance(part, dict) or "name" not in part \
                or ("value" in part) == ("file" in part):
            raise BridgeError("invalid", "Each part of \"multipart\" is {name, value} for "
                                         "text or {name, file, type?, filename?} for a "
                                         "file. Nothing was sent.")
        allowed = {"name", "value"} if "value" in part else {"name", "file", "type",
                                                             "filename"}
        if set(part) - allowed:
            raise BridgeError("invalid", "A part of \"multipart\" holds something it does "
                                         "not take. Nothing was sent. Text parts take name "
                                         "and value; file parts take name, file, type and "
                                         "filename.")
        name = check_part_name(part["name"], "A part's name")
        if "value" in part:
            if not isinstance(part["value"], str):
                raise BridgeError("invalid", "A text part's value is text. Nothing was "
                                             "sent. Send numbers as text.")
            out.append({"name": name, "value": part["value"]})
            continue
        path = check_body_path(part["file"])
        out.append({"name": name, "file": path,
                    "type": None if part.get("type") is None
                    else check_media_type(part["type"], "A file part's \"type\""),
                    "filename": check_part_name(part["filename"], "A file part's filename")
                    if part.get("filename") is not None else path.rsplit("/", 1)[-1]})
    return out


def is_file_mark(value: Any) -> bool:
    """Whether a JSON value is a file mark: ``{"$file": path, "as": ..., "type"?}``."""
    return isinstance(value, dict) and "$file" in value and set(value) <= _MARK_KEYS


def check_mark(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("as") not in ("data_url", "base64"):
        raise BridgeError("invalid", "A file in \"json\" says how it is sent: \"as\": "
                                     "\"data_url\" or \"as\": \"base64\". Nothing was sent.")
    return {"file": check_body_path(value["$file"]), "as": value["as"],
            "type": None if value.get("type") is None
            else check_media_type(value["type"], "A file's \"type\" in \"json\"")}


def find_marks(value: Any, found: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every file mark in a JSON value, in the order JSON writes them."""
    found = [] if found is None else found
    if is_file_mark(value):
        found.append(check_mark(value))
    elif isinstance(value, dict):
        for item in value.values():
            find_marks(item, found)
    elif isinstance(value, list):
        for item in value:
            find_marks(item, found)
    return found


def _missing() -> BridgeError:
    return BridgeError("not_found", "There is no such file in your app's data folder, so "
                                    "nothing was sent. Use the ref an upload returned, or a "
                                    "path your program wrote.")


def locate(folder: Path, relative: str) -> Path:
    """The ordinary file ``relative`` names inside ``folder``: no link anywhere on the
    way, the folder itself included, and resolving inside it."""
    link = BridgeError("not_allowed", "That path goes through a link, which JimmyHQ does "
                                      "not follow. Nothing was sent. Keep the real file in "
                                      "your app's data folder.")
    for step in (folder.parent, folder):
        if step.is_symlink():
            raise link
    here = folder
    for segment in relative.split("/"):
        here = here / segment
        if here.is_symlink():
            raise link
    try:
        info = here.lstat()
    except OSError:
        raise _missing() from None
    if not stat.S_ISREG(info.st_mode):
        raise _missing()
    real = here.resolve()
    if not real.is_relative_to(folder.resolve()):
        raise link  # a junction or a mount point on the way
    return real


def check_file_size(size: int, most: int) -> None:
    if size > most:
        raise BridgeError("too_large", f"A file can be at most {most // (1024 * 1024)} MB "
                                       "in one request, and this one is larger, so nothing "
                                       "was sent. Send a smaller one, or ask the owner to "
                                       "raise \"Largest file an app may send (MB)\" under "
                                       "Settings, Apps.")


def check_body_size(size: int, most: int) -> None:
    if size > most:
        raise BridgeError("too_large", f"This request would weigh more than the "
                                       f"{most // (1024 * 1024)} MB an app may send in one, "
                                       "so nothing was sent. Send fewer or smaller files at a "
                                       "time, or ask the owner to raise \"Largest request "
                                       "with files an app may send (MB)\" under Settings, "
                                       "Apps.")


def base64_length(size: int) -> int:
    return 4 * ((size + 2) // 3)


# --- the pieces of a body -------------------------------------------------------------------


@dataclass
class OpenFile:
    """One file, open, with its size fixed when it was opened."""

    ref: str
    handle: BinaryIO
    size: int

    def head(self, n: int = 32) -> bytes:
        data = self.handle.read(n)
        self.handle.seek(0)
        return data

    async def raw(self) -> AsyncIterator[bytes]:
        await asyncio.to_thread(self.handle.seek, 0)  # a file may appear twice in a body
        left = self.size
        while left > 0:
            piece = await asyncio.to_thread(self.handle.read, min(CHUNK, left))
            if not piece:
                raise OSError("the file got shorter while it was sent")
            left -= len(piece)
            yield piece

    async def encoded(self) -> AsyncIterator[bytes]:
        async for piece in self.raw():
            yield base64.b64encode(piece)


def open_file(folder: Path, ref: str, most: int) -> OpenFile:
    found = locate(folder, ref)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(found, flags)
    except OSError:
        raise _missing() from None
    handle = os.fdopen(fd, "rb")
    try:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise _missing()
        check_file_size(info.st_size, most)
    except BaseException:
        handle.close()
        raise
    return OpenFile(ref=ref, handle=handle, size=info.st_size)


def sniffed_type(file: OpenFile) -> str:
    """What a file's first bytes say it is, when it is a picture; otherwise bytes."""
    from jimmyhq.apps import uploads  # noqa: PLC0415 - it imports bridge, which imports us

    ending = uploads.sniff_image(file.head())
    return uploads.TYPES[ending] if ending else "application/octet-stream"


Piece = bytes | tuple[str, OpenFile]  # ("raw" | "base64", file)


@dataclass
class Body:
    """A body to stream: fixed bytes and files, in order, with its whole length known."""

    form: str
    pieces: list[Piece]
    files: list[OpenFile]
    content_type: str | None
    _closed: bool = field(default=False, repr=False)

    @property
    def length(self) -> int:
        total = 0
        for piece in self.pieces:
            if isinstance(piece, bytes):
                total += len(piece)
            else:
                how, file = piece
                total += file.size if how == "raw" else base64_length(file.size)
        return total

    async def chunks(self) -> AsyncIterator[bytes]:
        for piece in self.pieces:
            if isinstance(piece, bytes):
                if piece:
                    yield piece
                continue
            how, file = piece
            async for chunk in (file.raw() if how == "raw" else file.encoded()):
                yield chunk

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            for file in self.files:
                file.handle.close()


def _opened(core: Core, app_id: str, refs: list[str]) -> dict[str, OpenFile]:
    """Each named file once, open; on any refusal the ones already opened are closed."""
    if len(dict.fromkeys(refs)) > MAX_FILES:
        raise BridgeError("invalid", f"One request can carry at most {MAX_FILES} files. "
                                     "Nothing was sent. Send them in more than one.")
    folder = appdata.data_dir(core, app_id)
    most = int(core.settings.get(MAX_MB)) * 1024 * 1024
    out: dict[str, OpenFile] = {}
    try:
        for ref in refs:
            if ref not in out:
                out[ref] = open_file(folder, ref, most)
    except BaseException:
        for file in out.values():
            file.handle.close()
        raise
    return out


def raw_body(core: Core, app_id: str, ref: Any, headers: dict[str, str]) -> Body:
    ref = check_body_path(ref)
    files = _opened(core, app_id, [ref])
    given = next((v for k, v in headers.items() if k.lower() == "content-type"), None)
    kind = None if given == "" else (given or "application/octet-stream")
    return Body("raw", [("raw", files[ref])], list(files.values()), kind)


def _multipart(parts: list[dict[str, Any]], files: dict[str, OpenFile]) -> tuple[list[Piece],
                                                                                 str]:
    boundary = "jimmyhq-" + secrets.token_hex(16)
    pieces: list[Piece] = []
    for part in parts:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{part["name"]}"'
        if "value" in part:
            pieces.append(f"{head}\r\n\r\n".encode() + part["value"].encode("utf-8")
                          + b"\r\n")
            continue
        file = files[part["file"]]
        kind = part["type"] or sniffed_type(file)
        pieces.append(f'{head}; filename="{part["filename"]}"\r\nContent-Type: {kind}\r\n'
                      "\r\n".encode())
        pieces.append(("raw", file))
        pieces.append(b"\r\n")
    pieces.append(f"--{boundary}--\r\n".encode())
    return pieces, f"multipart/form-data; boundary={boundary}"


def multipart_body(core: Core, app_id: str, value: Any) -> Body:
    parts = check_parts(value)
    files = _opened(core, app_id, [p["file"] for p in parts if "file" in p])
    try:
        pieces, kind = _multipart(parts, files)
    except BaseException:
        for file in files.values():
            file.handle.close()
        raise
    return Body("multipart", pieces, list(files.values()), kind)


def _swap_marks(value: Any, tokens: list[str]) -> Any:
    if is_file_mark(value):
        token = f"jimmyhq-file-{secrets.token_hex(12)}"
        tokens.append(token)
        return token
    if isinstance(value, dict):
        return {k: _swap_marks(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap_marks(v, tokens) for v in value]
    return value


def json_body(core: Core, app_id: str, value: Any) -> Body:
    """A JSON body whose file marks are filled in with each file's base64 as it is sent.
    The rest of the JSON is written by JimmyHQ, so the app cannot break it."""
    marks = find_marks(value)
    tokens: list[str] = []
    text = appdata.encode(_swap_marks(value, tokens), "\"json\"")
    files = _opened(core, app_id, [m["file"] for m in marks])
    try:
        pieces: list[Piece] = []
        rest = text
        for mark, token in zip(marks, tokens, strict=True):
            before, quoted, rest = rest.partition(json.dumps(token))
            if not quoted:  # cannot happen: each token is random and written once
                raise BridgeError("internal", "JimmyHQ could not build that request. "
                                              "Nothing was sent.")
            file = files[mark["file"]]
            lead = '"'
            if mark["as"] == "data_url":
                lead += f"data:{mark['type'] or sniffed_type(file)};base64,"
            pieces.extend([(before + lead).encode("utf-8"), ("base64", file), b'"'])
        pieces.append(rest.encode("utf-8"))
    except BaseException:
        for file in files.values():
            file.handle.close()
        raise
    return Body("json", pieces, list(files.values()), "application/json")


def has_files(args: dict[str, Any]) -> bool:
    """Whether this fetch's body is one JimmyHQ builds from files."""
    return (args.get("file") is not None or args.get("multipart") is not None
            or (args.get("json") is not None and bool(find_marks(args["json"]))))


def build(core: Core, app_id: str, args: dict[str, Any], headers: dict[str, str]) -> Body:
    """The body a fetch's arguments describe, with every file open and every limit
    checked, or a ``BridgeError`` with nothing open."""
    if args.get("file") is not None:
        body = raw_body(core, app_id, args["file"], headers)
    elif args.get("multipart") is not None:
        if any(k.lower() == "content-type" for k in headers):
            raise BridgeError("invalid", "A multipart request's Content-Type is set by "
                                         "JimmyHQ, with its boundary. Nothing was sent. "
                                         "Leave that header out.")
        body = multipart_body(core, app_id, args["multipart"])
    else:
        body = json_body(core, app_id, args["json"])
    try:
        check_body_size(body.length, int(core.settings.get(BODY_MAX_MB)) * 1024 * 1024)
    except BaseException:
        body.close()
        raise
    return body


def body_headers(headers: dict[str, str], body: Body, fresh: bool) -> dict[str, str]:
    """The headers a built body is sent with: the app's own, the length and the content
    type set by JimmyHQ, and ``Connection: close`` when asked."""
    out = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    if body.content_type is not None:
        out["Content-Type"] = body.content_type
    out["Content-Length"] = str(body.length)
    if fresh:
        out["Connection"] = "close"
    return out


def sent(core: Core, app_id: str, actor: str, host: str, method: str, body: Body) -> None:
    """The receipt: where, how, which files and how much. Never what was in them."""
    core.tape.print("apps.file_sent", {
        "app_id": app_id, "host": host, "method": method, "form": body.form,
        "size": body.length, "type": (body.content_type or "")[:100] or None,
        "files": [{"ref": f.ref, "size": f.size} for f in body.files],
    }, actor=actor, subject=kinds.subject(app_id))
