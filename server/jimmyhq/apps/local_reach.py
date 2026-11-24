"""Addresses on the owner's own network that he lets one app reach.

An app reaches only public https hosts: what its manifest names (``net:<host>``) and what
the owner gives it (``owner_hosts``), each checked at every request against web lookup's
address rule (``lookup.safety``), so a name can never be used to reach the owner's own
machine or network. That rule is what keeps an app from probing his router, his printer
or JimmyHQ itself (server-side request forgery). Some services an owner wants an app to
use live exactly there: an image server on another computer at home, one on a tailnet,
one on this computer. So the owner, and only the owner, may make one exception at a time,
for one app, for one exact address::

    GET    /api/apps/<id>/local-addresses                 owner  what it has, how many
    POST   /api/apps/<id>/local-addresses/check  {address} owner  where it leads, now
    POST   /api/apps/<id>/local-addresses  {address, resolved}  owner  add it
    POST   /api/apps/<id>/local-addresses/<key>/confirm  {resolved}  owner  where it leads now
    DELETE /api/apps/<id>/local-addresses/<key>           owner  take one back

The app cannot add, ask for or change one: there is no call for it, and its manifest says
nothing about it. All an app can do is tell the owner, in its own settings, which address
to add. The signed-in owner adds it on the app's page under Apps, from JimmyHQ's own page
only, in two steps: ``check`` resolves the name and answers with the addresses it leads to,
which the warning dialog shows; ``add`` carries those addresses back, and is refused if the
name leads anywhere else by then. Those addresses are stored with the allowance.

**The rules for an address**, each a function below, checked when it is added, when it is
confirmed, and again at every request:

- ``read_address``: one exact origin, ``http`` or ``https``, a host, and a port (the
  scheme's own when none is written). No path, query, sign-in part or wildcard; a host
  written as a number in an odd form is refused (web lookup's ``parse``).
- ``metadata_problem``: never a cloud provider's metadata service, by name or address
  (all of link-local, which is where they live, and the known others), in any of the forms
  an IPv6 address can carry an IPv4 one (``carried``).
- ``own_port_problem``: never a port JimmyHQ itself listens on, on any host: its main port,
  and the door's own port when the door is set to listen there. On this computer, also any
  port this process listens on now (each browser session's fence proxy).
- ``own_name_problem``: never a name JimmyHQ itself is reached by (``safety.own_names``, the
  door's names), on any port: such a name may lead to JimmyHQ through a tunnel or a
  ``tailscale serve`` mapping on a port of its own. ``localhost`` and an address written as
  one are not names of this kind: they are checked by the rules on ports and loopback.
- ``loopback_problem``: an address on this computer itself (loopback) only when the owner
  typed ``localhost`` or a loopback address, never through some other name.
- ``public_problem``: every address is on the owner's own machine or network (a public
  host is given the usual way, over https).
- ``check_new`` and ``check_room``: not one it has; at most ``apps.local_addresses_max``.

**At every request** (``check_at_connect``, from ``bridge.net_fetch``): the request's
scheme, host and port must be exactly one the owner added (``matches``); any other port,
scheme or name falls to the ordinary rule, which refuses anything private. The name is
resolved again, every rule above is checked again, and every address it gives now must be
one of those stored when the owner added or last confirmed it (``moved_problem``): whoever
runs that name's DNS cannot point it at another machine on his network. The request
connects to the address that was checked and nowhere else, and its receipt
(``apps.fetched``) names that address. Redirects are handed back to the app, never
followed, and the next address is checked like any other.

**On the record**: ``apps.local_address_added`` (with the addresses it led to),
``apps.local_address_confirmed`` (the owner accepted where it leads now) and
``apps.local_address_removed``, folded by the ``apps`` lens into the app's row, read afresh
by every process on every call (``bridge.find_app``). Removing the app drops them with its
row, and so does an update or a rollback that changes its tier. A private app is held to
the same rule; it gains nothing more.

**What cannot be known here**: a port some other program opened for JimmyHQ, such as the
web dev server while JimmyHQ is being worked on, is not JimmyHQ's own to know of, so it is
not refused by these rules (docs/apps.md says so).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from jimmyhq.apps import kinds, store
from jimmyhq.auth import Principal
from jimmyhq.web.deps import owner

if TYPE_CHECKING:
    from jimmyhq.core import Core
    from jimmyhq.lookup.safety import Checked, Parsed, Resolver

MAX = "apps.local_addresses_max"
_EXAMPLE = "http://192.0.2.10:8000"
_TYPED_MOST = 300
_KEY_CHARS = 16
_RESOLVED_MOST = 32  # addresses one name may lead to, as stored and as sent back
IP = ipaddress.IPv4Address | ipaddress.IPv6Address

# Where cloud providers answer with a machine's credentials. All of link-local is refused
# (169.254.169.254 and the others live there); these are the ones outside it.
_METADATA_NETS = (*(ipaddress.ip_network(n) for n in (
    "169.254.0.0/16", "fe80::/10",  # link-local: AWS, GCP, Azure, ECS, and more
    "fd00:ec2::254/128",  # AWS over IPv6
    "fd20:ce::254/128",  # Google Cloud over IPv6, as its metadata documentation gives it
    # Oracle Cloud over IPv6. Not found in Oracle's own documentation when this was
    # written; cloud-init's Oracle data source uses it as the metadata address.
    "fd00:c1::a9fe:a9fe/128",
    "0.0.0.0/8", "::/128",  # nowhere, which some systems read as this machine
    "224.0.0.0/4", "ff00::/8", "255.255.255.255/32",  # many machines at once
)), ipaddress.IPv4Network((0x646464C8, 32)))  # Alibaba Cloud's, written as a number
# Checked against the last 32 bits of every IPv6 address, whatever its prefix: a local
# translator or tunnel may carry an IPv4 address there in a form not listed below.
_LOW_METADATA = (ipaddress.ip_network("169.254.0.0/16"),
                 ipaddress.IPv4Network((0x646464C8, 32)))
_METADATA_NAMES = frozenset({"metadata", "metadata.google.internal", "metadata.goog",
                             "instance-data", "instance-data.ec2.internal",
                             "metadata.azure.internal"})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")  # a network's own translator
_SIIT = ipaddress.ip_network("::ffff:0:0:0/96")  # "IPv4-translated": ::ffff:0:a.b.c.d
_ISATAP_MASK, _ISATAP = 0xFCFFFFFF, 0x00005EFE  # interface id 0[0-3]00:5efe:a.b.c.d
_LOCALHOST = "localhost"
WARNING = ("{app} will be able to send to and read from {address} on your own network, "
           "exactly that address and port and nothing else there. Only add an address you "
           "trust {app} with.")
LEADS = (" {host} leads to {found} just now. If it later leads anywhere else, {app} is "
         "stopped from reaching it until you confirm the new address here.")


class AddressRefused(Exception):
    """An address that was not added or removed. ``detail`` says so and what to do."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Address:
    """One exact origin on the owner's own network."""

    scheme: str
    host: str  # lower case; an IP address written the ordinary way, without brackets
    port: int

    @property
    def origin(self) -> str:
        return origin_of(self.scheme, self.host, self.port)

    @property
    def key(self) -> str:
        return key_of(self.origin)


