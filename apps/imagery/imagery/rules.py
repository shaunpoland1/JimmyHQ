"""Small rules, each a named function with its own test: what the owner typed, cleaned.

Every refusal is a sentence in the second person that says nothing was made and what to do.
"""

from __future__ import annotations

import re
from typing import Any


class Refused(ValueError):
    """The sentence the owner reads: what was refused, that nothing changed, what to do."""


_ASPECT = re.compile(r"^([1-9]\d{0,1}):([1-9]\d{0,1})\Z")
_SIZE = re.compile(r"^([1-9]\d{2,4})x([1-9]\d{2,4})\Z")


def parse_aspect(text: Any) -> tuple[int, int] | None:
    """``"16:9"`` as (16, 9), else None."""
    found = _ASPECT.match(str(text or "").strip().replace(" ", ""))
    return (int(found.group(1)), int(found.group(2))) if found else None


def clean_aspects(lines: Any) -> list[str]:
    """The owner's list of shapes, each written like ``16:9``, no shape twice; a line that
    is not a shape is left out."""
    out: list[str] = []
    for line in lines if isinstance(lines, list) else []:
        parsed = parse_aspect(line)
        text = f"{parsed[0]}:{parsed[1]}" if parsed else ""
        if text and text not in out:
            out.append(text)
    return out


def parse_sizes(lines: Any) -> dict[str, str]:
    """``["3:2=1536x1024"]`` as ``{"3:2": "1536x1024"}``; a line that does not read so is
    left out."""
    out: dict[str, str] = {}
    for line in lines if isinstance(lines, list) else []:
        left, _, right = str(line).partition("=")
        parsed = parse_aspect(left)
        size = _SIZE.match(right.strip().lower().replace(" ", ""))
        if parsed and size:
            out.setdefault(f"{parsed[0]}:{parsed[1]}", size.group(0))
    return out


def parse_presets(lines: Any) -> list[dict[str, str]]:
    """``["Photo: a realistic photograph"]`` as ``[{"name": "Photo", "ending": ...}]``. A
    line with no colon, no name or no words after it is left out; a name is kept once."""
    out: list[dict[str, str]] = []
    for line in lines if isinstance(lines, list) else []:
        name, colon, ending = str(line).partition(":")
        name, ending = " ".join(name.split()), " ".join(ending.split())
        if colon and name and ending and all(p["name"].lower() != name.lower() for p in out):
            out.append({"name": name[:40], "ending": ending[:400]})
    return out


def clean_prompt(text: Any, most: int) -> str:
    """The description with its spaces tidied, or a ``Refused`` saying what is wrong."""
    words = " ".join(str(text or "").replace("\r\n", "\n").split())
    if not words:
        raise Refused("Say what the picture should show. Nothing was made.")
    if len(words) > most:
        raise Refused(f"That description is {len(words)} characters long, and Imagery takes "
                      f"{most} at most. Nothing was made. Shorten it.")
    return words


def join_style(prompt: str, ending: str) -> str:
    """The description with a style ending added: one sentence, then the other."""
    if not ending:
        return prompt
    joiner = " " if prompt.rstrip().endswith((".", "!", "?")) else ". "
    return f"{prompt.rstrip()}{joiner}{ending}"


def count_of(value: Any, most: int) -> int:
    """How many pictures: a whole number from 1 to ``most``."""
    try:
        if isinstance(value, bool) or (isinstance(value, float) and value != int(value)):
            raise ValueError
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise Refused("Choose how many pictures to make, as a whole number. Nothing was "
                      "made.") from None
    if not 1 <= number <= most:
        raise Refused(f"You can make 1 to {most} pictures at a time, and you asked for "
                      f"{number}. Nothing was made. Change \"Most pictures in one go\" in "
                      "Imagery's settings to allow more, up to 4.")
    return number


def whole(value: Any, name: str) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise Refused(f"\"{name}\" must be a whole number. Nothing was changed.") from None
