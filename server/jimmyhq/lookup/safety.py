"""Where web lookup may go. Every address a model asks for passes through here.

A model chooses the URL, and a page can talk a model into choosing one. So
nothing about an address is trusted until it has been checked, and what was
checked is what is connected to:

- **Shape.** Only ``http`` and ``https``. No user name or password. No
  backslash, control character or space. A host that is a number in an odd
  form (decimal, hex, octal, fewer than four parts) is refused outright:
  those forms exist to slip past checks like this one.
- **Address.** The host is resolved once. Every address it resolves to must
  be public: not loopback, private, link-local, carrier-grade NAT (which is
  where tailnets live), multicast, unspecified, reserved or a documentation
  range, in IPv4 or IPv6, including an IPv4 address carried inside an IPv6
  one (mapped, compatible, 6to4, NAT64, Teredo).
- **Pinned.** ``Checked.address`` is the address that passed. The fetch
  connects to that address and never resolves the name again, so a name that
  answers "public" to the check and "private" a moment later gains nothing.
- **Every hop.** A redirect is a new URL and goes through ``check`` again.
- **Never itself.** JimmyHQ's own address and port are refused whatever the
  settings say, as is any name the owner reaches JimmyHQ by.
- **The owner's exceptions.** A host named in ``lookup.allow_private_hosts``
  skips the address rule, and only that rule.

Nothing here opens a connection except the resolver.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from jimmyhq.core import Core
from jimmyhq.lookup.errors import WebError

MAX_URL = 4000
SCHEMES = {"http": 80, "https": 443}

Resolver = Callable[[str, int], Awaitable[list[str]]]
IP = ipaddress.IPv4Address | ipaddress.IPv6Address

_NAME = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)(\.(?!-)[a-z0-9_-]{1,63}(?<!-))*$")
# A label that a browser would read as a number: digits, or 0x and hex digits.
_NUMBER = re.compile(r"^(?:0x[0-9a-f]*|[0-9]+)$")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")

PRIVATE = ("Web lookup does not read addresses on your own machine or private network, and "
           "{host} is one. If you mean it to, add {host} under Settings, Web lookup, "
           "\"Private hosts a model may read\".")
ITSELF = "Web lookup does not read JimmyHQ's own address. Nothing was fetched."


@dataclass(frozen=True)
class Checked:
    """A URL that may be fetched, and the one address to connect to for it."""

    url: str  # as it will be shown and recorded: no fragment
    scheme: str
    host: str  # the name (or address) the URL gave, lower case
    port: int
    target: str  # path and query, starting with "/"
    address: str  # the checked address: connect here, and nowhere else
    allowed_private: bool = False  # the owner named this host as an exception

    @property
    def host_header(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == SCHEMES[self.scheme] else f"{host}:{self.port}"

    @property
    def connect_url(self) -> str:
        """The same request, addressed to the checked address."""
        address = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.scheme}://{address}:{self.port}{self.target}"


@dataclass(frozen=True)
class Parsed:
    url: str
    scheme: str
    host: str
    port: int
    target: str
    literal: IP | None  # set when the host is an address, not a name


def parse(url: object) -> Parsed:
    """A URL's parts, or a refusal. No network."""
    if not isinstance(url, str) or not url.strip():
        raise WebError("bad_url", "A web address is needed, such as https://example.com/page.")
    url = url.strip()
    if len(url) > MAX_URL:
        raise WebError("bad_url", f"That web address is longer than {MAX_URL} characters. "
                                  "Use a shorter one.")
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in url) or "\\" in url:
        raise WebError("bad_url", "That web address has a space, a backslash or a control "
                                  "character in it. Send the address on its own.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise WebError("bad_url", "That is not a web address that can be read. Use one "
                                  "like https://example.com/page.") from None
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise WebError("bad_url", "Only http:// and https:// addresses can be read.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise WebError("bad_url", "A web address with a user name or password in it is not "
                                  "read. Send the address without them.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise WebError("bad_url", "That web address names no host. Use one like "
                                  "https://example.com/page.")
    if port is None:
        port = SCHEMES[scheme]
    if not 1 <= port <= 65535:
        raise WebError("bad_url", "That web address has a port that does not exist.")
    literal = _literal(host)
    if literal is None:
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            host = ""
        if not host or len(host) > 253 or not _NAME.match(host):
            raise WebError("bad_url", "That web address has a host name that cannot be "
                                      "looked up.")
    else:
        host = str(literal)
    target = parts.path or "/"
    if parts.query:
        target += f"?{parts.query}"
    shown = f"[{host}]" if ":" in host else host
    if port != SCHEMES[scheme]:
        shown += f":{port}"
    return Parsed(f"{scheme}://{shown}{target}", scheme, host, port, target, literal)


