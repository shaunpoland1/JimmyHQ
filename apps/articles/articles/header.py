"""A header image for an article: optional, and off until the owner sets it up.

Once an article is ready, the owner may ask for a header image (``make``):

1. The pre-checks, each a named function: header images are switched on
   (``header_images``); the article is ready, approved or sent; and the site has style
   guidance in the owner's own words (``set_style``). Articles has no style of its own and
   adds none: no look, no people, no subject the owner did not write.
2. A model (``header_role``) writes a short description of one picture from the title and
   the opening, which are data, never instructions, under the owner's guidance. It is told
   never to show a real person or anyone's likeness, and the code holds it to that
   (``check_description``): a description that names anyone or anything by a proper name,
   or asks for a portrait or a likeness, is refused and nothing is made.
3. An image provider renders it (``ImageProvider``). The platform has no call for making
   images yet, so the only provider is Practice: a plain placeholder made here, from
   nothing outside this computer, so the flow can be tried. A real provider plugs in by
   this interface once JimmyHQ offers one; nothing here calls an image service itself.
4. The picture is kept in the app's own storage as an upload (``uploads/<random>.png``),
   so the page shows it and the owner can download it (``hq.files.thumbnail``), and is
   recorded with its description. A newer one replaces the older.

Sending: WordPress uploads it to the same host the draft goes to and names it the draft's
featured image (``for_publication``). A Practice placeholder is never sent anywhere.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import struct
import zlib
from dataclasses import dataclass
from typing import Any, Protocol

from articles import render
from articles.context import Ctx

STYLE_MOST = 2000
DESCRIPTION_LEAST = 20
DESCRIPTION_MOST = 600
OPENING_CHARS = 700
STATES = ("ready", "approved", "sent")

SCHEMA = """
CREATE TABLE IF NOT EXISTS header_style (
  site_id INTEGER PRIMARY KEY, style TEXT NOT NULL, updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS header_image (
  id INTEGER PRIMARY KEY, piece_id INTEGER NOT NULL, site_id INTEGER NOT NULL,
  description TEXT NOT NULL, style TEXT NOT NULL, ref TEXT NOT NULL, type TEXT NOT NULL,
  provider TEXT NOT NULL, sendable INTEGER NOT NULL, width INTEGER NOT NULL,
  height INTEGER NOT NULL, created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS header_piece ON header_image (piece_id, id);
"""

DESCRIBE_SCHEMA = {"type": "object", "properties": {"description": {"type": "string"}},
                   "required": ["description"], "additionalProperties": False}

# Words that ask for someone's face or likeness. A safety rule, not a taste: never a setting.
_LIKENESS = re.compile(r"\b(portrait|likeness|selfie|headshot|celebrity|famous|lookalike|"
                       r"look-alike|resembl\w*|depict(?:ing|s)? (?:a |the )?real)\b", re.I)
_CAPITALISED = re.compile(r"\b[A-Z][A-Za-z'\u2019-]*\b")
_TITLE = re.compile(r"\b(Dr|Mr|Mrs|Ms|Mx|Prof|Sir|Dame|Lord|Lady|Rev|Sen|Rep|Gov|Pres)\b\.?"
                    r"(?=\s+[A-Z])")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;][\"\u201d')]?\s+)[\"\u201c(']?([A-Z])")


class HeaderError(ValueError):
    """No image was made. The message says why and what to do instead."""


# --- the pre-checks, one function each; None means it passes ------------------------------


def must_be_on(cfg: Any) -> str | None:
    if bool(cfg.header_images):
        return None
    return ("Header images are switched off, so nothing was made. Switch on \"Header images\" "
            "under Settings, Apps, Articles, and give the site its style guidance first.")


def must_be_ready(piece: dict[str, Any]) -> str | None:
    if piece["state"] in STATES:
        return None
    return ("A header image is made once the article is ready, so nothing was made. Wait "
            "until it is waiting for you.")


def must_have_style(style: str) -> str | None:
    """Rule ``the_style_is_the_owners``: without the owner's own words for how the site's
    pictures look, nothing is made: Articles has no style of its own to fall back on."""
    if style.strip():
        return None
    return ("This site has no style guidance for its header images, so nothing was made. "
            "Under Sites, edit the site and write, in your own words, how its pictures "
            "should look; Articles adds no style of its own.")


def check_description(text: Any) -> str:
    """Rule ``never_a_real_person``: one short description of a scene, naming no one and
    nothing by a proper name, asking for no portrait or likeness. Otherwise ``HeaderError``
    and nothing is made. (A capital letter that starts a sentence is fine.)"""
    if not isinstance(text, str):
        raise HeaderError("The model did not describe a picture, so nothing was made. Try "
                          "again, or change the style guidance.")
    text = " ".join(text.split())
    if not DESCRIPTION_LEAST <= len(text) <= DESCRIPTION_MOST:
        raise HeaderError(f"The model's description was not {DESCRIPTION_LEAST} to "
                          f"{DESCRIPTION_MOST} characters long, so nothing was made. Try again.")
    if _LIKENESS.search(text):
        raise HeaderError("The model's description asked for a portrait or someone's "
                          "likeness, which Articles never makes, so nothing was made. Try "
                          "again; if it keeps happening, say in the style guidance that the "
                          "pictures show places and things.")
    if _TITLE.search(text):
        raise HeaderError("The model's description named someone by a title such as Dr. or "
                          "Mr., and a header image never shows a real person or anyone's "
                          "likeness, so nothing was made. Try again.")
    starts = {m.start(1) for m in _SENTENCE_START.finditer(text)}
    for word in _CAPITALISED.finditer(text):
        if word.start() not in starts:
            raise HeaderError("The model's description named someone or something by name "
                              f"(“{word.group(0)}”), and a header image never shows a "
                              "real person or anyone's likeness, so nothing was made. Try "
                              "again.")
    return text


# --- the image provider ---------------------------------------------------------------------


@dataclass(frozen=True)
class ImageRequest:
    description: str  # what the model wrote, checked
    style: str  # the owner's own words, as he wrote them
    width: int
    height: int


@dataclass(frozen=True)
class Rendered:
    data: bytes
    type: str  # "image/png", "image/jpeg" or "image/webp"


class ImageProvider(Protocol):
    id: str
    label: str
    sends_out: bool  # whether what it is given leaves this computer
    sendable: bool  # whether its pictures may go on to the owner's site

    def needs(self) -> str | None: ...
    def render(self, request: ImageRequest) -> Rendered: ...


def png(width: int, height: int, rows: list[tuple[int, int, int]]) -> bytes:
    """A plain PNG: each row of pixels one colour, ``rows`` repeating down the picture."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + bytes(rows[y % len(rows)]) * width for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class PracticeImages:
    """A plain placeholder: two soft bands of colour chosen from the description, made
    here. Nothing is sent anywhere, and it is never sent on to a site."""

    id = "practice"
    label = "Practice"
    sends_out = False
    sendable = False

    def needs(self) -> str | None:
        return None

    def render(self, request: ImageRequest) -> Rendered:
        seed = hashlib.sha256(request.description.encode("utf-8")).digest()
        top = tuple(160 + b % 80 for b in seed[:3])
        low = tuple(120 + b % 80 for b in seed[3:6])
        band = max(1, request.height // 2)
        rows = [top] * band + [low] * (request.height - band)  # type: ignore[list-item]
        return Rendered(png(request.width, request.height, rows), "image/png")


def providers() -> dict[str, ImageProvider]:
    return {"practice": PracticeImages()}


# --- the records ----------------------------------------------------------------------------


def _ready(ctx: Ctx) -> None:
    ctx.store.conn.executescript(SCHEMA)
    ctx.store.conn.commit()


def style_of(ctx: Ctx, site_id: Any) -> str:
    _ready(ctx)
    row = ctx.store.one("SELECT style FROM header_style WHERE site_id=?", (site_id,))
    return str(row["style"]) if row else ""


def set_style(ctx: Ctx, site_id: Any, style: Any) -> dict[str, Any]:
    """The owner's own words for how one site's header images look. Kept as written."""
    if ctx.store.site(site_id) is None:
        raise HeaderError("There is no such site. Nothing was saved.")
    text = str(style or "").replace("\r\n", "\n").strip()
    if len(text) > STYLE_MOST:
        raise HeaderError(f"Style guidance is at most {STYLE_MOST} characters. Nothing was "
                          "saved.")
    _ready(ctx)
    ctx.store.conn.execute(
        "INSERT INTO header_style (site_id, style, updated) VALUES (?, ?, ?) ON CONFLICT"
        "(site_id) DO UPDATE SET style=excluded.style, updated=excluded.updated",
        (int(site_id), text, ctx.now()))
    ctx.store.conn.commit()
    return {"site_id": int(site_id), "style": text}


def latest(ctx: Ctx, piece_id: Any) -> dict[str, Any] | None:
    _ready(ctx)
    return ctx.store.one("SELECT * FROM header_image WHERE piece_id=? ORDER BY id DESC "
                         "LIMIT 1", (piece_id,))


# --- making one -----------------------------------------------------------------------------


def describe_prompt(title: str, opening: str) -> tuple[str, str, list[str]]:
    """The model's instructions, the owner's guidance and the article: the instructions
    and the guidance as the system's words, the title and opening as data."""
    prompt = ("Describe one picture to head this article, in one to three plain sentences: "
              "what is in it and how it is arranged. Follow the site's style guidance below, "
              "which is the owner's own. Show places, things, plants, animals or hands at "
              "work. Never show a real person, a public figure, or anyone's face or "
              "likeness, and name no one: no proper names at all. Put no words or letters "
              "in the picture. Answer as JSON: {\"description\": \"...\"}.")
    return prompt, title, [f"The article's title: {title}", f"The article's opening: {opening}"]


def make(ctx: Ctx, piece_id: int, *, provider: ImageProvider | None = None
         ) -> dict[str, Any]:
    """Ask for one header image for a ready article, if every rule allows it."""
    cfg = ctx.cfg()
    piece = ctx.store.piece(piece_id)
    if piece is None:
        raise HeaderError("There is no such article. Nothing was made.")
    style = style_of(ctx, piece["site_id"])
    refused = [f for f in (must_be_on(cfg), must_be_ready(piece), must_have_style(style)) if f]
    if refused:
        raise HeaderError(" ".join(dict.fromkeys(refused)))
    chosen = provider or providers().get(str(cfg.header_provider))
    if chosen is None:
        raise HeaderError("The image provider chosen under Settings, Apps, Articles is not "
                          "known, so nothing was made. Choose Practice.")
    lacks = chosen.needs()
    if lacks:
        raise HeaderError(f"{lacks} Nothing was made.")
    revision = ctx.store.revision(piece_id) or {}
    title = str(revision.get("title") or piece["topic"])
    opening = render.plain_text(str(revision.get("body") or ""))[:OPENING_CHARS]
    prompt, _, untrusted = describe_prompt(title, opening)
    system = ("The site's style guidance for header images, in the owner's own words:\n"
              f"{style}")
    reply = ctx.app.model.ask(prompt, role=str(cfg.header_role), system=system,
                              json_schema=DESCRIBE_SCHEMA, untrusted=untrusted)
    data = reply.data if isinstance(reply.data, dict) else {}
    description = check_description(data.get("description"))
    width, height = int(cfg.header_width), int(cfg.header_height)
    made = chosen.render(ImageRequest(description, style, width, height))
    ending = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(made.type)
    if ending is None or not made.data:
        raise HeaderError(f"{chosen.label} did not give back a picture Articles can keep, so "
                          "nothing was made.")
    ref = f"uploads/{secrets.token_hex(16)}.{ending}"
    folder = ctx.app.data_dir
    if folder is None:
        raise HeaderError("Articles has no storage of its own here, so nothing was made.")
    target = folder / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(made.data)
    _ready(ctx)
    image_id = ctx.store.insert("header_image", {
        "piece_id": piece_id, "site_id": piece["site_id"], "description": description,
        "style": style, "ref": ref, "type": made.type, "provider": chosen.id,
        "sendable": int(bool(chosen.sendable)), "width": width, "height": height,
        "created": ctx.now()})
    ctx.store.add_turn(piece_id, "header", f"{chosen.label} made a header image: "
                       f"{description}"[:600], model=str(getattr(reply, "model", "") or ""),
                       local=getattr(reply, "local", None))
    ctx.tape("header.made", {"piece": piece_id, "image": image_id, "provider": chosen.id},
             subject=f"piece:{piece_id}")
    ctx.emit()
    return view(ctx, piece_id) or {}


def view(ctx: Ctx, piece_id: Any) -> dict[str, Any] | None:
    """What the article's page shows: the latest picture's ref (for ``hq.files.thumbnail``,
    to show and to download), what it shows, and whether it goes with the draft."""
    row = latest(ctx, piece_id)
    if row is None:
        return None
    return {"ref": row["ref"], "type": row["type"], "description": row["description"],
            "provider": row["provider"], "width": row["width"], "height": row["height"],
            "created": row["created"],
            "sent_with_draft": bool(row["sendable"]),
            "name": f"article-{piece_id}-header.{row['ref'].rsplit('.', 1)[-1]}"}


def for_publication(ctx: Ctx, piece_id: int) -> tuple[str, str] | None:
    """Rule ``only_a_real_picture_goes_to_the_site``: the header image to send with the
    draft, ``(ref, type)``, from a provider whose pictures may go on; never a Practice
    placeholder, and never when header images are switched off."""
    if not bool(ctx.cfg().header_images):
        return None
    row = latest(ctx, piece_id)
    if row is None or not row["sendable"]:
        return None
    return row["ref"], row["type"]
