"""The gallery: keeping pictures, finding them, starring and deleting them, and the limits.

A picture's file is ``uploads/<32 letters and digits>.<png|jpg|webp>`` in the app's data
folder, which is where JimmyHQ's page helper can show a small version of it
(``hq.files.thumbnail``); its details are a row in the store. Deleting overwrites the file
with zeros, removes it, and overwrites the row in the database: nothing is left.

The limits are ``max_pictures`` and ``max_total_mb``. When a new picture takes the gallery
over one, the oldest pictures the owner has not starred are deleted until it is inside
again, never the one just made, and Imagery says so. A gallery full of starred pictures
cannot make room, and refuses new ones (``room_problem``) before anything is sent.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from jimmyhq_app import CallError

from imagery import png
from imagery.context import Ctx
from imagery.rules import Refused, whole

MB = 1024 * 1024
ROW_FIELDS = ("id", "ref", "kind", "prompt", "style", "provider", "model", "aspect", "mime",
              "bytes", "width", "height", "said", "created")


def uploads_dir(ctx: Ctx) -> Path:
    data = ctx.app.data_dir
    if data is None:
        raise RuntimeError("Imagery has no data folder: start it from JimmyHQ, or set "
                           "JIMMYHQ_APP_DATA while developing.")
    folder = Path(data) / "uploads"
    if folder.is_symlink():
        raise Refused("Imagery's pictures folder is a link, which is not followed. Nothing "
                      "was kept.")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def row_of(pic: dict[str, Any]) -> dict[str, Any]:
    out = {k: pic[k] for k in ROW_FIELDS}
    out["starred"] = bool(pic["starred"])
    return out


def counts(ctx: Ctx) -> dict[str, int]:
    store, cfg = ctx.store, ctx.cfg()
    return {"pictures": store.number("SELECT COUNT(*) FROM picture"),
            "starred": store.number("SELECT COUNT(*) FROM picture WHERE starred=1"),
            "bytes": store.number("SELECT COALESCE(SUM(bytes),0) FROM picture"),
            "max_pictures": int(cfg.max_pictures),
            "max_bytes": int(cfg.max_total_mb) * MB}


def keep(ctx: Ctx, data: bytes, mime: str, *, kind: str, prompt: str = "", style: str = "",
         provider: str = "", model: str = "", aspect: str = "", said: str = "",
         run_id: int | None = None) -> dict[str, Any]:
    """Write the file and its row. The file goes down whole or not at all."""
    ending = png.ENDINGS.get(mime)
    if ending is None or png.kind_of(data[:16]) != ending:
        raise Refused("That is not a PNG, JPEG or WebP picture, so nothing was kept.")
    folder = uploads_dir(ctx)
    token = secrets.token_hex(16)
    part = folder / f"{token}.part"
    final = folder / f"{token}.{ending}"
    try:
        part.write_bytes(data)
        part.replace(final)
    except OSError:
        part.unlink(missing_ok=True)
        raise Refused("Imagery could not write the picture to this computer's disk, so "
                      "nothing was kept. Check there is room, then try again.") from None
    size = png.size_of(data)
    new_id = ctx.store.insert("picture", {
        "ref": f"uploads/{token}.{ending}", "kind": kind, "prompt": prompt, "style": style,
        "provider": provider, "model": model, "aspect": aspect, "mime": mime,
        "bytes": len(data), "width": size[0] if size else None,
        "height": size[1] if size else None, "said": said[:300], "run_id": run_id,
        "created": ctx.now()})
    return ctx.store.picture(new_id) or {}


def wipe_file(ctx: Ctx, ref: str) -> None:
    """Overwrite a picture's file with zeros and remove it. A file already gone is fine."""
    try:
        path = ctx.app.files.path(ref)
    except (ValueError, FileNotFoundError):
        return
    try:
        size = path.stat().st_size
        with path.open("r+b") as handle:
            handle.write(b"\0" * size)
            handle.flush()
    except OSError:
        pass
    try:
        path.unlink()
    except OSError:
        pass


def forget(ctx: Ctx, pic: dict[str, Any]) -> None:
    """Delete a picture for good: its file and its row."""
    wipe_file(ctx, pic["ref"])
    store = ctx.store
    store.forget_picture(pic["id"])
    run_id = pic.get("run_id")
    if run_id is not None:
        # The run holds the same words. Once none of its pictures is left, it goes too, so
        # a deleted picture's description is not kept anywhere.
        run = store.run(run_id)
        if run and run["state"] not in ("queued", "working") and not store.number(
                "SELECT COUNT(*) FROM picture WHERE run_id=?", (run_id,)):
            store.forget_run(run_id)


def prune(ctx: Ctx, spare_id: int | None = None) -> int:
    """Delete the oldest unstarred pictures (never ``spare_id``) until the gallery is inside
    its limits. Returns how many were deleted, and remembers that for the page to say."""
    store = ctx.store
    gone = 0
    while True:
        now = counts(ctx)
        if now["pictures"] <= now["max_pictures"] and now["bytes"] <= now["max_bytes"]:
            break
        oldest = store.one("SELECT * FROM picture WHERE starred=0 AND id IS NOT ? ORDER BY "
                           "created, id LIMIT 1", (spare_id,))
        if oldest is None:
            break
        forget(ctx, oldest)
        gone += 1
    if gone:
        earlier = store.get_meta("pruned") or {}
        store.set_meta(pruned={"count": int(earlier.get("count", 0)) + gone, "ms": ctx.now()})
    return gone


