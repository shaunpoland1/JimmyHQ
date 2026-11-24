"""Small pictures made at run time for Posts' picture tests: PNG and JPEG files built from
a grid of shades, with no picture library. (Named so it cannot be mistaken for the
repository's own test helpers when both folders are collected in one run.)

The JPEG writer keeps only each 8 by 8 block's average (its DC value), which is all
Posts reads; every other coefficient is zero. That is a valid JPEG, baseline or
progressive.
"""

from __future__ import annotations

import struct
import zlib

Shades = list[list[int]]  # rows of 0..255


def pattern(width: int, height: int, seed: int = 1) -> Shades:
    """A fixed, uneven pattern of shades: the same seed always gives the same one."""
    out, state = [], seed
    for _ in range(height):
        row = []
        for _ in range(width):
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            row.append(state % 256)
        out.append(row)
    return out


def nudged(shades: Shades, by: int = 3) -> Shades:
    """The same picture, a little lighter in places, as a second shot of one scene."""
    return [[min(255, v + (by if (x + y) % 5 == 0 else 0)) for x, v in enumerate(row)]
            for y, row in enumerate(shades)]


def blown_up(shades: Shades, factor: int = 8) -> Shades:
    return [[v for v in row for _ in range(factor)] for row in shades for _ in range(factor)]


# --- PNG ------------------------------------------------------------------------------------


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def png(shades: Shades, *, colour: bool = True, interlaced: bool = False) -> bytes:
    """An 8-bit PNG (RGB, or grey), each row with the next of the five filters in turn."""
    height, width = len(shades), len(shades[0])
    step = 3 if colour else 1
    raw, previous = bytearray(), bytearray(width * step)
    for y, row in enumerate(shades):
        line = bytearray(v for v in row for _ in range(step))
        kind = y % 5
        out = bytearray(len(line))
        for i, value in enumerate(line):
            left = line[i - step] if i >= step else 0
            up = previous[i]
            corner = previous[i - step] if i >= step else 0
            guess = (0, left, up, (left + up) >> 1, _paeth(left, up, corner))[kind]
            out[i] = (value - guess) & 255
        raw += bytes([kind]) + out
        previous = line
    header = struct.pack(">IIBBBBB", width, height, 8, 2 if colour else 0, 0, 0,
                         1 if interlaced else 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw))) + _chunk(b"IEND", b""))


# --- JPEG -----------------------------------------------------------------------------------


class _Bits:
    def __init__(self) -> None:
        self.bits: list[int] = []
        self.out = bytearray()

    def put(self, value: int, count: int) -> None:
        for shift in range(count - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def flush(self) -> None:
        while len(self.bits) % 8:
            self.bits.append(1)
        for i in range(0, len(self.bits), 8):
            byte = int("".join(map(str, self.bits[i:i + 8])), 2)
            self.out.append(byte)
            if byte == 0xFF:
                self.out.append(0)
        self.bits = []


def _segment(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def jpeg(blocks: Shades, *, components: int = 1, progressive: bool = False,
         restart: int = 0) -> bytes:
    """A JPEG whose 8 by 8 blocks have the given shades (one entry per block)."""
    down, across = len(blocks), len(blocks[0])
    frame = struct.pack(">BHHB", 8, down * 8, across * 8, components) + b"".join(
        bytes([c + 1, 0x11, 0]) for c in range(components))
    dc = bytes([0, 0, 0, 12] + [0] * 12) + bytes(range(12))  # twelve codes of four bits
    ac = bytes([1] + [0] * 15) + b"\x00"  # one code, "0": end of block
    tables = b"\x00" + dc + (b"" if progressive else b"\x10" + ac)
    scan = bytes([components]) + b"".join(bytes([c + 1, 0x00]) for c in range(components)) \
        + (bytes([0, 0, 0]) if progressive else bytes([0, 63, 0]))
    writer = _Bits()
    pred = [0] * components
    for n in range(down * across):
        if restart and n and n % restart == 0:
            writer.flush()
            writer.out += bytes([0xFF, 0xD0 + (n // restart - 1) % 8])
            pred = [0] * components
        y, x = divmod(n, across)
        for c in range(components):
            value = (blocks[y][x] - 128) * 8 if c == 0 else 0
            diff = value - pred[c]
            pred[c] = value
            size = abs(diff).bit_length()
            writer.put(size, 4)
            if size:
                writer.put(diff if diff >= 0 else diff + (1 << size) - 1, size)
            if not progressive:
                writer.put(0, 1)
    writer.flush()
    return (b"\xff\xd8" + _segment(0xDB, b"\x00" + bytes([1] * 64))
            + _segment(0xC2 if progressive else 0xC0, frame)
            + (_segment(0xDD, struct.pack(">H", restart)) if restart else b"")
            + _segment(0xC4, tables) + _segment(0xDA, scan) + bytes(writer.out)
            + b"\xff\xd9")


GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00" \
      b"\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88" \
       b"\x88\xfe\x07\x00"
