"""Pictures as a way in: the owner uploads pictures with notes, a model that can see
looks at each one, and a caption is drafted in the page's voice from the notes and what
was seen. From there it is a post like any other: the same checks, the same draft card,
the same queue.

- **Taken in** (``check_pictures``): only pictures the owner uploaded through the page
  (``hq.files.upload``), read from their own first bytes; only PNG, JPEG and WebP, the
  kinds a model can be shown. Anything else is refused with a sentence.
- **Looked at** (``analyse_step``, piece state ``analysing``), one picture a run: the
  owner's ``vision`` role describes it in a fixed shape that has no place for who a
  person is. With no model that can see pictures, the piece stops and says so: a
  picture is never captioned without being looked at.
- **Compared** (``likeness``): a difference hash of each picture against the pictures of
  the page's recent posts. A close one is a warning on the draft, never a block.
- **Captioned** (piece state ``captioning``): ``plain.write_step`` with the caption
  prompt; the descriptions go to the model as untrusted text, the owner's notes as theirs.
- **Tidied** (``tidy``): a picture's file is deleted ``picture_keep_days`` after its post
  went out, was declined or its piece was dropped; its hash is kept for comparing.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from jimmyhq_app import CallError

from posts import imagehash, similar, states
from posts.context import Ctx

log = logging.getLogger("posts")

DAY_MS = 86_400_000
HEAD_BYTES = 16
MAX_NOTE = 1000
MAX_FIELD = 400
MAX_OBJECTS = 12

# What the first bytes say a file is. Only the first three can be shown to a model.
FOR_MODELS = ("png", "jpg", "webp")
_KIND_WORDS = {"gif": "a GIF", "avif": "an AVIF picture",
               "heic": "a HEIC picture (the kind a phone often saves)"}


class PictureError(ValueError):
    """The pictures cannot be taken. The message says what to do; nothing was started."""


def sniff(head: bytes) -> str | None:
    """What kind of picture these first bytes are, as JimmyHQ reads them, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return "avif"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1"):
        return "heic"
    return None


# --- taking them in -------------------------------------------------------------------------


def check_pictures(ctx: Ctx, given: Any, most: int) -> list[dict[str, Any]]:
    """The owner's pictures as ``[{ref, note, sha256}]``, each proven to be a picture this
    app keeps and a model can be shown, or ``PictureError``."""
    if not isinstance(given, list) or not given:
        raise PictureError("Choose at least one picture to start from. Nothing was started.")
    if len(given) > most:
        raise PictureError(f"You chose {len(given)} pictures, and a piece starts from at most "
                           f"{most}. Choose fewer, or raise \"Pictures in one piece\" under "
                           "Posts' settings. Nothing was started.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n, item in enumerate(given, 1):
        ref = item.get("ref") if isinstance(item, dict) else None
        note = str((item.get("note") if isinstance(item, dict) else "") or "")
        note = note.replace("\r\n", "\n").strip()
        if len(note) > MAX_NOTE:
            raise PictureError(f"Your note on picture {n} is {len(note)} characters; keep it "
                               f"to {MAX_NOTE}. Nothing was started.")
        try:
            path = ctx.app.files.path(str(ref or ""))
        except ValueError:
            raise PictureError(f"Picture {n} was not uploaded through this page, so Posts "
                               "cannot find it. Upload it again. Nothing was started.") from None
        except (FileNotFoundError, OSError):
            raise PictureError(f"Picture {n} is no longer kept, so Posts cannot use it. "
                               "Upload it again. Nothing was started.") from None
        data = path.read_bytes()
        kind = sniff(data[:HEAD_BYTES])
        if kind is None:
            raise PictureError(f"Picture {n} is not a picture Posts can read. Upload a JPEG, "
                               "PNG or WebP picture. Nothing was started.")
        if kind not in FOR_MODELS:
            raise PictureError(f"Picture {n} is {_KIND_WORDS.get(kind, kind)}, which a model "
                               "cannot be shown. Save it as JPEG or PNG and upload it again. "
                               "Nothing was started.")
        if ref in seen:
            continue  # the same upload twice is one picture
        seen.add(str(ref))
        out.append({"ref": str(ref), "note": note,
                    "sha256": hashlib.sha256(data).hexdigest()})
    return out


def keep(ctx: Ctx, piece_id: int, pictures: list[dict[str, Any]]) -> None:
    for picture in pictures:
        ctx.store.insert("picture", {"piece_id": piece_id, "ref": picture["ref"],
                                     "sha256": picture["sha256"], "note": picture["note"],
                                     "created": ctx.now()})


def of_piece(ctx: Ctx, piece_id: int) -> list[dict[str, Any]]:
    return ctx.store.all("SELECT * FROM picture WHERE piece_id=? ORDER BY id", (piece_id,))


# --- looking at them ------------------------------------------------------------------------

# The shape a description takes. It has no place for who a person is, on purpose.
ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "what": {"type": "string"},
        "setting": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "text_seen": {"type": "string"},
        "people": {"type": "integer"},
        "mood": {"type": "string"},
    },
    "required": ["what", "setting", "objects", "text_seen", "people", "mood"],
    "additionalProperties": False,
}

