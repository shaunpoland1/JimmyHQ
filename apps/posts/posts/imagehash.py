"""A difference hash ("dHash") of a picture, worked out with nothing but the standard
library, so Posts needs no picture library to say "this looks like last week's".

A dHash shrinks the picture to 9 by 8 shades of grey and keeps, for each row, whether
each shade is darker than the next one: 64 bits. Two pictures that look alike have
hashes that differ in few bits (``distance``), whatever their size or compression.

What can be read:
- PNG, every colour type and bit depth, not interlaced (an interlaced PNG gives None);
- JPEG, baseline and progressive: only the average brightness of each 8 by 8 block
  (the "DC" value) is decoded, which is all a 9 by 8 hash needs, and needs no maths
  beyond adding;
- anything else (WebP among them) gives None, and the caller says so in words.

Nothing here raises on a damaged file: ``dhash`` returns None.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

HASH_W, HASH_H = 9, 8  # 9 columns compared in pairs give 8 bits a row, 8 rows: 64 bits
SAMPLES = 12  # points read along each side of one cell of the 9 by 8 grid

Grid = tuple[int, int, list[list[float]]]  # width, height, rows of brightness


# --- the hash -------------------------------------------------------------------------------


def shrink(grid: Grid) -> list[list[float]]:
    """The grid as 9 by 8 cells, each the mean of points spread evenly over it."""
    width, height, rows = grid
    out = []
    for cy in range(HASH_H):
        y0, y1 = cy * height / HASH_H, (cy + 1) * height / HASH_H
        line = []
        for cx in range(HASH_W):
            x0, x1 = cx * width / HASH_W, (cx + 1) * width / HASH_W
            total, count = 0.0, 0
            for sy in range(SAMPLES):
                y = min(height - 1, int(y0 + (y1 - y0) * (sy + 0.5) / SAMPLES))
                row = rows[y]
                for sx in range(SAMPLES):
                    total += row[min(width - 1, int(x0 + (x1 - x0) * (sx + 0.5) / SAMPLES))]
                    count += 1
            line.append(total / count)
        out.append(line)
    return out


def hash_grid(grid: Grid) -> str:
    """The 64 bits as 16 hexadecimal digits."""
    cells = shrink(grid)
    bits = 0
    for line in cells:
        for left, right in zip(line, line[1:], strict=False):
            bits = (bits << 1) | (1 if left < right else 0)
    return f"{bits:016x}"


def distance(a: str, b: str) -> int:
    """How many of the 64 bits differ."""
    return (int(a, 16) ^ int(b, 16)).bit_count()


def dhash(path: Path) -> str | None:
    """The picture's dHash, or None when it cannot be read here."""
    try:
        data = Path(path).read_bytes()
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            grid = png_grey(data)
        elif data.startswith(b"\xff\xd8"):
            grid = jpeg_dc(data)
        else:
            return None
    except (OSError, ValueError, IndexError, KeyError, struct.error, zlib.error):
        return None
    if grid is None or grid[0] < 1 or grid[1] < 1:
        return None
    return hash_grid(grid)


# --- PNG ------------------------------------------------------------------------------------

