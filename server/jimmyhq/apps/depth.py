"""How deeply a piece of JSON text is nested, counted without parsing it.

Python's own JSON reader stops very deep nesting on some versions and not on
others, so an app's manifest or a bridge request is measured here first and
refused by our own rule, the same on every version."""

from __future__ import annotations

MAX_DEPTH = 64


def too_deep(text: str, limit: int = MAX_DEPTH) -> bool:
    """True if brackets and braces outside strings nest deeper than ``limit``.

    Text that is not valid JSON is measured the same way; the parser says what
    is wrong with it afterwards."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                return True
        elif char in "]}":
            depth -= 1
    return False