ANALYSE_PROMPT = (
    "Describe this picture for someone who will write a social media post about it. Say "
    "what is in it in one sentence, the setting, the main things in it, any text you can "
    "read in it (copied exactly, or empty), how many people are in it, and its mood. Never "
    "say or guess who a person is: no names, no roles, no relationships. Describe only what "
    "can be seen, such as \"a person in a red coat\". Answer with the JSON asked for.")


def take_analysis(data: Any) -> dict[str, Any]:
    """What a model said about a picture, kept only in the shape asked for: anything
    else it added (a name, a guess at who someone is) is not kept."""
    data = data if isinstance(data, dict) else {}

    def text(key: str) -> str:
        return " ".join(str(data.get(key) or "").split())[:MAX_FIELD]

    objects = data.get("objects") if isinstance(data.get("objects"), list) else []
    try:
        people = max(0, int(data.get("people") or 0))
    except (TypeError, ValueError):
        people = 0
    return {"what": text("what"), "setting": text("setting"),
            "objects": [" ".join(str(o).split())[:80] for o in objects][:MAX_OBJECTS],
            "text_seen": text("text_seen"), "people": people, "mood": text("mood")}


def no_vision(n: int, said: str) -> str:
    return (f"No model that can see pictures answered, so Posts has not looked at picture "
            f"{n} and will not caption it blind. JimmyHQ said: {said} Choose a model for "
            "pictures under Settings, Models.")


def analyse_step(ctx: Ctx, piece: dict[str, Any]) -> str:
    """Look at the next picture not yet looked at; once all are, go on to the overlap
    check. One picture a run, so a large picture never holds up the job."""
    from posts.plain import StepError  # noqa: PLC0415 - plain imports this module

    pictures = [p for p in of_piece(ctx, piece["id"]) if p["deleted"] is None]
    if not pictures:
        raise StepError("This piece has no pictures left to look at.")
    for n, picture in enumerate(pictures, 1):
        if picture["analysis"] is not None:
            continue
        try:
            reply = ctx.app.model.ask(ANALYSE_PROMPT, role="vision", images=[picture["ref"]],
                                      json_schema=ANALYSIS_SCHEMA)
        except CallError as error:
            raise StepError(no_vision(n, error.message)) from None
        analysis = take_analysis(_reply_data(reply))
        if not analysis["what"]:
            raise StepError(f"The model gave back no description of picture {n}.")
        try:
            found = imagehash.dhash(ctx.app.files.path(picture["ref"]))
        except (ValueError, OSError):
            found = None
        ctx.store.update("picture", picture["id"], {"analysis": analysis, "dhash": found})
        ctx.store.add_turn(piece["id"], "analysing", f"Looked at picture {n}: "
                           f"{analysis['what']}"[:300], role="vision",
                           model=getattr(reply, "model", "") or "",
                           local=getattr(reply, "local", None))
        if n < len(pictures):
            return f"Looked at picture {n} of {len(pictures)}."
    whats = "; ".join(p["analysis"]["what"] if p["analysis"] else ""
                      for p in of_piece(ctx, piece["id"]))
    ctx.store.move_piece(piece["id"], "overlap_check",
                         source_title=f"Pictures: {whats}"[:300])
    said = f"Looked at all {len(pictures)} picture{'s' if len(pictures) != 1 else ''}."
    ctx.store.add_turn(piece["id"], "analysing", said)
    return said


def _reply_data(reply: Any) -> Any:
    import json  # noqa: PLC0415

    if getattr(reply, "data", None) is not None:
        return reply.data
    try:
        return json.loads(reply.text)
    except (TypeError, ValueError):
        return None


# --- what the caption is written from -------------------------------------------------------


def describe(n: int, picture: dict[str, Any]) -> str:
    a = picture["analysis"] or {}
    parts = [f"Picture {n}: {a.get('what', '')}"]
    if a.get("setting"):
        parts.append(f"setting: {a['setting']}")
    if a.get("objects"):
        parts.append("things in it: " + ", ".join(a["objects"]))
    if a.get("text_seen"):
        parts.append(f"text in it: {a['text_seen']}")
    parts.append(f"people: {a.get('people', 0)}")
    if a.get("mood"):
        parts.append(f"mood: {a['mood']}")
    return "; ".join(parts)


def blocks(ctx: Ctx, piece: dict[str, Any]) -> list[str]:
    """The untrusted text a caption is written and checked from: what the model saw."""
    if piece["way"] != "pictures":
        return []
    lines = [describe(n, p) for n, p in enumerate(of_piece(ctx, piece["id"]), 1)
             if p["analysis"]]
    return ["What a model saw in the owner's pictures:\n" + "\n".join(lines)] if lines else []


def owner_notes(ctx: Ctx, piece: dict[str, Any]) -> list[str]:
    """The owner's own note on each picture, in their words."""
    if piece["way"] != "pictures":
        return []
    return [f"Picture {n}: {p['note']}" for n, p in enumerate(of_piece(ctx, piece["id"]), 1)
            if p["note"]]


