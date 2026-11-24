"""Pictures as bytes: what kind a file is, how big, and a plain PNG writer.

Nothing here needs an image library. A kind is read from a file's first bytes, never from
a name or a type someone declared.
"""

from __future__ import annotations

import struct
import zlib

TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
ENDINGS = {mime: ending for ending, mime in TYPES.items()}


def kind_of(head: bytes) -> str | None:
    """``png``, ``jpg`` or ``webp`` for the first bytes of a file, else None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def size_of(data: bytes) -> tuple[int, int] | None:
    """Width and height in pixels, when the bytes say (PNG and JPEG; None otherwise)."""
    kind = kind_of(data[:16])
    try:
        if kind == "png" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if kind == "jpg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB,
                              0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[i + 5:i + 9])
                    return width, height
                i += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def _chunk(tag: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(tag + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)


def write_png(width: int, height: int, rows: list[bytes]) -> bytes:
    """An 8-bit RGB PNG from ``height`` rows of ``width * 3`` bytes each."""
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))
