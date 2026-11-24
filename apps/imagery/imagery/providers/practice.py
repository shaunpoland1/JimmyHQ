"""Practice: draws a labelled placeholder picture on this computer. The default.

It sends nothing anywhere and needs no key, so every part of Imagery can be tried. The
picture says it is a practice picture, in the shape asked for, with the start of the
description on it, so it is never mistaken for a real one. The same description and shape
always give the same colours.
"""

from __future__ import annotations

import hashlib
from typing import Any

from imagery import png
from imagery.providers import Made, Request
from imagery.rules import parse_aspect

LONG_SIDE = 512
SCALE = 3  # pixels on the picture for one dot of the letters
# A 5 by 7 letter shape: seven rows of five dots, each row as a number.
_GLYPHS = {
    "A": "0E 11 11 1F 11 11 11", "B": "1E 11 11 1E 11 11 1E", "C": "0E 11 10 10 10 11 0E",
    "D": "1E 11 11 11 11 11 1E", "E": "1F 10 10 1E 10 10 1F", "F": "1F 10 10 1E 10 10 10",
    "G": "0E 11 10 17 11 11 0F", "H": "11 11 11 1F 11 11 11", "I": "0E 04 04 04 04 04 0E",
    "J": "07 02 02 02 02 12 0C", "K": "11 12 14 18 14 12 11", "L": "10 10 10 10 10 10 1F",
    "M": "11 1B 15 15 11 11 11", "N": "11 11 19 15 13 11 11", "O": "0E 11 11 11 11 11 0E",
    "P": "1E 11 11 1E 10 10 10", "Q": "0E 11 11 11 15 12 0D", "R": "1E 11 11 1E 14 12 11",
    "S": "0F 10 10 0E 01 01 1E", "T": "1F 04 04 04 04 04 04", "U": "11 11 11 11 11 11 0E",
    "V": "11 11 11 11 11 0A 04", "W": "11 11 11 15 15 15 0A", "X": "11 11 0A 04 0A 11 11",
    "Y": "11 11 11 0A 04 04 04", "Z": "1F 01 02 04 08 10 1F",
    "0": "0E 11 13 15 19 11 0E", "1": "04 0C 04 04 04 04 0E", "2": "0E 11 01 02 04 08 1F",
    "3": "1F 02 04 02 01 11 0E", "4": "02 06 0A 12 1F 02 02", "5": "1F 10 1E 01 01 11 0E",
    "6": "06 08 10 1E 11 11 0E", "7": "1F 01 02 04 08 08 08", "8": "0E 11 11 0E 11 11 0E",
    "9": "0E 11 11 0F 01 02 0C",
    ".": "00 00 00 00 00 0C 0C", ",": "00 00 00 00 0C 04 08", "-": "00 00 00 1F 00 00 00",
    "!": "04 04 04 04 04 00 04", "?": "0E 11 01 02 04 00 04", ":": "00 0C 0C 00 0C 0C 00",
    "'": "0C 04 08 00 00 00 00", "/": "01 01 02 04 08 10 10", "(": "02 04 08 08 08 04 02",
    ")": "08 04 02 02 02 04 08", " ": "00 00 00 00 00 00 00",
}
_ROWS = {ch: [int(v, 16) for v in rows.split()] for ch, rows in _GLYPHS.items()}
CELL_W, CELL_H = 6 * SCALE, 9 * SCALE  # a letter and the space after and below it


def dimensions(aspect: str) -> tuple[int, int]:
    """Width and height for a shape like ``16:9``: the longer side is ``LONG_SIDE``."""
    parsed = parse_aspect(aspect) or (1, 1)
    a, b = parsed
    if a >= b:
        return LONG_SIDE, max(64, round(LONG_SIDE * b / a))
    return max(64, round(LONG_SIDE * a / b)), LONG_SIDE


def wrap(text: str, per_line: int, lines: int) -> list[str]:
    """The text in capitals cut into at most ``lines`` lines of ``per_line`` letters,
    breaking at spaces where it can, with a dot on the last when more was left out."""
    words = text.upper().split()
    out: list[str] = []
    line = ""
    for word in words:
        while len(word) > per_line:
            if line:
                out.append(line)
                line = ""
            out.append(word[:per_line])
            word = word[per_line:]
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= per_line:
            line += " " + word
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][:max(0, per_line - 1)] + "."
    return out


def draw(prompt: str, aspect: str) -> bytes:
    """The practice picture as PNG bytes."""
    width, height = dimensions(aspect)
    seed = hashlib.sha256(f"{prompt}|{aspect}".encode()).digest()
    back = bytes(60 + seed[i] % 120 for i in range(3))
    stripe = bytes(min(255, c + 40) for c in back)
    ink = b"\xff\xff\xff"
    pixels = [[back] * width for _ in range(height)]
    for y in range(height):
        if (y // 24) % 2:
            pixels[y] = [stripe] * width
    per_line = max(4, (width - 2 * CELL_W) // CELL_W)
    room = max(2, (height - 4 * CELL_H) // CELL_H)
    text = ["PRACTICE PICTURE", *wrap(prompt, per_line, room - 1)]
    top = CELL_H
    for n, line in enumerate(text):
        for k, char in enumerate(line):
            rows = _ROWS.get(char, _ROWS["?"])
            for gy, bits in enumerate(rows):
                for gx in range(5):
                    if bits >> (4 - gx) & 1:
                        for dy in range(SCALE):
                            for dx in range(SCALE):
                                x = CELL_W + k * CELL_W + gx * SCALE + dx
                                y = top + n * CELL_H + gy * SCALE + dy
                                if 0 <= x < width and 0 <= y < height:
                                    pixels[y][x] = ink
    return png.write_png(width, height, [b"".join(row) for row in pixels])


class Practice:
    id = "practice"
    label = "Practice (nothing is sent)"
    outside = False
    supports_reference = False
    model_name = "practice"

    def hosts(self, cfg: Any) -> tuple[str, ...]:
        return ()

    def problem(self, cfg: Any) -> str | None:
        return None

    def aspects(self, cfg: Any) -> list[str]:
        from imagery.rules import clean_aspects  # noqa: PLC0415

        return clean_aspects(cfg.aspects)

    def model(self, cfg: Any) -> str:
        return self.model_name

    def make(self, request: Request, cfg: Any) -> Made:
        return Made(draw(request.prompt, request.aspect), "image/png",
                    "Practice: nothing was sent anywhere.")