def for_post(ctx: Ctx, post_id: int) -> list[dict[str, str]]:
    """The pictures a post goes out with, in the order the owner gave them:
    ``[{ref, name}]``, where ``ref`` is the upload inside the app's data folder and
    ``name`` a safe file name made from its place and ending. Empty for a post with none
    (and once its pictures were deleted). The booking code reads this."""
    rows = ctx.store.all("SELECT ref FROM picture WHERE post_id=? AND deleted IS NULL "
                         "AND ref IS NOT NULL AND ref != '' ORDER BY id", (post_id,))
    out = []
    for n, row in enumerate(rows, 1):
        ending = row["ref"].rsplit(".", 1)[-1] if "." in row["ref"] else "jpg"
        out.append({"ref": row["ref"], "name": f"picture-{n}.{ending}"})
    return out


def link_post(ctx: Ctx, piece_id: int, post_id: int) -> None:
    ctx.store.conn.execute("UPDATE picture SET post_id=? WHERE piece_id=?", (post_id, piece_id))
    ctx.store.conn.commit()


# --- "looks like the post of last week" -----------------------------------------------------


def likeness(ctx: Ctx, piece: dict[str, Any]) -> list[dict[str, Any]]:
    """For each picture of the piece: whether it could be compared, and the closest
    picture of the page's posts going out or sent within ``dup_days``, when it is within
    ``picture_like_bits``. Measured afresh each time, so it follows what went out since."""
    cfg = ctx.cfg()
    since = ctx.now() - int(cfg.dup_days) * DAY_MS
    marks = ",".join("?" * len(states.POST_OUT))
    earlier = ctx.store.all(
        f"SELECT p.dhash, post.id AS post_id, post.title, "  # noqa: S608
        f"COALESCE(post.slot_ms, post.updated) AS when_ms FROM picture p "
        f"JOIN post ON post.id = p.post_id WHERE post.page_id=? AND post.state IN ({marks}) "
        "AND p.dhash IS NOT NULL AND p.piece_id != ? AND COALESCE(post.slot_ms, post.updated) "
        ">= ?", (piece["page_id"], *sorted(states.POST_OUT), piece["id"], since))
    most = int(cfg.picture_like_bits)
    out = []
    for n, picture in enumerate(of_piece(ctx, piece["id"]), 1):
        best = None
        if picture["dhash"]:
            for other in earlier:
                gap = imagehash.distance(picture["dhash"], other["dhash"])
                if gap <= most and (best is None or gap < best["distance"]):
                    best = {**other, "distance": gap}
        looks_like = None
        if best:
            name = f" (\"{best['title']}\")" if best["title"] else ""
            looks_like = (f"Picture {n} looks like a picture in the post of "
                          f"{similar.ago(best['when_ms'], ctx.now())}{name}.")
        out.append({"n": n, "id": picture["id"], "hashed": bool(picture["dhash"]),
                    "looked_at": picture["analysis"] is not None,
                    "looks_like": looks_like, "post": best["post_id"] if best else None})
    return out


def view(ctx: Ctx, piece: dict[str, Any]) -> list[dict[str, Any]]:
    """The pictures as the page shows them: refs only, never the bytes."""
    like = {row["id"]: row for row in likeness(ctx, piece)} if piece["way"] == "pictures" \
        else {}
    return [{"id": p["id"], "ref": None if p["deleted"] else p["ref"], "note": p["note"],
             "analysis": p["analysis"], "looks_like": like.get(p["id"], {}).get("looks_like"),
             "deleted": p["deleted"] is not None}
            for p in of_piece(ctx, piece["id"])]


# --- tidying up -----------------------------------------------------------------------------


def done_since(row: dict[str, Any]) -> int | None:
    """When the picture's post stopped needing it, or None while it still may."""
    if row["post_state"] == "went_out":
        return row["out_at"]
    if row["post_state"] == "declined":
        return row["post_updated"]
    if row["piece_state"] == "dropped" and row["post_state"] not in states.POST_OUT:
        return row["piece_updated"]
    return None


def tidy(ctx: Ctx) -> int:
    """Delete the files of pictures no post needs any more, ``picture_keep_days`` after
    that; the row and its hash stay. Returns how many were deleted."""
    keep_ms = int(ctx.cfg().picture_keep_days) * DAY_MS
    rows = ctx.store.all(
        "SELECT p.id, p.ref, post.state AS post_state, post.updated AS post_updated, "
        "COALESCE(post.slot_ms, post.updated) AS out_at, piece.state AS piece_state, "
        "piece.updated AS piece_updated FROM picture p JOIN piece ON piece.id = p.piece_id "
        "LEFT JOIN post ON post.id = p.post_id WHERE p.deleted IS NULL")
    count = 0
    for row in rows:
        since = done_since(row)
        if since is None or ctx.now() < since + keep_ms:
            continue
        try:
            ctx.app.files.path(row["ref"]).unlink()
        except FileNotFoundError:
            pass  # already gone: the record still says so
        except (ValueError, OSError) as error:
            log.warning("could not delete picture %s: %s", row["id"], error)
            continue
        ctx.store.update("picture", row["id"], {"deleted": ctx.now()})
        count += 1
    return count
