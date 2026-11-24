"""What crosses the door, in each direction, is shaped here.

Coming in: arguments are checked against the tool's schema before the tool sees them,
and text is cleaned of hidden and control characters. Going out: anything that looks
like a secret is taken out, addresses are taken out unless the client is allowed them,
and long text and long lists are cut, saying so.

Every function is a named rule with its own test.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from jimmyhq import scrub

ADDRESS_REMOVED = "[address removed]"
CUT = " [... cut: {more} more characters]"

_URL = re.compile(r"(?i)\b(?:https?|ftp|wss?|ssh|file)://[^\s<>\"')\]]+")
_MAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\d.])")
_WWW = re.compile(r"(?i)\bwww\.[a-z0-9-]+(?:\.[a-z0-9-]+)+[^\s<>\"')\]]*")


class BadArguments(ValueError):
    """The arguments do not fit the tool's schema. The sentence never repeats a value."""


def clean_text(value: str) -> str:
    """Rule ``door_text_cleaned``: text from outside with its hidden and control
    characters taken out (new lines and tabs stay), in one normal form. What cannot be
    seen cannot be left for an agent or the owner to read without knowing."""
    value = unicodedata.normalize("NFC", value)
    kept = []
    for ch in value:
        if ch in "\n\t":
            kept.append(ch)
        elif ch == "\r":
            continue
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        else:
            kept.append(ch)
    return "".join(kept).strip()


def one_line(value: str) -> str:
    """Cleaned text on a single line."""
    return " ".join(clean_text(value).split())


def without_addresses(value: str) -> str:
    """Rule ``door_addresses_removed``: web addresses, mail addresses and numeric network
    addresses are replaced with a marker. A client sees them only if the owner said so."""
    for shape in (_URL, _MAIL, _WWW, _IPV4):
        value = shape.sub(ADDRESS_REMOVED, value)
    return value


def cut(value: str, limit: int) -> str:
    """Rule ``door_text_cut``: text over the limit is cut and says how much is missing.
    Never cut silently."""
    if len(value) <= limit:
        return value
    return value[:limit] + CUT.format(more=len(value) - limit)


def shown(value: Any, *, limit: int, addresses: bool) -> str:
    """Rule ``door_text_shaped``: one piece of text as a client may see it: no hidden or
    control characters (``clean_text``), no secrets, no addresses unless allowed, cut to
    the limit."""
    text = scrub.text(clean_text(str(value or "")))
    if not addresses:
        text = without_addresses(text)
    return cut(text, limit)


def capped(items: list[Any], cap: int) -> tuple[list[Any], dict[str, Any]]:
    """Rule ``door_list_capped``: the first ``cap`` items, and a note of how many there
    were: ``{"total", "shown", "cut"}``."""
    kept = items[:cap]
    return kept, {"total": len(items), "shown": len(kept), "cut": len(items) > len(kept)}


# --- arguments -------------------------------------------------------------------------


def _check(schema: dict[str, Any], value: Any, name: str) -> None:
    kind = schema.get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise BadArguments(f"{name} must be text.")
        if len(value) > schema.get("maxLength", 10_000):
            raise BadArguments(f"{name} is {len(value)} characters and the most is "
                               f"{schema.get('maxLength', 10_000)}.")
        if len(value.strip()) < schema.get("minLength", 0):
            raise BadArguments(f"{name} cannot be empty.")
        if "enum" in schema and value not in schema["enum"]:
            raise BadArguments(f"{name} must be one of: {', '.join(schema['enum'])}.")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise BadArguments(f"{name} is not in the form this tool takes. "
                               f"{schema.get('description', '')}".strip())
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise BadArguments(f"{name} must be a whole number.")
        low, high = schema.get("minimum"), schema.get("maximum")
        if (low is not None and value < low) or (high is not None and value > high):
            raise BadArguments(f"{name} must be from {low} to {high}.")
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise BadArguments(f"{name} must be a number.")
        low, high = schema.get("minimum"), schema.get("maximum")
        if (low is not None and value < low) or (high is not None and value > high):
            raise BadArguments(f"{name} must be from {low} to {high}.")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise BadArguments(f"{name} must be true or false.")
    else:
        # A schema this does not know how to check lets nothing through.
        raise BadArguments(f"{name} cannot be checked by the door, so it is not taken.")


def check_arguments(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    """Rule ``door_arguments_checked``: the arguments as the tool will get them, or
    ``BadArguments``. Only what the schema names is accepted, every required one must be
    there, and each must be of its type and within its size. The tools' schemas, and the
    ones made for app tools, use only flat objects of text, numbers and true/false, which
    is all this checks: any other type is refused."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise BadArguments("The arguments must be an object.")
    props = schema.get("properties", {})
    unknown = sorted(str(k)[:40] for k in args if k not in props)
    if unknown:
        known = ", ".join(props) or "none"
        raise BadArguments(f"This tool takes no argument called {unknown[0]!r}. It takes: "
                           f"{known}.")
    for name in schema.get("required", []):
        if args.get(name) is None:
            raise BadArguments(f"{name} is required.")
    out = {}
    for name, value in args.items():
        if value is None:
            continue
        _check(props[name], value, name)
        out[name] = value
    return out
