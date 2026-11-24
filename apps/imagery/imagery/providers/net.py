"""The one way a provider reaches the network: JimmyHQ's ``net.fetch``, to a host the owner
agreed to, with a key that is taken out of anything that might be shown.

``post_json`` returns what came back (``Answer``) for any HTTP status; it raises
``ProviderError`` only when there was no answer, or JimmyHQ itself refused the request.
Reading the answer, and what a status means, is each provider's own business.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jimmyhq_app import CallError

from imagery.providers import ProviderError

MAX_SAID = 300


@dataclass(frozen=True)
class Answer:
    status: int
    data: Any  # the parsed JSON body, or None when it was not JSON
    text: str


def scrub(text: Any, *secrets: str | None) -> str:
    """Text that may be shown or kept, with each secret taken out wherever it appears."""
    out = str(text or "")
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[your key]")
    return out


def clip(text: Any, most: int = MAX_SAID) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= most else line[:most - 1].rstrip() + "…"


MAX_KB_LABEL = "Largest network answer for an app (KB)"
WAIT_LABEL = "Longest wait for an app's network request (seconds)"
OWN_WAIT_LABEL = "Wait for the service for (seconds)"
OWN_NETWORK = "Addresses on your own network"


def post_json(app: Any, url: str, *, headers: dict[str, str], body: dict[str, Any],
              timeout_s: int, who: str, secret: str | None = None, need_kb: int = 8192,
              need_s: int = 120, own_address: str | None = None) -> Answer:
    """POST ``body`` as JSON. ``who`` names the service in sentences; ``secret`` is a key
    to take out of any sentence. ``need_kb`` and ``need_s`` are what the service may need
    (an answer's size, a wait), so a refusal for the owner's lower limit can say what to
    raise it to. ``own_address`` is set when the request goes to an address on the owner's
    own network, so a refusal can say where he adds it."""
    try:
        reply = app.net.fetch(url, method="POST", headers=headers, json=body,
                              timeout_s=timeout_s)
    except CallError as error:
        raise _from_platform(error, who, secret, need_kb=need_kb, need_s=need_s,
                             own_address=own_address) from None
    text = str(reply.get("text") or "")
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:
        data = None
    return Answer(int(reply.get("status") or 0), data, scrub(text, secret))


def _size_words(kb: int) -> str:
    return f"{kb // 1024} MB" if kb >= 1024 and kb % 1024 == 0 else f"{kb} KB"


def _number(pattern: str, said: str) -> int | None:
    found = re.search(pattern, said)
    return int(found.group(1)) if found else None


def not_added_sentence(address: str, who: str, said: str = "") -> str:
    """The sentence for an address on the owner's own network that JimmyHQ has not been
    told to let Imagery reach: nothing was sent, and where to add it."""
    return (
        f"JimmyHQ did not let Imagery reach {address}, so nothing was sent. An address on "
        f"your own network has to be added by you first: open Settings, Apps, Imagery, find "
        f"\"{OWN_NETWORK}\", and add exactly {address} there (the same scheme, name and "
        "port), then try again."
        + (f" JimmyHQ said: {clip(said)}" if said and OWN_NETWORK not in said else ""))


def _from_platform(error: CallError, who: str, secret: str | None, *, need_kb: int = 8192,
                   need_s: int = 120, own_address: str | None = None) -> ProviderError:
    said = scrub(error.message, secret)
    if error.code == "not_allowed":
        if own_address:
            return ProviderError("setup", not_added_sentence(own_address, who, said),
                                 sent=False)
        return ProviderError("setup", (
            f"JimmyHQ did not let Imagery reach {who}, so nothing was sent. {clip(said)} "
            "Check that Imagery's permissions were agreed to when it was installed, and for "
            "your own server that its address is listed under Your own addresses."),
            sent=False)
    if error.code == "too_large":
        limit = _number(r"larger than the (\d+) KB the owner lets", said)
        if limit is not None or "net_expects" not in said:
            allowed = f"the {_size_words(limit)}" if limit else "what"
            return ProviderError("setup", (
                f"{who} answered with a picture larger than {allowed} JimmyHQ lets an "
                "app be given, so none of it was kept. The request may still have been "
                f"charged. Raise \"{MAX_KB_LABEL}\" under Settings, Apps, advanced, to "
                f"{need_kb} or more ({_size_words(need_kb)}), then try again."))
        return ProviderError("setup", (
            f"{who} answered with a picture larger than Imagery expected, so none of it "
            f"was kept. The request may still have been charged. {clip(said)}"))
    if error.code == "quota":
        return ProviderError("busy", (
            f"JimmyHQ's own limit on this app's network requests is used up for now, so "
            f"nothing was sent to {who}. {clip(said)} Wait a while, or raise the limit "
            "under Settings, Apps, Limits for one app."), sent=False)
    if error.code == "unavailable":
        ceiling = _number(r"longer than (\d+) seconds to answer, the most the owner", said)
        asked = _number(r"longer than (\d+) seconds, the wait you asked for", said)
        if ceiling is not None:
            return ProviderError("timeout", (
                f"JimmyHQ stopped waiting for {who} after {ceiling} seconds, the most it "
                "lets an app wait, so nothing came back. The request may still have been "
                f"charged. Raise \"{WAIT_LABEL}\" under Settings, Apps, advanced, to "
                f"{need_s} or more, then try again."))
        if asked is not None:
            return ProviderError("timeout", (
                f"{who} took longer than the {asked} seconds Imagery waits, so nothing "
                "came back. The request may still have been charged. Raise \""
                f"{OWN_WAIT_LABEL}\" in Imagery's advanced settings, then try again."))
        return ProviderError("timeout", (
            f"{who} did not answer in time, or could not be reached, so nothing was made. "
            f"{clip(said)} Try again. If it keeps happening, check that it is running and "
            f"that the address is right."))
    return ProviderError("other", (
        f"JimmyHQ could not send the request to {who}, so nothing was made. {clip(said)}"),
        sent=error.code not in ("invalid", "internal"))
