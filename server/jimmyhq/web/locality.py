"""Where a request came from: straight from this computer, or relayed to it.

This is the one place that decides it. The allowed-hosts check, the sign-in pause, the
app API, the session cookie, the device route and the door all ask here.

A reverse proxy or tunnel on this computer (Tailscale Serve is one) connects from the
loopback address, just as the owner's own browser does, so the peer address alone cannot
tell them apart. What tells them apart is that a relayed request carries a forwarding
header, or is addressed to a name other than the machine's own. Such a request is never
"this computer": it does not count as local, it is not answered for a local name, and it
is counted under a key of its own (``client_key``), never under the loopback address.

A tunnel that adds no forwarding header at all (a raw TCP forward) cannot be told apart
here; see docs/remote.md.
"""

from __future__ import annotations

import ipaddress
from typing import Any

LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")
# tailscaled sets this on a request that came in through Funnel, from the public
# internet, and removes any copy a client sent itself.
FUNNEL_HEADER = "tailscale-funnel-request"
FORWARDED_PREFIX = "forwarded:"
_MAX_VIA = 64  # a longer forwarding entry is cut, so it cannot fill a table
_ANY_ADDRESS = ("", "0.0.0.0", "::")  # noqa: S104 - compared with, never bound


def host_name(header: str | None) -> str:
    """The name part of a Host header, without the port, in lower case."""
    value = (header or "").strip().lower()
    if value.startswith("["):  # an IPv6 address in brackets
        return value[1:].split("]", 1)[0]
    return value.rsplit(":", 1)[0] if ":" in value else value


def is_loopback(address: str | None) -> bool:
    try:
        parsed = ipaddress.ip_address(address or "")
    except ValueError:
        return False
    return (getattr(parsed, "ipv4_mapped", None) or parsed).is_loopback


def peer(request: Any) -> str:
    """The address that connected, as the server saw it."""
    return request.client.host if request.client else "unknown"


def is_local_name(name: str, bound: str = "") -> bool:
    """Whether a host name is one of this computer's own: ``localhost`` (or a name under
    it), a loopback address, or the address the server is bound to. A final dot is
    ignored, as a browser and a resolver ignore it."""
    name = name.strip().lower().rstrip(".")
    if not name:
        return False
    if name == "localhost" or name.endswith(".localhost") or is_loopback(name):
        return True
    if name in ("0.0.0.0", "::"):  # noqa: S104 - a browser takes these for this computer
        return True
    return bool(bound) and bound not in _ANY_ADDRESS and name == bound.strip().lower()


def is_forwarded(request: Any) -> bool:
    """True when the request says something relayed it."""
    return any(h in request.headers for h in FORWARDING_HEADERS)


def is_public(request: Any) -> bool:
    """True when Tailscale says the request came from the public internet (Funnel)."""
    return FUNNEL_HEADER in request.headers


def is_local_request(request: Any) -> bool:
    """True only for a request made on this machine, directly to this server.

    A reverse proxy or tunnel on the same machine also connects from the local
    address, so a request that carries forwarding headers, or is addressed to
    anything but the machine's own names, does not count.
    """
    if not is_loopback(peer(request)):
        return False
    if is_forwarded(request):
        return False
    return host_name(request.headers.get("host")) in LOCAL_NAMES


def client_key(request: Any, bound: str = "") -> str:
    """Who to count a request under, for pauses and "is it this machine" checks.

    The peer address, except for a request relayed by something on this computer: a
    loopback peer that is not a local request, or a peer at the address the server is
    bound to that carries a forwarding header. That one is ``forwarded:<who it was
    for>``, which is not an address, so it is never taken for this computer and never
    shares a count with the owner sitting at it."""
    address = peer(request)
    if is_loopback(address):
        name = host_name(request.headers.get("host"))
        if not is_forwarded(request) and (name in LOCAL_NAMES or name == bound.lower()):
            return address
    elif not (bound not in _ANY_ADDRESS and address == bound and is_forwarded(request)):
        return address
    return FORWARDED_PREFIX + (relayed_for(request) or "unknown")


def relayed_for(request: Any) -> str:
    """Who a relayed request was for, as the nearest relay wrote it, or "".

    Only the last entry of a forwarding header is taken: a proxy appends the address it
    took the request from, so everything before that entry is whatever the client chose
    to send, and a client could otherwise pick its own key (and a fresh one for every
    try). The entry is cut to ``_MAX_VIA`` characters."""
    for header in ("x-forwarded-for", "x-real-ip", "forwarded"):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value.rsplit(",", 1)[-1].strip()[:_MAX_VIA]
    return ""


def public_refusal(port: int) -> str:
    """What a request from the public internet is told, with the command that stops it."""
    which = f"--https={port} " if port else ""
    return ("JimmyHQ does not answer the public internet, and Tailscale Funnel has made "
            "this address public. Nothing was done. On the computer JimmyHQ runs on, turn "
            f"Funnel off with `tailscale funnel {which}off` (`tailscale funnel status` shows "
            "which port it is on) and use the address from your own devices on your "
            "tailnet. To answer the public internet anyway, set allow_public = true in "
            "config.toml and restart.")


def over_https(request: Any) -> bool:
    """Whether the browser reached JimmyHQ over HTTPS: directly, or through a proxy on
    this computer that says so (Tailscale Serve ends HTTPS and forwards plain HTTP)."""
    if request.url.scheme == "https":
        return True
    if not is_loopback(peer(request)):
        return False
    proto = request.headers.get("x-forwarded-proto") or ""
    return proto.split(",", 1)[0].strip().lower() == "https"