def origin_of(scheme: str, host: str, port: int) -> str:
    """``scheme://host:port``, the port always written, an IPv6 host in brackets."""
    shown = f"[{host}]" if ":" in host else host
    return f"{scheme}://{shown}:{port}"


def key_of(origin: str) -> str:
    """A short id for one address, to name it in a route without putting it in a path."""
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()[:_KEY_CHARS]


# --- the rules, one function each -----------------------------------------------------------


def read_address(text: Any) -> Address:
    """Rule ``one_exact_origin``: what the owner typed, as one scheme, host and port."""
    from jimmyhq.lookup import safety  # noqa: PLC0415
    from jimmyhq.lookup.errors import WebError  # noqa: PLC0415

    if not isinstance(text, str) or not text.strip():
        raise AddressRefused(422, f"Type the address with its port, such as {_EXAMPLE}. "
                                  "Nothing was added.")
    text = text.strip()
    if len(text) > _TYPED_MOST or "*" in text:
        raise AddressRefused(422, f"Type one exact address, such as {_EXAMPLE}. Nothing was "
                                  "added.")
    if "://" not in text:
        raise AddressRefused(422, "Type the address with http:// or https:// in front, so it "
                                  f"is clear which one is meant, such as {_EXAMPLE}. Nothing "
                                  "was added.")
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 - raises for a port that is not a number
    except ValueError:
        raise AddressRefused(422, f"That address cannot be read. Type one such as {_EXAMPLE}. "
                                  "Nothing was added.") from None
    if parts.path not in ("", "/") or parts.query or parts.fragment or "#" in text \
            or "?" in text:
        raise AddressRefused(422, "Type the address with nothing after its port: no page, "
                                  f"such as {_EXAMPLE}. The app chooses the page. Nothing "
                                  "was added.")
    try:
        parsed = safety.parse(text)
    except WebError as error:
        raise AddressRefused(422, f"{error.message} Nothing was added.") from None
    return Address(parsed.scheme, parsed.host, parsed.port)


