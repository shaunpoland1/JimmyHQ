"""Taking out anything that looks like a secret: one scrubber for every module.

A key or a password that passed through a file, a message or a provider's answer must
not live on in a log or reach a model. ``text`` takes the shapes of one out of a text and
leaves the rest whole.

The memory module has its own check, tuned for sentences about the owner: it treats any
long run of letters and digits as a token. That is right for a fact and wrong here, where
it would take out file paths, web addresses and card ids. So this keeps the shapes of a
key apart from the things a record must keep.
"""

from __future__ import annotations

import re

REMOVED = "[removed]"

_SHAPES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)",
               re.S),
    re.compile(r"\b(sk|pk|rk)[-_][A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,})"),
    re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(\.[A-Za-z0-9_-]+)?"),
    re.compile(r"\bjhqd?_[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}"),
]
# "password is hunter2", "api key: abc", "token=abc": the value goes, the words stay.
_SAID = re.compile(r"(?i)\b((?:pass(?:word|code|phrase)|api[ _-]?key|secret|access[ _-]?token|"
                   r"token|pin(?: code| number)?|cvv|cvc)\b(?:\s+\w+){0,2}\s*(?:is|was|:|=)\s*)"
                   r"(\S{3,})")
# A long run with no separators that mixes letters and digits: a token, not a word.
_RUN = re.compile(r"[A-Za-z0-9+=]{24,}")
# The same with hyphens and underscores, as web-safe tokens have. Lower case, upper case
# and digits together: a file name is rarely all three.
_RUN_WEB = re.compile(r"[A-Za-z0-9_-]{32,}")
_CARD_ID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")
_HEX = re.compile(r"[0-9a-f]{12,16}")
_DIGITS = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(digits)):
        n = int(ch)
        if index % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _run(m: re.Match[str]) -> str:
    token = m.group(0)
    if _CARD_ID.fullmatch(token) or _HEX.fullmatch(token):
        return token  # a Bench card id, or one of our own fingerprints
    mixed = any(c.isdigit() for c in token) and any(c.isalpha() for c in token)
    return REMOVED if mixed else token


def _run_web(m: re.Match[str]) -> str:
    token = m.group(0)
    kinds = (any(c.isdigit() for c in token), any(c.islower() for c in token),
             any(c.isupper() for c in token))
    return REMOVED if all(kinds) else token


def _card_number(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    return REMOVED if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)


def text(value: str) -> str:
    """Text with anything that looks like a secret taken out."""
    for shape in _SHAPES:
        value = shape.sub(REMOVED, value)
    value = _SAID.sub(lambda m: m.group(1) + REMOVED, value)
    value = _RUN.sub(_run, value)
    value = _RUN_WEB.sub(_run_web, value)
    return _DIGITS.sub(_card_number, value)


def looks_secret(value: str) -> bool:
    return text(value) != value


def has_key_shape(value: str) -> bool:
    """True when a text holds one of the well-known shapes of a key: a private key
    block, a prefixed API key, a signed token, a bearer header. Narrower than
    ``looks_secret``: a long path or a plain long word never matches."""
    return any(shape.search(value) for shape in _SHAPES)