def room_problem(ctx: Ctx) -> str | None:
    """The sentence that says the gallery cannot take another picture, or None. It cannot
    when the starred pictures alone fill a limit, since nothing may be deleted to make room."""
    now = counts(ctx)
    store = ctx.store
    starred = store.number("SELECT COUNT(*) FROM picture WHERE starred=1")
    starred_bytes = store.number("SELECT COALESCE(SUM(bytes),0) FROM picture WHERE starred=1")
    if starred >= now["max_pictures"] or starred_bytes >= now["max_bytes"]:
        return ("Your starred pictures fill the gallery up to your limits, and Imagery "
                "never deletes a starred picture to make room. Nothing was made. Delete or "
                "unstar some pictures, or raise \"Most pictures to keep\" or \"Most space "
                "for pictures\" in Imagery's settings.")
    return None


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def listing(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Pictures, newest first, whose description holds every word searched for."""
    store, cfg = ctx.store, ctx.cfg()
    words = str(args.get("query") or "").split()[:8]
    where = " AND ".join("prompt LIKE ? ESCAPE '\\'" for _ in words) or "1=1"
    params = [_like(w) for w in words]
    if args.get("starred") is True:
        where += " AND starred=1"
    total = store.number(f"SELECT COUNT(*) FROM picture WHERE {where}", params)  # noqa: S608
    limit = min(max(whole(args.get("limit") or cfg.page_size, "limit"), 1), 200)
    offset = max(whole(args.get("offset") or 0, "offset"), 0)
    rows = store.all(f"SELECT * FROM picture WHERE {where} ORDER BY created DESC, id DESC "  # noqa: S608
                     "LIMIT ? OFFSET ?", [*params, limit, offset])
    return {"rows": [row_of(r) for r in rows], "total": total}


def find(ctx: Ctx, args: dict[str, Any], key: str = "id") -> dict[str, Any]:
    pic = ctx.store.picture(whole(args.get(key), "picture"))
    if pic is None:
        raise Refused("There is no such picture. It may have been deleted. Nothing was "
                      "changed.")
    return pic


def star(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    pic = find(ctx, args)
    on = args.get("on")
    on = (not pic["starred"]) if on is None else bool(on)
    ctx.store.update("picture", pic["id"], starred=1 if on else 0)
    return row_of(ctx.store.picture(pic["id"]) or pic)


def remove(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """Delete one picture for good, starred or not: the owner asked."""
    pic = find(ctx, args)
    forget(ctx, pic)
    ctx.emit()
    return {"said": "Deleted. The picture's file and its details are gone from this "
                    "computer."}


GIVEN_KINDS = ("png", "jpg", "webp")


def add_given(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The owner picked a picture to start from: the page has handed it to JimmyHQ, which
    kept it in this app's folder under ``ref``. It joins the gallery as the owner's own."""
    ref = str(args.get("ref") or "")
    held = ctx.store.one("SELECT * FROM picture WHERE ref=?", (ref,))
    if held is not None:
        # The same picture handed over twice (a second click, or a page that sends a ref
        # it already has): it is already in the gallery, and its file is not touched. The
        # checks below would otherwise delete a picture the gallery still shows, or fail on
        # the unique ref after the file was read.
        return row_of(held)
    try:
        path = ctx.app.files.path(ref)
    except (ValueError, FileNotFoundError, CallError) as error:
        raise Refused(f"{error} Nothing was added.") from None
    most = int(ctx.cfg().reference_max_mb) * MB
    size = path.stat().st_size
    with path.open("rb") as handle:
        kind = png.kind_of(handle.read(16))
    problem = None
    if kind not in GIVEN_KINDS:
        problem = ("Only PNG, JPEG and WebP pictures can be used as a starting picture, and "
                   "this one is another kind.")
    elif size > most:
        problem = (f"That picture is {size / MB:.1f} MB, and a starting picture may be "
                   f"{most // MB} MB at most (see \"Largest starting picture\" in Imagery's "
                   "settings).")
    if problem:
        wipe_file(ctx, ref)
        raise Refused(f"{problem} Nothing was added.")
    room = room_problem(ctx)
    if room:
        wipe_file(ctx, ref)
        raise Refused(room.replace("Nothing was made.", "Nothing was added."))
    data = path.read_bytes()
    dims = png.size_of(data)
    new_id = ctx.store.insert("picture", {
        "ref": ref, "kind": "given", "prompt": str(args.get("name") or "")[:120], "mime":
        png.TYPES[kind or "png"], "bytes": len(data), "width": dims[0] if dims else None,
        "height": dims[1] if dims else None, "created": ctx.now()})
    prune(ctx, spare_id=new_id)
    ctx.emit()
    return row_of(ctx.store.picture(new_id) or {})
