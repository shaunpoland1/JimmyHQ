"""What the hub and a node agree on. Both sides import this and nothing else of
each other's, and nothing here does I/O.

The link is plain HTTP that the node opens. The hub never connects to a node.

- The node says it is alive, and what it offers, with ``POST /api/nodes/link/heartbeat``.
- The node asks for work with ``GET /api/nodes/link/jobs`` (held open until there
  is a job, or the wait runs out).
- The node answers one job with ``POST /api/nodes/link/jobs/<id>/reply``, whose
  body is a run of frames sent as they are made (see ``frame``).

A job is ``{"id", "kind", "offer", ...}``. Stage 1 has one kind, ``model``: an
HTTP request for the model server the node keeps under that offer's name. The
kind ``browser`` is reserved for a browser offered by a node; see docs/nodes.md.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

# Raised when a hub and a node could no longer understand each other. Two
# machines with different protocol numbers do no work together.
PROTOCOL = 1

LINK = "/api/nodes/link"
JOIN = "/api/nodes/join"
RELAY = "/api/nodes/relay"
VERSION_HEADER = "x-jimmyhq-version"
PROTOCOL_HEADER = "x-jimmyhq-protocol"

# What a node can lend. "browser" is declared so the browser package can plug in
# without changing the protocol; nothing offers one yet.
OFFERS = ("models", "browser")
JOB_KINDS = {"models": "model", "browser": "browser"}
# A version is shown to the owner inside sentences, so it is kept to what a
# version number is made of.
VERSION = re.compile(r"[0-9A-Za-z.+-]{1,40}")
OFFER_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")
MAX_OFFERED = 16  # names of one kind a node may offer

# The only places on a model server a ``model`` job may ask for. The hub chooses
# from these; the node refuses anything else, so the hub can never make a node
# fetch an address the owner did not configure.
MODEL_PATHS = {"/models": "GET", "/chat/completions": "POST", "/embeddings": "POST"}

# Frames of a reply: one byte for the type, four for the length, then the payload.
HEAD, DATA, ERROR, END = b"H", b"D", b"E", b"Z"
MAX_FRAME = 1024 * 1024
CHUNK = 64 * 1024
# Why a node could not do a job. The hub turns the word into its own sentence and
# never shows text that a node sent.
ERRORS = ("unreachable", "timeout", "broken", "no_offer", "no_handler", "bad_job")

_DOCUMENTATION = tuple(ipaddress.ip_network(n) for n in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32"))


def frame(kind: bytes, payload: bytes | dict[str, Any] = b"") -> bytes:
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
    if len(payload) > MAX_FRAME:
        raise ValueError("a frame cannot be larger than 1 MB")
    return kind + len(payload).to_bytes(4, "big") + payload


class FrameReader:
    """Puts the frames of a reply back together from bytes as they arrive."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[tuple[bytes, bytes]]:
        """The whole frames now available. Raises ValueError for a frame of an
        unknown type or an impossible size: the rest of the reply cannot be trusted."""
        self._buffer += data
        out: list[tuple[bytes, bytes]] = []
        while len(self._buffer) >= 5:
            kind = bytes(self._buffer[:1])
            size = int.from_bytes(self._buffer[1:5], "big")
            if kind not in (HEAD, DATA, ERROR, END) or size > MAX_FRAME:
                raise ValueError("not a frame")
            if len(self._buffer) < 5 + size:
                break
            out.append((kind, bytes(self._buffer[5:5 + size])))
            del self._buffer[:5 + size]
        return out


def complete_events(buffer: bytes) -> tuple[bytes, bytes]:
    """Split a stream of server-sent events into (whole events, the unfinished
    rest). Only whole events are passed on, so when a node goes away part-way
    through one, what the reader already has still parses."""
    cut = max(buffer.rfind(b"\n\n") + 2 if b"\n\n" in buffer else 0,
              buffer.rfind(b"\n\r\n") + 3 if b"\n\r\n" in buffer else 0)
    return buffer[:cut], buffer[cut:]


def reconnect_delay(failures: int, longest: float = 30.0) -> float:
    """Seconds a node waits before trying the hub again after this many failures
    in a row: 1, 2, 4 and so on, never longer than ``longest``."""
    if failures <= 0:
        return 0.0
    return float(min(longest, 2 ** min(failures - 1, 16)))