def _literal(host: str) -> IP | None:
    """The address a host is, when it is one written the ordinary way. A host
    that only a lenient parser would read as an address is refused."""
    if ":" in host:
        if "%" in host:
            raise WebError("refused", "A web address with a network zone in it is not read.")
        try:
            return ipaddress.IPv6Address(host)
        except ValueError:
            raise WebError("bad_url", "That web address has a host that cannot be read."
                           ) from None
    try:
        return ipaddress.IPv4Address(host)
    except ValueError:
        pass
    if _NUMBER.match(host.rsplit(".", 1)[-1]):
        # 2130706433, 0x7f.1, 0177.0.0.1, 127.1: all one machine's own address
        # to a lenient parser, and none of them a name anyone is given.
        raise WebError("refused", "That web address writes its host as a number in an "
                                  "unusual form, which is not read. Use the site's name.")
    return None


def is_public(ip: IP) -> bool:
    """Whether an address is out on the public internet: somewhere a stranger's
    web page could be, and nowhere a private service could."""
    if isinstance(ip, ipaddress.IPv6Address):
        inner: list[IP] = []
        if ip.ipv4_mapped is not None:
            inner.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            inner.append(ip.sixtofour)
        if ip.teredo is not None:
            inner.extend(ip.teredo)
        if ip in _NAT64:
            inner.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip in _NAT64_LOCAL or int(ip) >> 32 == 0:
            # Locally run NAT64, and the old "IPv4-compatible" form (which
            # also holds :: and ::1).
            return False
        if inner:
            return all(is_public(part) for part in inner)
        if ip.is_site_local:
            return False
    return bool(ip.is_global) and not (ip.is_multicast or ip.is_reserved or ip.is_loopback
                                       or ip.is_link_local or ip.is_unspecified
                                       or ip.is_private)


async def system_resolver(host: str, port: int) -> list[str]:
    """Every address the system's resolver gives for a name."""
    loop = asyncio.get_running_loop()
    try:
        found = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        return []
    out: list[str] = []
    for *_, sockaddr in found:
        address = str(sockaddr[0]).split("%", 1)[0]
        if address not in out:
            out.append(address)
    return out


def _own_names(core: Core) -> set[str]:
    names = {core.config.host.strip().lower().strip("[]")}
    for listed in (core.config.allowed_hosts, core.settings.get("access.allowed_hosts")):
        for name in listed:
            name = name.strip().lower()
            if name.startswith("["):
                name = name[1:].split("]", 1)[0]
            elif name.count(":") == 1:
                name = name.split(":", 1)[0]
            names.add(name.rstrip("."))
    return names - {""}


def own_names(core: Core) -> set[str]:
    """Every name the owner reaches JimmyHQ by: never a place an app may be sent."""
    return _own_names(core)


# Ports this process listens on, on loopback only, that the settings do not name: each
# browser session's fence proxy takes a free one. Kept here so that an address on this
# computer can be refused while JimmyHQ itself answers there.
_LISTENING_HERE: set[int] = set()


def listening_here(port: int) -> None:
    """Note a loopback port this process has just started listening on."""
    _LISTENING_HERE.add(int(port))


def stopped_listening_here(port: int) -> None:
    _LISTENING_HERE.discard(int(port))


def ports_listening_here() -> frozenset[int]:
    """The loopback ports this process listens on now, beyond its own main port."""
    return frozenset(_LISTENING_HERE)


def allowed_private(core: Core) -> set[str]:
    return set(core.settings.get("lookup.allow_private_hosts"))


async def check(core: Core, url: object, resolver: Resolver = system_resolver) -> Checked:
    """A URL checked and resolved, with the one address to connect to. Raises
    ``WebError`` with a sentence when it may not be fetched."""
    parsed = parse(url)
    own_port = parsed.port == core.config.port
    if own_port and parsed.host in _own_names(core):
        raise WebError("refused", ITSELF)
    if parsed.literal is not None:
        addresses: list[IP] = [parsed.literal]
    else:
        addresses = []
        for found in await resolver(parsed.host, parsed.port):
            try:
                addresses.append(ipaddress.ip_address(found))
            except ValueError:
                continue
        if not addresses:
            raise WebError("unreachable", f"No site called {parsed.host} could be found. "
                                          "Check the spelling of the address.")
    # Every address must pass, not only the one that will be used: a name that
    # mixes public and private answers is not one to trust.
    public = all(is_public(ip) for ip in addresses)
    if own_port and not public:
        # Whatever the owner has allowed, a private address on JimmyHQ's own
        # port may be JimmyHQ.
        raise WebError("refused", ITSELF)
    excepted = parsed.host in allowed_private(core)
    if not public and not excepted:
        raise WebError("refused", PRIVATE.format(host=parsed.host))
    return Checked(parsed.url, parsed.scheme, parsed.host, parsed.port, parsed.target,
                   str(addresses[0]), allowed_private=excepted and not public)