_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def png_grey(data: bytes) -> Grid | None:
    pos, idat, palette = 8, bytearray(), b""
    width = height = depth = colour = 0
    interlaced = False
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            interlaced = interlace != 0
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if interlaced or colour not in _CHANNELS or not width or not height:
        return None
    channels = _CHANNELS[colour]
    bits = channels * depth
    step = max(1, bits // 8)  # bytes to the same byte of the pixel before, for the filters
    stride = (width * bits + 7) // 8
    raw = zlib.decompress(bytes(idat))
    previous = bytearray(stride)
    rows: list[list[float]] = []
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        line = bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(step, stride):
                line[i] = (line[i] + line[i - step]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                corner = previous[i - step] if i >= step else 0
                line[i] = (line[i] + _paeth(left, previous[i], corner)) & 255
        rows.append(_png_row(line, width, depth, colour, palette))
        previous = line
    return width, height, rows


def _png_row(line: bytearray, width: int, depth: int, colour: int,
             palette: bytes) -> list[float]:
    channels = _CHANNELS[colour]
    if depth < 8:
        per_byte = 8 // depth
        mask = (1 << depth) - 1
        values = [(line[i // per_byte] >> (8 - depth * (i % per_byte + 1))) & mask
                  for i in range(width)]
        if colour == 3:
            return [_grey_rgb(palette, v) for v in values]
        return [v * 255 / mask for v in values]
    size = depth // 8
    if size == 2:
        samples = [line[i] for i in range(0, len(line), 2)]  # the high byte is enough
    else:
        samples = list(line)
    if colour == 3:
        return [_grey_rgb(palette, v) for v in samples[:width]]
    if channels <= 2:
        return [float(samples[i * channels]) for i in range(width)]
    return [0.299 * samples[i * channels] + 0.587 * samples[i * channels + 1]
            + 0.114 * samples[i * channels + 2] for i in range(width)]


def _grey_rgb(palette: bytes, index: int) -> float:
    r, g, b = palette[index * 3:index * 3 + 3] or b"\0\0\0"
    return 0.299 * r + 0.587 * g + 0.114 * b


# --- JPEG: the brightness of each block ------------------------------------------------------


def _table(counts: bytes, symbols: bytes) -> list[int]:
    """A lookup by the next 16 bits: (code length << 8) | symbol, or 0 for no code."""
    lookup = [0] * 65536
    code, k = 0, 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            first = code << (16 - length)
            entry = (length << 8) | symbols[k]
            for i in range(first, first + (1 << (16 - length))):
                lookup[i] = entry
            code += 1
            k += 1
        code <<= 1
    return lookup


def _segments(data: bytes, start: int) -> tuple[list[bytes], int]:
    """The entropy-coded data after a scan header, split at restart markers, with the
    stuffed zero bytes taken out; and where the next marker starts."""
    out, current, i = [], bytearray(), start
    while i < len(data):
        byte = data[i]
        if byte != 0xFF:
            current.append(byte)
            i += 1
            continue
        following = data[i + 1] if i + 1 < len(data) else 0xD9
        if following == 0x00:
            current.append(0xFF)
            i += 2
        elif 0xD0 <= following <= 0xD7:
            out.append(bytes(current))
            current = bytearray()
            i += 2
        elif following == 0xFF:
            i += 1
        else:
            break
    out.append(bytes(current))
    return out, i


class _Bits:
    __slots__ = ("acc", "data", "nbits", "pos")

    def __init__(self, data: bytes):
        self.data, self.pos, self.acc, self.nbits = data, 0, 0, 0

    def _fill(self) -> None:
        while self.nbits <= 24:
            byte = self.data[self.pos] if self.pos < len(self.data) else 0xFF
            self.pos += 1
            self.acc = ((self.acc << 8) | byte) & 0xFFFFFFFFFF
            self.nbits += 8

    def symbol(self, lookup: list[int]) -> int:
        if self.nbits < 16:
            self._fill()
        entry = lookup[(self.acc >> (self.nbits - 16)) & 0xFFFF]
        if not entry:
            raise ValueError("bad Huffman code")
        self.nbits -= entry >> 8
        return entry & 0xFF

    def bits(self, count: int) -> int:
        if not count:
            return 0
        if self.nbits < count:
            self._fill()
        self.nbits -= count
        return (self.acc >> self.nbits) & ((1 << count) - 1)

    def signed(self, count: int) -> int:
        value = self.bits(count)
        return value if count == 0 or value >= 1 << (count - 1) else value - (1 << count) + 1


def jpeg_dc(data: bytes) -> Grid | None:
    """The brightness of each 8 by 8 block of the picture's first component (its grey,
    or its luma), from the DC values of a baseline or progressive JPEG."""
    pos = 2
    dc_tables: dict[int, list[int]] = {}
    ac_tables: dict[int, list[int]] = {}
    frame: dict[str, object] | None = None
    restart = 0
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xD9:
            break
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        body = data[pos + 4:pos + 2 + length]
        pos += 2 + length
        if marker in (0xC0, 0xC1, 0xC2):
            height, width, count = struct.unpack(">HHB", body[1:6])
            comps = [(body[6 + 3 * i], body[7 + 3 * i] >> 4, body[7 + 3 * i] & 15)
                     for i in range(count)]
            frame = {"width": width, "height": height, "comps": comps,
                     "progressive": marker == 0xC2}
        elif marker in (0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return None  # lossless or arithmetic coding: not read here
        elif marker == 0xC4:
            at = 0
            while at < len(body):
                kind, ident = body[at] >> 4, body[at] & 15
                counts = body[at + 1:at + 17]
                total = sum(counts)
                table = _table(counts, body[at + 17:at + 17 + total])
                (ac_tables if kind else dc_tables)[ident] = table
                at += 17 + total
        elif marker == 0xDD:
            restart = struct.unpack(">H", body[:2])[0]
        elif marker == 0xDA:
            if frame is None:
                return None
            grid = _scan(data, pos, body, frame, dc_tables, ac_tables, restart)
            if grid is not None:
                return grid
            pos = _segments(data, pos)[1]  # a scan without the first component's DC
    return None


def _ceil(a: int, b: int) -> int:
    return -(-a // b)


def _scan(data: bytes, pos: int, header: bytes, frame: dict, dc_tables: dict,
          ac_tables: dict, restart: int) -> Grid | None:
    comps = frame["comps"]
    hmax = max(c[1] for c in comps)
    vmax = max(c[2] for c in comps)
    count = header[0]
    in_scan = [(header[1 + 2 * i], header[2 + 2 * i] >> 4, header[2 + 2 * i] & 15)
               for i in range(count)]
    ss = header[1 + 2 * count]
    al = header[3 + 2 * count] & 15
    ah = header[3 + 2 * count] >> 4
    first_id = comps[0][0]
    if frame["progressive"] and (ss != 0 or ah != 0):
        return None  # only the first DC scan is needed
    if first_id not in [c[0] for c in in_scan]:
        return None
    width, height = frame["width"], frame["height"]
    _, h1, v1 = comps[0]
    bw = _ceil(_ceil(width * h1, hmax), 8)  # blocks across the first component
    bh = _ceil(_ceil(height * v1, vmax), 8)
    by_id = {c[0]: c for c in comps}
    if count == 1:
        mcu_x, mcu_y = bw, bh
        layout = [(first_id, 1, 1)]
    else:
        mcu_x = _ceil(width, 8 * hmax)
        mcu_y = _ceil(height, 8 * vmax)
        layout = [(cid, by_id[cid][1], by_id[cid][2]) for cid, _, _ in in_scan]
    tables = {cid: (dc_tables.get(td), ac_tables.get(ta)) for cid, td, ta in in_scan}
    across, down = (mcu_x * h1, mcu_y * v1) if count > 1 else (bw, bh)
    rows = [[0.0] * across for _ in range(down)]
    skip_ac = not frame["progressive"]
    segments, _ = _segments(data, pos)
    per_segment = restart or mcu_x * mcu_y
    mcu = 0
    for segment in segments:
        bits = _Bits(segment)
        pred = dict.fromkeys(tables, 0)
        for _ in range(per_segment):
            if mcu >= mcu_x * mcu_y:
                break
            mx, my = mcu % mcu_x, mcu // mcu_x
            for cid, h, v in layout:
                dc, ac = tables[cid]
                if dc is None or (skip_ac and ac is None):
                    raise ValueError("missing Huffman table")
                for by in range(v):
                    for bx in range(h):
                        size = bits.symbol(dc)
                        pred[cid] += bits.signed(size)
                        if skip_ac:
                            k = 1
                            while k < 64:
                                rs = bits.symbol(ac)
                                run, size = rs >> 4, rs & 15
                                if size == 0:
                                    if run != 15:
                                        break
                                    k += 16
                                    continue
                                k += run
                                bits.bits(size)
                                k += 1
                        if cid == first_id:
                            rows[my * v + by][mx * h + bx] = float(pred[cid] << al)
            mcu += 1
    return bw, bh, [row[:bw] for row in rows[:bh]]