def skew(hub_version: str, hub_protocol: int, node_version: str | None,
         node_protocol: int | None, name: str) -> tuple[str | None, bool]:
    """What to say when the hub and a node run different versions, and whether
    they must stop working together until that is fixed.

    Different protocol numbers stop the work: neither side can be sure what the
    other means. Different versions with the same protocol only earn a sentence.
    """
    if node_version is None or node_protocol is None:
        return None, False
    if node_protocol != hub_protocol:
        older = "this machine" if hub_protocol < node_protocol else name
        return (f"This machine runs JimmyHQ {hub_version} and {name} runs {node_version}, "
                f"and the two cannot work together. Nothing is sent to {name} until they "
                f"match: update {older}."), True
    if node_version != hub_version:
        return (f"This machine runs JimmyHQ {hub_version} and {name} runs {node_version}. "
                "They still work together. Update the older one when you can."), False
    return None, False


def skew_on_node(hub_version: str, hub_protocol: int, version: str,
                 protocol: int) -> tuple[str | None, bool]:
    """The same finding, said from the node's side for its own log and terminal."""
    start = f"The hub runs JimmyHQ {hub_version} and this machine runs {version}"
    if protocol != hub_protocol:
        older = "this machine" if protocol < hub_protocol else "the hub"
        return (f"{start}, and the two cannot work together. This machine does no work for "
                f"the hub until they match: update {older}."), True
    if version != hub_version:
        return f"{start}. They still work together. Update the older one when you can.", False
    return None, False


def is_private_address(address: str) -> bool:
    """Whether an IP address is this machine or a private network: loopback, the
    private ranges, link-local, and the shared range mesh VPNs hand out. A public
    address is not, and nor are the ranges reserved for documentation."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    parsed = getattr(parsed, "ipv4_mapped", None) or parsed
    if any(parsed in net for net in _DOCUMENTATION if net.version == parsed.version):
        return False
    return not parsed.is_global and not parsed.is_multicast and not parsed.is_unspecified


def resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def hub_address(value: str) -> str:
    """The hub's address as the node keeps it: scheme, host and port, nothing
    else. Raises ValueError with a sentence that says what to type."""
    value = value.strip()
    if "://" not in value:
        value = f"http://{value}"
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        host = port = None
        parts = None
    if (parts is None or parts.scheme not in ("http", "https") or not host or parts.username
            or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment
            or not re.fullmatch(r"[A-Za-z0-9.:-]+", host)):
        raise ValueError("The hub's address is not one this can use. Type it as you would in "
                         "a browser on this machine, such as http://192.0.2.10:8440.")
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}" + (f":{port}" if port else "")


def plain_http_refusal(hub: str, accepted: bool,
                       resolver: Callable[[str], Iterable[str]] = resolve) -> str | None:
    """Why a node will not talk to this hub, or None if it will.

    HTTPS is always fine. Plain HTTP is fine to this machine itself and to a
    private address, where the network (a home network, a mesh VPN) is what
    keeps strangers out. To anything else the join code and the node's token
    would cross networks the owner does not control in the clear, so it is
    refused unless the owner has said they accept that.
    """
    parts = urlsplit(hub)
    if parts.scheme == "https" or accepted:
        return None
    host = parts.hostname or ""
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            addresses = list(resolver(host))
        except OSError:
            addresses = []
        if not addresses:
            return (f"Nothing was sent: the name {host} could not be looked up from this "
                    "machine. Check the hub's address, and that this machine is on the same "
                    "private network as the hub.")
    if all(is_private_address(a) for a in addresses):
        return None
    return (f"Nothing was sent: {host} is not on a private network and the address is plain "
            "http, so the join code and this machine's token would cross the internet "
            "unprotected. Use an https address, or a private network that encrypts (a mesh "
            "VPN). To go ahead anyway, add --allow-plain-http.")


def offer_names(value: Any, what: str) -> list[str]:
    """A node's list of offered names, checked. Raises ValueError with a sentence."""
    if not isinstance(value, list) or len(value) > MAX_OFFERED:
        raise ValueError(f"A node can offer at most {MAX_OFFERED} {what}.")
    names: list[str] = []
    for name in value:
        if not isinstance(name, str) or not OFFER_NAME.fullmatch(name):
            raise ValueError(f"A name for one of a node's {what} is 1 to 16 characters: "
                             "lower-case letters, digits, - and _.")
        if name not in names:
            names.append(name)
    return names
