"""What a sender or a model said, made safe to keep: text of a sensible length with no
control characters. Nothing here puts the text it checks into a message."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f  ]")
_BLANKS = re.compile(r"\n{3,}")


def block(value: Any, most: int) -> str:
    """Several lines of text, cut to ``most`` characters; "" when it is not text."""
    if not isinstance(value, str):
        return ""
    out = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    out = _BLANKS.sub("\n\n", _CONTROL.sub("", out)).strip()
    return out[:most].rstrip()


def line(value: Any, most: int) -> str:
    """One line of text: its own line breaks become spaces. Cut to ``most``."""
    if not isinstance(value, str):
        return ""
    out = " ".join(_CONTROL.sub("", unicodedata.normalize("NFC", value)).split())
    return out[:most].rstrip()


def key(value: str) -> str:
    """A line as it is compared with others: no case, no extra spaces, no end marks."""
    return " ".join(value.casefold().split()).strip(" .,;:!?\"'")
