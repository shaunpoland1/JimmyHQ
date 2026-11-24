"""What may be written in a provider's options, and how a secret is named there.

A provider's record is an event on the Tape, so no key, token or password may be in it.
Three kinds of thing are written in options, and each is checked for what it is:

- **Names of saved values** (environment variables, headers). The values themselves are
  secrets of the provider and never in the record.
- **A program, its folder and its arguments.** These are stored exactly as given: a long
  path is a long path. Only an argument that looks like a credential is refused, and the
  refusal says what to do: save it as a secret of the provider and write
  ``{secret:NAME}`` where it belongs. The name is put back only when the program is
  started (``with_secrets``), so the value is never in the record.
- **Everything else.** Checked with the shared scrubber, as before.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any

from jimmyhq import scrub
from jimmyhq.hands.provider import ProviderType
from jimmyhq.models.validate import Invalid

SECRET_REF = re.compile(r"\{secret:([a-z][a-z0-9_-]{0,39})\}")
MAX_ARGS = 100
MAX_ARG_CHARS = 2000
TOKEN_CHARS = 24  # a run this long that mixes letters and digits
HEX_CHARS = 32  # the same when it is only hexadecimal
MIN_BITS = 3.0  # per character: below this it is a repeated pattern, not a token

_WORD = (r"(?:pass(?:word|wd|phrase|code)?|secret|token|api[_-]?key|apikey|key|auth|"
         r"authorization|credentials?|bearer)")
# "--token=abc", "key=abc", "API_KEY:abc": a secret's name, then its value.
_NAMED = re.compile(rf"(?i)^-{{0,2}}[a-z0-9_.-]*?{_WORD}s?[=:](.+)$", re.S)
# "--token" by itself: the value is the next argument.
_FLAG = re.compile(rf"(?i)^-{{1,2}}[a-z0-9_.-]*?{_WORD}s?$")
# "...?token=abc" in an address, and "scheme://user:password@host".
_IN_QUERY = re.compile(rf"(?i)[?&;][a-z0-9_.-]*?{_WORD}s?=([^&;#]+)")
_USERINFO = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s@]+)@")
_RUN = re.compile(rf"[A-Za-z0-9+=]{{{TOKEN_CHARS},}}")
# The same with the hyphens and underscores a web-safe token has: lower case, upper case
# and digits together, which a package or a file name rarely is.
_RUN_WEB = re.compile(r"[A-Za-z0-9_-]{32,}")
_HEX = re.compile(r"[0-9a-fA-F]+")

ADVICE = ("Save it as a secret of this provider instead, and write {secret:NAME} in its "
          "place, with the name you saved it under.")


def bits(value: str) -> float:
    """How varied a text's characters are, in bits per character."""
    if not value:
        return 0.0
    counts = {ch: value.count(ch) for ch in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def dense_token(run: str) -> bool:
    """A long run with no separators that mixes letters and digits and does not repeat
    itself: what a generated key looks like, and a word, a version or a name does not."""
    if not (any(c.isdigit() for c in run) and any(c.isalpha() for c in run)):
        return False
    if _HEX.fullmatch(run) and len(run) < HEX_CHARS:
        return False
    return bits(run) >= MIN_BITS


def _is_path(item: str) -> bool:
    return "/" in item or "\\" in item


def credential_in(item: str, after_flag: bool = False) -> bool:
    """Rule ``hand_argv_credentials_refused``: whether one argument of a program looks
    like a credential written out.

    It does when it has the well-known shape of a key; when it names a secret and gives
    its value (``--token=...``, ``key=...``, ``?api_key=...`` in an address, a password
    in an address); when it follows a flag that names one (``--token`` then the value);
    or when it is a long token of mixed letters and digits. A path is never judged by
    its length. A value given as ``{secret:NAME}`` is a reference, not a credential."""
    bare = SECRET_REF.sub("", item)
    if scrub.has_key_shape(bare):
        return True
    for pattern in (_NAMED, _IN_QUERY, _USERINFO):
        found = pattern.search(item)
        if found and not SECRET_REF.search(found.group(1)):
            return True
    if after_flag and not item.startswith("-") and not SECRET_REF.search(item):
        return True
    if _is_path(bare):
        return False
    if any(dense_token(run) for run in _RUN.findall(bare)):
        return True
    return any(any(c.isdigit() for c in run) and any(c.islower() for c in run)
               and any(c.isupper() for c in run) and bits(run) >= MIN_BITS
               for run in _RUN_WEB.findall(bare))


def check_argv(args: Any, what: str = "arguments") -> None:
    """A program's arguments as the owner wrote them. Raises ``Invalid`` naming the
    position of the one that looks like a credential, never its text."""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise Invalid(f"The {what} must be a list of texts, one for each argument.")
    if len(args) > MAX_ARGS or any(len(a) > MAX_ARG_CHARS for a in args):
        raise Invalid(f"The {what} can hold at most {MAX_ARGS} items of at most "
                      f"{MAX_ARG_CHARS} characters each.")
    after_flag = False
    for index, item in enumerate(args, start=1):
        if "{secret:" in SECRET_REF.sub("", item):
            raise Invalid(f"Argument {index} names a secret in a way that cannot be read. "
                          "Write it as {secret:NAME}, where NAME starts with a letter and "
                          "uses lower-case letters, digits, - and _.")
        if credential_in(item, after_flag):
            raise Invalid(f"Nothing was saved: argument {index} looks like a key, a token "
                          f"or a password. {ADVICE}")
        after_flag = bool(_FLAG.match(item))


def check_options(kind: ProviderType, options: dict[str, Any]) -> None:
    """Rule ``hand_options_hold_no_secret``: a provider's options, checked field by
    field for what each is. Paths are stored as given, arguments are checked one by one,
    and everything else must not look like a secret."""
    paths, lists = set(kind.fields_of("path")), set(kind.fields_of("argv"))
    rest = {}
    for name, value in options.items():
        if name in lists:
            check_argv(value or [], what=f"option \"{name}\"")
        elif name in paths:
            if value is not None and not isinstance(value, str):
                raise Invalid(f"The option \"{name}\" must be a path, as text.")
        else:
            rest[name] = value
    if scrub.looks_secret(json.dumps(rest)):
        raise Invalid("Something in the options looks like a key or a password. Save it "
                      "as a secret of the provider instead, and name the secret here.")


def secret_names(args: Any) -> list[str]:
    """The secrets a list of arguments refers to, in order, each once."""
    seen: list[str] = []
    for item in args if isinstance(args, list | tuple) else ():
        for name in SECRET_REF.findall(item) if isinstance(item, str) else ():
            if name not in seen:
                seen.append(name)
    return seen


def with_secrets(args: list[str] | tuple[str, ...], secret: Callable[[str], str | None]
                 ) -> tuple[tuple[str, ...], list[str]]:
    """Rule ``hand_secret_reference``: the arguments with every ``{secret:NAME}``
    replaced by the saved value, and the values that were put in. Only called when a
    program is about to be started. Raises ``KeyError(name)`` for a name with no saved
    value. A saved value is put in as it is: it is never read again for references."""
    used: list[str] = []

    def put(match: re.Match[str]) -> str:
        value = secret(match.group(1))
        if not value:
            raise KeyError(match.group(1))
        used.append(value)
        return value

    return tuple(SECRET_REF.sub(put, item) for item in args), used