def _rfc6052(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """The IPv4 address a translator's prefix carries, for each prefix length RFC 6052
    allows (32 to 64 bits: the address follows it, skipping bits 64 to 71)."""
    bits = format(int(ip), "0128b")
    out = []
    for length in (32, 40, 48, 56, 64):
        taken = [i for i in range(length, 128) if not 64 <= i < 72][:32]
        out.append(ipaddress.IPv4Address(int("".join(bits[i] for i in taken), 2)))
    return out


def carried(ip: IP) -> list[IP]:
    """The address and every IPv4 address an IPv6 one carries inside it: mapped, 6to4,
    Teredo, NAT64 (the well-known prefix and a network's own, ``64:ff9b:1::/48``),
    "IPv4-translated" (``::ffff:0:a.b.c.d``), ISATAP (``...:5efe:a.b.c.d``) and the old
    "IPv4-compatible" form."""
    out: list[IP] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            out.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            out.append(ip.sixtofour)
        if ip.teredo is not None:
            out.extend(ip.teredo)
        value = int(ip)
        low = ipaddress.IPv4Address(value & 0xFFFFFFFF)
        if (ip in _NAT64 or ip in _NAT64_LOCAL or ip in _SIIT
                or ((value >> 32) & _ISATAP_MASK) == _ISATAP
                or (value >> 32 == 0 and value > 1)):
            out.append(low)
        if ip in _NAT64_LOCAL:
            out.extend(_rfc6052(ip))
    return out


def is_metadata(ip: IP) -> bool:
    """Whether an address is, or carries, one where a cloud machine's metadata may be."""
    if any(part in net for part in carried(ip) for net in _METADATA_NETS
           if part.version == net.version):
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        low = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        return any(low in net for net in _LOW_METADATA)
    return False


def is_loopback(ip: IP) -> bool:
    """Whether an address is this computer itself, or carries one that is."""
    return any(part.is_loopback for part in carried(ip))


def _setting(core: Core, key: str, default: Any) -> Any:
    from jimmyhq.settings import SettingError  # noqa: PLC0415

    try:
        return core.settings.get(key)
    except SettingError:  # the module that declares it is not loaded
        return default


def own_ports(core: Core) -> set[int]:
    """Every port JimmyHQ itself listens on, on any host: its main port, and the door's
    own port when the door is set to listen there (switched on or not)."""
    ports = {int(core.config.port)} - {0}
    if _setting(core, "door.listen", "main_port") == "own_port":
        ports.add(int(_setting(core, "door.port", 0)))
    return ports - {0}


def own_names(core: Core) -> set[str]:
    """Every name JimmyHQ itself is reached by: the names the owner reaches it by (with a
    tailnet name remote access added) and the door's. ``localhost`` and addresses are left
    to the rules on ports and loopback, so a service on another port of this computer, or
    of an address, may still be added."""
    from jimmyhq.lookup import safety  # noqa: PLC0415

    names = set(safety.own_names(core))
    for name in _setting(core, "door.allowed_hosts", []) or []:
        names.add(str(name).strip().lower().rstrip("."))
    public_url = str(_setting(core, "door.public_url", "") or "")
    if public_url:
        try:
            names.add((urlsplit(public_url).hostname or "").rstrip("."))
        except ValueError:
            pass
    return {n for n in names if n and n != _LOCALHOST and _literal(n) is None}


def metadata_problem(host: str, found: Collection[IP]) -> str | None:
    """Rule ``never_cloud_metadata``: by name or by any address it gives."""
    if host in _METADATA_NAMES or any(is_metadata(ip) for ip in found):
        return (f"{host} is where a cloud computer keeps its own keys and settings, which no "
                "app is ever let near.")
    return None


def own_port_problem(core: Core, port: int, found: Collection[IP] = ()) -> str | None:
    """Rule ``never_jimmyhq_itself``: not a port JimmyHQ listens on, on any host; on this
    computer, not one this process listens on now either."""
    from jimmyhq.lookup import safety  # noqa: PLC0415

    if port in own_ports(core):
        return (f"Port {port} is JimmyHQ's own, and an app is never let reach JimmyHQ itself "
                "that way.")
    if port in safety.ports_listening_here() and any(is_loopback(ip) for ip in found):
        return (f"Port {port} on this computer is one JimmyHQ's own is listening on just now, "
                "and an app is never let reach JimmyHQ itself.")
    return None


def own_name_problem(core: Core, host: str) -> str | None:
    """Rule ``never_jimmyhqs_own_name``: not a name JimmyHQ is reached by, on any port."""
    if host in own_names(core):
        return (f"{host} is a name JimmyHQ itself is reached by, and an app is never let "
                "reach JimmyHQ on any port of it.")
    return None


def loopback_problem(host: str, found: Collection[IP]) -> str | None:
    """Rule ``this_computer_only_when_typed``: an address on this computer itself only
    when the owner typed ``localhost`` or a loopback address."""
    literal = _literal(host)
    typed_here = host == _LOCALHOST or (literal is not None and is_loopback(literal))
    here = [str(ip) for ip in found if is_loopback(ip)]
    if here and not typed_here:
        return (f"{host} leads to this computer itself ({', '.join(here)}). For a service on "
                "this computer, type localhost, or 127.0.0.1, with its port.")
    return None


def public_problem(host: str, found: Collection[IP]) -> str | None:
    """Rule ``on_the_owners_own_network``: every address it gives is on this machine or
    the owner's own network."""
    from jimmyhq.lookup import safety  # noqa: PLC0415

    if any(safety.is_public(ip) for ip in found):
        return (f"{host} is on the public internet, not your own network. An app reaches a "
                "public site over https, as its install showed you, or as one of your own "
                "addresses on its Settings page.")
    return None


def moved_problem(host: str, found: Collection[IP], stored: Collection[str]) -> str | None:
    """Rule ``still_where_the_owner_saw_it``: every address a name gives now is one stored
    when the owner added or last confirmed it."""
    now = [str(ip) for ip in found]
    if not set(now) <= set(stored):
        was = ", ".join(stored) or "nothing recorded"
        return (f"{host} now points somewhere else ({', '.join(now)}) than when the owner "
                f"allowed it ({was}). The owner can confirm the new address on your app's "
                "page under Apps.")
    return None


def problems(core: Core, address: Address, found: list[IP]) -> str | None:
    """The first rule an address and what it leads to now break, or None."""
    return (metadata_problem(address.host, found)
            or own_port_problem(core, address.port, found)
            or own_name_problem(core, address.host)
            or loopback_problem(address.host, found)
            or public_problem(address.host, found))


def check_new(app_name: str, have: tuple[str, ...], address: Address) -> None:
    if address.origin in have:
        raise AddressRefused(409, f"{app_name} may reach {address.origin} already. Nothing "
                                  "was added.")


def check_room(core: Core, app_name: str, have: tuple[str, ...]) -> None:
    most = int(core.settings.get(MAX))
    if len(have) >= most:
        raise AddressRefused(409, (
            f"{app_name} may be given at most {most} address{'' if most == 1 else 'es'} on "
            "your own network, and has them. Nothing was added. Remove one first, or raise "
            "\"Addresses on your own network one app may be given\" under Settings, Apps."))


async def resolve(host: str, port: int, literal: IP | None, resolver: Resolver) -> list[IP]:
    """Every address a host gives, or the one it is, each once."""
    if literal is not None:
        return [literal]
    out: list[IP] = []
    for found in await resolver(host, port):
        try:
            ip = ipaddress.ip_address(found)
        except ValueError:
            continue
        if ip not in out:
            out.append(ip)
    return out


def _literal(host: str) -> IP | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


async def resolved_now(core: Core, address: Address, *, done: str) -> list[IP]:
    """Rule ``on_the_owners_own_network``, now: the host resolves, and every address it
    gives passes every rule. ``done`` ends each refusal ("Nothing was added.")."""
    from jimmyhq import lookup  # noqa: PLC0415

    found = await resolve(address.host, address.port, _literal(address.host),
                          lookup.service(core).resolver)
    if not found:
        raise AddressRefused(422, f"No computer called {address.host} could be found just "
                                  f"now. {done} Check the spelling, and that it is switched "
                                  "on.")
    if len(found) > _RESOLVED_MOST:
        raise AddressRefused(422, f"{address.host} leads to more than {_RESOLVED_MOST} "
                                  f"addresses, far more than one service has. {done}")
    problem = problems(core, address, found)
    if problem:
        raise AddressRefused(422, f"{problem} {done}")
    return found


def check_shown(address: Address, found: list[IP], shown: Any, *, done: str) -> list[str]:
    """Rule ``exactly_what_the_owner_saw``: the addresses the owner was shown are the ones
    the name leads to now."""
    now = sorted(str(ip) for ip in found)
    said = read_resolved(shown)
    if said is None:
        raise AddressRefused(422, "Send \"resolved\" as the list of addresses you were "
                                  f"shown for {address.host}. {done}")
    if said != now:
        raise AddressRefused(409, (
            f"{address.host} now leads to {', '.join(now)}, not what you were shown. {done} "
            "Look at it again, and agree to where it leads now if you trust it."))
    return [str(ip) for ip in found]


def read_resolved(value: Any) -> list[str] | None:
    """Addresses sent back by the owner's page, sorted, or None for anything else."""
    if not isinstance(value, list) or not 0 < len(value) <= _RESOLVED_MOST:
        return None
    out = []
    for item in value:
        ip = _literal(item) if isinstance(item, str) and len(item) <= 64 else None
        if ip is None:
            return None
        out.append(str(ip))
    return sorted(set(out))


# --- what an app holds, and the check at every request ------------------------------------


def granted(row: dict[str, Any] | None) -> tuple[str, ...]:
    """Every address the record says the owner let this app reach, oldest first."""
    given = (row or {}).get("local_addresses") or {}
    return tuple(sorted(given, key=lambda origin: (given[origin], origin)))


def stored(row: dict[str, Any] | None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """For each address the app holds, what it led to when the owner added or last
    confirmed it. An address on the record from before these were kept leads nowhere yet:
    the owner confirms it once."""
    led = (row or {}).get("local_resolved") or {}
    return tuple((origin, tuple(led.get(origin) or ())) for origin in granted(row))


def matches(local: tuple[str, ...], parsed: Parsed) -> bool:
    """Rule ``exactly_that_origin``: the request's scheme, host and port are exactly one
    the owner added. Another port, scheme or name is not."""
    return origin_of(parsed.scheme, parsed.host, parsed.port) in local


async def check_at_connect(core: Core, parsed: Parsed, resolver: Resolver,
                           led: Collection[str] = ()) -> Checked:
    """Resolve and check an added address again, now, against every rule and against
    ``led``, where it led when the owner allowed it; give the one address to connect to.
    Raises ``WebError`` ("refused") with a sentence, as web lookup's check does."""
    from jimmyhq.lookup import safety  # noqa: PLC0415
    from jimmyhq.lookup.errors import WebError  # noqa: PLC0415

    if parsed.port in own_ports(core):
        raise WebError("refused", safety.ITSELF)
    found = await resolve(parsed.host, parsed.port, parsed.literal, resolver)
    if not found:
        raise WebError("unreachable", f"No computer called {parsed.host} could be found on "
                                      "the owner's network just now.")
    address = Address(parsed.scheme, parsed.host, parsed.port)
    problem = problems(core, address, found)
    if parsed.literal is None:
        problem = problem or moved_problem(parsed.host, found, led)
    if problem:
        raise WebError("refused", problem)
    return safety.Checked(parsed.url, parsed.scheme, parsed.host, parsed.port, parsed.target,
                          str(found[0]), allowed_private=True)


# --- adding, confirming and removing --------------------------------------------------------


def _app(core: Core, app_id: Any) -> tuple[str, str, dict[str, Any]]:
    from jimmyhq.apps import bridge  # noqa: PLC0415 - it imports this module

    found = bridge.find_app(core, app_id) if isinstance(app_id, str) else None
    row = store.get(core, app_id) if found is not None else None
    if found is None or row is None or found.manifest.id != app_id:
        raise AddressRefused(404, "No app with that id is installed, so nothing was changed. "
                                  "Install it from the Apps screen first.")
    return found.manifest.id, found.manifest.name, row


def warning(app_name: str, address: Address, found: list[IP] | None) -> str:
    """The plain warning the owner reads before he adds or confirms an address, with where
    a name leads now."""
    said = WARNING.format(app=app_name, address=address.origin)
    if found and _literal(address.host) is None:
        said += LEADS.format(host=address.host, found=", ".join(str(ip) for ip in found),
                             app=app_name)
    return said


def listing(core: Core, app_id: Any) -> dict[str, Any]:
    """What the app's page shows: each address with its key, when, and where it led when
    the owner allowed it, and how many."""
    app_id, name, row = _app(core, app_id)
    given = row.get("local_addresses") or {}
    return {"max": int(core.settings.get(MAX)),
            "warning": WARNING.format(app=name, address="that address"),
            "addresses": [{"address": origin, "key": key_of(origin),
                           "added_at": given[origin], "resolved": list(led)}
                          for origin, led in stored(row)]}


def prepare(core: Core, app_id: Any, text: Any, *, new: bool = True) -> tuple[str, str, Address]:
    """Every rule that needs no network, in order."""
    app_id, name, row = _app(core, app_id)
    address = read_address(text)
    literal = [ip for ip in [_literal(address.host)] if ip]
    problem = (metadata_problem(address.host, literal)
               or own_port_problem(core, address.port, literal)
               or own_name_problem(core, address.host))
    if problem:
        raise AddressRefused(422, f"{problem} Nothing was added.")
    if new:
        have = granted(row)
        check_new(name, have, address)
        check_room(core, name, have)
    return app_id, name, address


async def check(core: Core, app_id: Any, text: Any) -> dict[str, Any]:
    """Where an address leads now, for the warning the owner reads first. Nothing is
    recorded. For one the app holds already, what it led to when allowed is given too."""
    app_id, name, address = await run_in_threadpool(prepare, core, app_id, text, new=False)
    held = dict(stored(await run_in_threadpool(store.get, core, app_id)))
    has = address.origin in held
    if not has:
        await run_in_threadpool(prepare, core, app_id, text)  # not one it has, and room
    found = await resolved_now(core, address,
                              done="Nothing was changed." if has else "Nothing was added.")
    return {"address": address.origin, "key": address.key,
            "resolved": [str(ip) for ip in found], "warning": warning(name, address, found),
            "added": has, "stored": list(held[address.origin]) if has else None}


def record_added(core: Core, app_id: str, address: Address, resolved: list[str], *,
                 actor: str) -> dict[str, Any]:
    """Put a checked address on the record, checking once more under the install lock."""
    with store.service(core).lock:
        app_id, name, row = _app(core, app_id)
        have = granted(row)
        check_new(name, have, address)
        check_room(core, name, have)
        core.tape.print("apps.local_address_added", {"app_id": app_id,
                                                     "address": address.origin,
                                                     "resolved": resolved},
                        actor=actor, subject=kinds.subject(app_id))
    return listing(core, app_id)


async def add(core: Core, app_id: Any, text: Any, shown: Any, *,
              actor: str = "owner") -> dict[str, Any]:
    """Let one app reach one address on the owner's own network, if every rule allows and
    it leads exactly where the owner was shown."""
    app_id, _, address = await run_in_threadpool(prepare, core, app_id, text)
    found = await resolved_now(core, address, done="Nothing was added.")
    resolved = check_shown(address, found, shown, done="Nothing was added.")
    return await run_in_threadpool(record_added, core, app_id, address, resolved, actor=actor)


def _held(core: Core, app_id: Any, key: Any) -> tuple[str, str, str]:
    app_id, name, row = _app(core, app_id)
    found = next((origin for origin in granted(row) if key_of(origin) == key), None)
    if found is None:
        raise AddressRefused(404, f"{name} was not let reach that address, so nothing was "
                                  "changed.")
    return app_id, name, found


async def confirm(core: Core, app_id: Any, key: Any, shown: Any, *,
                  actor: str = "owner") -> dict[str, Any]:
    """The owner agrees to where an address he added leads now: every rule is checked
    again, and the addresses he was shown are stored in place of the old ones."""
    app_id, _, origin = await run_in_threadpool(_held, core, app_id, key)
    _, _, address = await run_in_threadpool(prepare, core, app_id, origin, new=False)
    done = "Nothing was changed."
    found = await resolved_now(core, address, done=done)
    resolved = check_shown(address, found, shown, done=done)

    def record() -> dict[str, Any]:
        with store.service(core).lock:
            got_id, _, got = _held(core, app_id, key)
            core.tape.print("apps.local_address_confirmed", {
                "app_id": got_id, "address": got, "resolved": resolved},
                actor=actor, subject=kinds.subject(got_id))
        return listing(core, app_id)
    return await run_in_threadpool(record)


def remove(core: Core, app_id: Any, key: Any, *, actor: str = "owner") -> dict[str, Any]:
    """Take back one address, named by its key."""
    with store.service(core).lock:
        app_id, name, row = _app(core, app_id)
        found = next((origin for origin in granted(row) if key_of(origin) == key), None)
        if found is None:
            raise AddressRefused(404, f"{name} was not let reach that address, so nothing "
                                      "was removed.")
        core.tape.print("apps.local_address_removed", {"app_id": app_id, "address": found,
                                                       "reason": "owner"},
                        actor=actor, subject=kinds.subject(app_id))
    return listing(core, app_id)


# --- the routes -----------------------------------------------------------------------------

_NOT_OWN = ("Only JimmyHQ's own screen can change what an app may reach. Nothing was "
            "changed. Open the app's page under Apps.")
_NO_STORE = {"Cache-Control": "no-store"}
_SHAPES = {
    frozenset({"address"}): "{\"address\": \"...\"}",
    frozenset({"address", "resolved"}): "{\"address\": \"...\", \"resolved\": [...]}, the "
                                        "addresses you were shown",
    frozenset({"resolved"}): "{\"resolved\": [...]}, the addresses you were shown",
}


def _refused(error: AddressRefused) -> JSONResponse:
    return JSONResponse({"detail": error.detail}, error.status, headers=_NO_STORE)


def _body_of(raw: bytes, fields: frozenset[str]) -> dict[str, Any]:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        body = None
    if not isinstance(body, dict) or set(body) != fields:
        raise AddressRefused(422, f"Send {_SHAPES[fields]}, and nothing else. Nothing was "
                                  "changed.")
    return body


def build_router(core: Core) -> APIRouter:
    from jimmyhq.apps import bridge, secrets_entry  # noqa: PLC0415

    router = APIRouter(prefix="/api/apps", tags=["apps"])

    async def body(request: Request, fields: frozenset[str]) -> dict[str, Any]:
        try:
            raw = await secrets_entry._read_body(request, 4096)  # noqa: SLF001
        except secrets_entry.SecretRefused:
            raise AddressRefused(413, "That is far longer than an address. Nothing was "
                                      "changed.") from None
        return _body_of(raw, fields)

    @router.get("/{app_id}/local-addresses")
    def listed(app_id: str, request: Request,
               _: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            return JSONResponse(listing(core, app_id), headers=_NO_STORE)
        except AddressRefused as error:
            return _refused(error)

    @router.post("/{app_id}/local-addresses/check")
    async def checked(app_id: str, request: Request,
                      _: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            sent = await body(request, frozenset({"address"}))
            return JSONResponse(await check(core, app_id, sent["address"]),
                                headers=_NO_STORE)
        except AddressRefused as error:
            return _refused(error)

    @router.post("/{app_id}/local-addresses")
    async def added(app_id: str, request: Request,
                    who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            sent = await body(request, frozenset({"address", "resolved"}))
            return JSONResponse(await add(core, app_id, sent["address"], sent["resolved"],
                                          actor=who.actor), headers=_NO_STORE)
        except AddressRefused as error:
            return _refused(error)

    @router.post("/{app_id}/local-addresses/{key}/confirm")
    async def confirmed(app_id: str, key: str, request: Request,
                        who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            sent = await body(request, frozenset({"resolved"}))
            return JSONResponse(await confirm(core, app_id, key, sent["resolved"],
                                              actor=who.actor), headers=_NO_STORE)
        except AddressRefused as error:
            return _refused(error)

    @router.delete("/{app_id}/local-addresses/{key}")
    def removed(app_id: str, key: str, request: Request,
                who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            return JSONResponse(remove(core, app_id, key, actor=who.actor),
                                headers=_NO_STORE)
        except AddressRefused as error:
            return _refused(error)

    return router
