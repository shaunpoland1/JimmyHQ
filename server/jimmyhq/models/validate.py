"""Checks on what the owner types, shared by the routes and the Python API.

Every message is one sentence that says what to change.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from jimmyhq.models.shapes import EFFORTS, PROVIDER_TYPES, Provider, Target

ID = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
CALLER = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".localhost", ".home.arpa", ".localdomain")
MAX_MODEL = 200
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+))*$")


class Invalid(ValueError):
    """Something offered is not acceptable. The message says what to change."""


def text(value: Any, what: str, *, max_len: int, empty_ok: bool = False) -> str:
    if not isinstance(value, str):
        raise Invalid(f"{what} must be text.")
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise Invalid(f"{what} contains characters that cannot be saved.") from None
    # Control characters (a NUL, a newline, a bell) have no place in a name,
    # and these values end up inside sentences, headers and log lines.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise Invalid(f"{what} contains characters that cannot be saved.")
    if not value and not empty_ok:
        raise Invalid(f"{what} cannot be empty.")
    if len(value) > max_len:
        raise Invalid(f"{what} cannot be longer than {max_len} characters.")
    return value


def slug(label: str) -> str:
    """A provider id made from its label, for when the owner does not choose one."""
    made = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if not made:
        return "provider"
    if not made[0].isalpha():
        made = f"p-{made}"
    return made[:40].rstrip("-")


def base_url(value: Any) -> str:
    value = text(value, "The address", max_len=500)
    try:
        parts = urlsplit(value)
        host, _ = parts.hostname, parts.port  # reading the port checks that it is a number
    except ValueError:
        raise Invalid("The address is not a valid URL. Use something like "
                      "http://127.0.0.1:11434/v1.") from None
    if parts.scheme not in ("http", "https") or not host:
        raise Invalid("The address must start with http:// or https:// and name a host.")
    if parts.username or parts.password or "%" in parts.netloc:
        raise Invalid("Do not put a name or key in the address. Save the key with the "
                      "provider's key field instead.")
    if parts.query or parts.fragment:
        raise Invalid("The address cannot contain ? or #. Save a key with the provider's "
                      "key field instead.")
    return value.rstrip("/")


def looks_local(url: str | None) -> bool:
    """Whether an address is on the owner's own machines or network.

    Loopback, private and other non-public addresses, single-label names and
    the usual home and office suffixes count. Anything else is treated as
    hosted, so data is never assumed to stay home by accident.
    """
    host = (urlsplit(url or "").hostname or "").lower().rstrip(".")
    if not host:
        return False
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    if _NUMERIC_HOST.match(host):
        # A bare number, or a short or hex form of an IP address, can name any
        # machine on the internet. It is not a name on the owner's network.
        return False
    return host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES)


def effort(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if value not in EFFORTS:
        raise Invalid(f"Effort must be one of: {', '.join(EFFORTS)}.")
    return value


def provider(record: dict[str, Any]) -> Provider:
    """Check a whole provider record and fill in what follows from its type."""
    provider_id = record.get("id")
    if not isinstance(provider_id, str) or not ID.match(provider_id):
        raise Invalid("A provider id must start with a letter and use only lower-case "
                      "letters, digits, - and _ (40 at most).")
    kind = record.get("type")
    if kind not in PROVIDER_TYPES:
        raise Invalid(f"The provider type must be one of: {', '.join(PROVIDER_TYPES)}.")
    label = text(record.get("label"), "The label", max_len=80)
    url = record.get("base_url")
    url = base_url(url) if url not in (None, "") else None
    region = record.get("region")
    region = text(region, "The region", max_len=40) if region not in (None, "") else None
    profile = record.get("profile")
    profile = text(profile, "The profile", max_len=80) if profile not in (None, "") else None
    if kind == "openai_compatible" and url is None:
        raise Invalid("An OpenAI-compatible provider needs an address, such as "
                      "http://127.0.0.1:11434/v1.")
    if kind == "bedrock":
        if region is None:
            raise Invalid("A Bedrock provider needs an AWS region, such as us-east-1.")
        if not re.fullmatch(r"[a-z0-9-]+", region):
            raise Invalid("The region may only contain lower-case letters, digits and -.")
        url = None
    else:
        region = profile = None
    options = dict(record.get("options") or {})
    unknown = set(options) - {"max_tokens", "temperature", "effort", "native_json"}
    if unknown:
        raise Invalid(f"Unknown option: {sorted(unknown)[0]}.")
    max_tokens = options.get("max_tokens")
    if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int)
                                   or not 1 <= max_tokens <= 1_000_000):
        raise Invalid("The answer limit must be a whole number from 1 to 1000000.")
    temperature = options.get("temperature")
    if temperature is not None and (isinstance(temperature, bool)
                                    or not isinstance(temperature, int | float)
                                    or not 0 <= temperature <= 2):
        raise Invalid("Temperature must be a number from 0 to 2.")
    native = options.get("native_json")
    if native is not None and not isinstance(native, bool):
        raise Invalid("native_json must be true, false or left out.")
    options = {
        "max_tokens": max_tokens,
        "temperature": None if temperature is None else float(temperature),
        "effort": effort(options.get("effort")),
        "native_json": native,
    }
    local = record.get("local")
    if local is None:
        # Only an endpoint the owner points at can be on their own machines;
        # the hosted types are hosted unless the owner says otherwise.
        local = kind == "openai_compatible" and looks_local(url)
    if not isinstance(local, bool) or not isinstance(record.get("enabled", True), bool):
        raise Invalid("enabled and local must be true or false.")
    return Provider(id=provider_id, type=kind, label=label, base_url=url,
                    enabled=record.get("enabled", True), local=local, options=options,
                    region=region, profile=profile)


def target(value: Any, what: str) -> Target:
    if not isinstance(value, dict):
        raise Invalid(f"{what} must name a provider and a model.")
    unknown = set(value) - {"provider_id", "model", "effort"}
    if unknown:
        raise Invalid(f"{what} has a field that is not known: {sorted(map(str, unknown))[0][:40]}.")
    provider_id = value.get("provider_id")
    if not isinstance(provider_id, str) or not ID.match(provider_id):
        raise Invalid(f"{what} must name a provider.")
    model = text(value.get("model"), f"{what}'s model", max_len=MAX_MODEL)
    return Target(provider_id, model, effort(value.get("effort")))


def prices(entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or len(entries) > 500:
        raise Invalid("Prices must be a list of at most 500 lines.")
    out, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise Invalid("Each price line must be an object.")
        unknown = set(entry) - {"id", "provider", "model", "input_per_mtok", "output_per_mtok"}
        if unknown:
            raise Invalid("A price line has a field that is not known: "
                          f"{sorted(map(str, unknown))[0][:40]}.")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not ID.match(entry_id):
            raise Invalid("Each price line needs an id of lower-case letters, digits, - and _.")
        if entry_id in seen:
            raise Invalid(f"Two price lines share the id '{entry_id}'. Ids must be unique.")
        seen.add(entry_id)
        line = {
            "id": entry_id,
            "provider": text(entry.get("provider"), "A price line's provider", max_len=40),
            "model": text(entry.get("model"), "A price line's model pattern", max_len=MAX_MODEL),
        }
        for field in ("input_per_mtok", "output_per_mtok"):
            number = entry.get(field)
            if (isinstance(number, bool) or not isinstance(number, int | float)
                    or not 0 <= number <= 1_000_000):
                raise Invalid(f"{field} on price line '{entry_id}' must be a number from 0 "
                              "to 1000000.")
            line[field] = float(number)
        out.append(line)
    return out
