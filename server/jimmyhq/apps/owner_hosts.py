"""Hosts the owner grants an app himself, after install.

An app may reach only the hosts the owner agreed to. Most name them in their manifest
(``net:<host>``), but an app that publishes to the owner's own site cannot: only the owner
knows its address. Such an app declares, for one stated purpose and up to a small number,
that it may ask for hosts of his choosing::

    "owner_hosts": {"purpose": "Publish drafts to your own site.", "max": 3}

The install dialog shows that as its own sentence (``permissions.owner_hosts_line``) and
the owner's consent covers it (``permissions.fingerprint``). Then, on the app's own page
under Settings, Apps, the owner adds and removes hosts:

    GET    /api/apps/<id>/hosts                          owner  what it may ask, what it has
    POST   /api/apps/<id>/hosts            {host}        owner  add one
    DELETE /api/apps/<id>/hosts/<host>                   owner  take one away
    PUT    /api/apps/<id>/hosts/<host>/secrets/<name>    owner  {value}: its secret there
    DELETE /api/apps/<id>/hosts/<host>/secrets/<name>    owner  clear it

**The rules for a host**, each a function below, checked when it is added: the app
declares ``owner_hosts`` (``check_declares``); the owner typed https, or a bare name,
and one exact host with no wildcard, address, port, path or sign-in part
(``read_host``), a public name by the manifest's own rule (``permissions.check_host``:
no ``localhost``, ``.local``, ``ts.net`` and the like); not a name JimmyHQ itself is
reached by (``check_not_own``); not one it has already (``check_new``); room under the
app's ``max`` (``check_room``); and the name resolves now, every address it gives
public (``check_public``, by web lookup's own address rule in ``lookup.safety``, with no
exception the owner made for web lookup). ``net.fetch`` resolves and checks it again on
every request (``bridge.check_owner_host_public``), so a name that later points at a
private address reaches nothing.

**What it gives.** An owner-granted host is treated by ``net.fetch`` exactly like a host
the manifest names, and nothing more. The app lists its hosts with ``hosts.list``; no call
lets it add, change or remove one.

**Kept on the record and in step.** Each change is ``apps.host_added`` or
``apps.host_removed``, folded by the ``apps`` lens into the app's row, which every process
reads afresh on every call (``bridge.find_app``), so a host removed in one process is
gone in every other at its next call. Removing the app drops them with its row (and its
secrets). An update or a rollback to a version that no longer declares ``owner_hosts``,
or allows fewer, removes the rest (``drop_undeclared``); and whatever the record says,
only a version that declares the key is given any (``granted``).

**Secrets per host.** A manifest secret written ``"for_each": "owner_host"`` is kept once
for each granted host, as ``app.<id>.<name>#<host>``, typed in beside that host's
address, write only. The program reads one with ``secrets.get {name, host}``, for a host
it holds now; removing the host clears its secrets.
"""

from __future__ import annotations

import ipaddress
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from jimmyhq.apps import kinds, store
from jimmyhq.apps import permissions as perms
from jimmyhq.apps.permissions import PermissionRefused
from jimmyhq.apps.store import AppError
from jimmyhq.auth import Principal
from jimmyhq.web.deps import owner

if TYPE_CHECKING:
    from jimmyhq.apps.manifest import Manifest, OwnerHosts, Secret
    from jimmyhq.core import Core

_EXAMPLE = "https://blog.example.com"
_HOST_MOST = 300  # what the owner may type: an address, not a page


class HostRefused(Exception):
    """A host that was not added or removed. ``detail`` says so and what to do."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- the rules, one function each -----------------------------------------------------------


def check_declares(manifest: Manifest) -> OwnerHosts:
    """Rule ``only_an_app_that_declares_owner_hosts``: an app that did not say, at
    install, that it may ask for hosts of the owner's choosing is given none."""
    if manifest.owner_hosts is None:
        raise HostRefused(409, (
            f"{manifest.name} did not ask, when you installed it, to be given addresses of "
            "your own, so it cannot be given one. Nothing was added. It reaches only the "
            "hosts its install showed you."))
    return manifest.owner_hosts


def read_host(text: Any) -> str:
    """Rule ``one_exact_public_host_over_https``: what the owner typed, as one exact host
    name. A bare name or an https address with nothing after the host. Refused: another
    scheme, a sign-in part, a port, a path, a wildcard, an address written as numbers, and
    any name that only means something on one network (the manifest's own rule)."""
    if not isinstance(text, str) or not text.strip():
        raise HostRefused(422, f"Type your site's address, such as {_EXAMPLE}. Nothing was "
                               "added.")
    text = text.strip()
    if len(text) > _HOST_MOST or any(ord(ch) <= 32 or ord(ch) == 127 for ch in text) \
            or "\\" in text:
        raise HostRefused(422, f"Type only your site's address, such as {_EXAMPLE}, on one "
                               "line. Nothing was added.")
    if "*" in text:
        raise HostRefused(422, "A wildcard cannot be given: type one exact address, such as "
                               f"{_EXAMPLE}. Nothing was added. Add each site on its own.")
    if "://" in text:
        try:
            parts = urlsplit(text)
            port = parts.port
        except ValueError:
            raise HostRefused(422, f"That is not an address that can be read. Type one such "
                                   f"as {_EXAMPLE}. Nothing was added.") from None
        if parts.scheme.lower() != "https":
            raise HostRefused(422, "Only an https address can be given, so an app's requests "
                                   f"are always encrypted. Nothing was added. Type it as "
                                   f"{_EXAMPLE}.")
        if parts.username is not None or "@" in parts.netloc or port is not None \
                or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise HostRefused(422, "Type the site's address with nothing after its name: no "
                                   f"sign-in part, port or page, such as {_EXAMPLE}. Nothing "
                                   "was added.")
        host = parts.hostname or ""
    else:
        host = text.rstrip("/")
    host = host.lower().strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HostRefused(422, "An address written as numbers cannot be given: type the "
                               f"site's name, such as {_EXAMPLE}. Nothing was added.")
    try:
        return perms.check_host(host)
    except PermissionRefused:
        raise HostRefused(422, (
            "That is not one exact public site name, so it cannot be given to an app. "
            "Nothing was added. Names that only work on your own network, such as "
            f"localhost or .local, are never given. Type it as {_EXAMPLE}.")) from None


def check_not_own(own: set[str], host: str) -> None:
    """Rule ``never_jimmyhq_itself``: no name the owner reaches JimmyHQ by."""
    if host in own:
        raise HostRefused(422, f"{host} is where JimmyHQ itself is reached, and an app is "
                               "never given that. Nothing was added.")


def check_new(manifest: Manifest, have: tuple[str, ...], host: str) -> None:
    """Rule ``not_twice``: not a host it has already, from its manifest or from you."""
    if host in have:
        raise HostRefused(409, f"{manifest.name} has {host} already. Nothing was added.")
    if host in manifest.hosts:
        raise HostRefused(409, f"{manifest.name} reaches {host} already: you agreed to it "
                               "when you installed it. Nothing was added.")


def check_room(manifest: Manifest, have: tuple[str, ...]) -> None:
    """Rule ``at_most_its_max``: no more than the number its install showed."""
    asked = check_declares(manifest)
    if len(have) >= asked.max:
        raise HostRefused(409, (
            f"{manifest.name} may be given at most {asked.max} address"
            f"{'' if asked.max == 1 else 'es'} of yours, and has "
            f"{'it' if asked.max == 1 else 'them all'}. Nothing was added. Remove one "
            "first."))


async def check_public(core: Core, host: str) -> None:
    """Rule ``resolves_to_public_addresses_now``: the name resolves, and every address it
    gives is public, by web lookup's rule (``lookup.safety.check``). An exception the
    owner made for web lookup (``lookup.allow_private_hosts``) does not count here."""
    from jimmyhq import lookup  # noqa: PLC0415
    from jimmyhq.lookup import safety  # noqa: PLC0415
    from jimmyhq.lookup.errors import WebError  # noqa: PLC0415

    private = HostRefused(422, (
        f"{host} leads to JimmyHQ itself or to an address on your own machine or private "
        "network, which an app is never given. Nothing was added. Give the public address "
        "of your site."))
    try:
        checked = await safety.check(core, f"https://{host}/", lookup.service(core).resolver)
    except WebError as error:
        if error.kind == "refused":
            raise private from None
        raise HostRefused(422, f"No site called {host} could be found just now. Nothing was "
                               "added. Check the spelling, and that the site is up.") from None
    if checked.allowed_private:
        raise private


# --- what an app holds ----------------------------------------------------------------------


def recorded(row: dict[str, Any] | None) -> tuple[str, ...]:
    """Every host the record says the owner gave this app, oldest first."""
    given = (row or {}).get("owner_hosts") or {}
    return tuple(sorted(given, key=lambda host: (given[host], host)))


def granted(manifest: Manifest, row: dict[str, Any] | None) -> tuple[str, ...]:
    """Rule ``only_while_declared``: the hosts the app may reach because the owner gave
    them: none for a version that does not declare ``owner_hosts``, and never more than
    its ``max`` (the oldest kept)."""
    if manifest.owner_hosts is None:
        return ()
    return recorded(row)[:manifest.owner_hosts.max]


def _app(core: Core, app_id: Any) -> tuple[Manifest, dict[str, Any]]:
    from jimmyhq.apps import bridge  # noqa: PLC0415 - it imports this module

    found = bridge.find_app(core, app_id) if isinstance(app_id, str) else None
    row = store.get(core, app_id) if found is not None else None
    if found is None or row is None or found.manifest.id != app_id:
        raise HostRefused(404, "No app with that id is installed, so nothing was changed. "
                               "Install it from the Apps screen first.")
    return found.manifest, row


def _secret_names(manifest: Manifest) -> list[Secret]:
    return [s for s in manifest.secrets if s.per_host]


def listing(core: Core, app_id: Any) -> dict[str, Any]:
    """What the app's page under Settings shows: whether it may ask, why and how many,
    each host it has with when, and for each the secrets kept there (set or not set)."""
    manifest, row = _app(core, app_id)
    asked = manifest.owner_hosts
    kept = set(core.secrets.names())
    given = row.get("owner_hosts") or {}
    return {
        "declared": None if asked is None else {
            "purpose": asked.purpose, "max": asked.max, "next": asked.next},
        "hosts": [{"host": host, "added_at": given[host], "secrets": [
            {"name": s.name, "label": s.label, "help": s.help,
             "set": manifest.secret_name(s.name, host) in kept}
            for s in _secret_names(manifest)]} for host in granted(manifest, row)],
    }


def prepare(core: Core, app_id: Any, text: Any) -> tuple[Manifest, str]:
    """Every rule that needs no network, in order. Returns the app and the host."""
    from jimmyhq.lookup import safety  # noqa: PLC0415

    manifest, row = _app(core, app_id)
    check_declares(manifest)
    host = read_host(text)
    check_not_own(safety.own_names(core), host)
    have = granted(manifest, row)
    check_new(manifest, have, host)
    check_room(manifest, have)
    return manifest, host


def record_added(core: Core, app_id: str, host: str, *, actor: str) -> dict[str, Any]:
    """Put a checked host on the record, checking once more under the install lock that
    nothing changed meanwhile (another tab, another process)."""
    with store.service(core).lock:
        manifest, row = _app(core, app_id)
        check_declares(manifest)
        have = granted(manifest, row)
        check_new(manifest, have, host)
        check_room(manifest, have)
        core.tape.print("apps.host_added", {"app_id": app_id, "host": host}, actor=actor,
                        subject=kinds.subject(app_id))
    return listing(core, app_id)


async def add(core: Core, app_id: Any, text: Any, *, actor: str = "owner") -> dict[str, Any]:
    """Give an app one host of the owner's choosing, if every rule allows it."""
    manifest, host = await run_in_threadpool(prepare, core, app_id, text)
    await check_public(core, host)
    return await run_in_threadpool(record_added, core, manifest.id, host, actor=actor)


def _forget_secrets(core: Core, app_id: str, host: str) -> None:
    """Clear every secret kept for one host of one app."""
    end = f"#{host}"
    for name in core.secrets.names():
        if name.startswith(f"app.{app_id}.") and name.endswith(end):
            core.secrets.delete(name)


def remove(core: Core, app_id: Any, host: Any, *, actor: str = "owner",
           reason: str = "owner") -> dict[str, Any]:
    """Take a host away from an app, with the secrets kept for it."""
    with store.service(core).lock:
        manifest, row = _app(core, app_id)
        if not isinstance(host, str) or host not in recorded(row):
            raise HostRefused(404, f"{manifest.name} was not given that address, so nothing "
                                   "was removed.")
        _forget_secrets(core, manifest.id, host)
        core.tape.print("apps.host_removed", {"app_id": manifest.id, "host": host,
                                              "reason": reason},
                        actor=actor, subject=kinds.subject(manifest.id))
    return listing(core, manifest.id)


def drop_undeclared(core: Core, app_id: str, *, actor: str = "owner") -> list[str]:
    """After an update or a rollback: remove each host the version now in use no longer
    allows (none when it does not declare ``owner_hosts``, the newest beyond its
    ``max``). Returns the hosts removed."""
    try:
        manifest = store.manifest_of(core, app_id)
    except AppError:
        return []
    row = store.get(core, app_id)
    keep = set(granted(manifest, row))
    gone = [host for host in recorded(row) if host not in keep]
    for host in gone:
        remove(core, app_id, host, actor=actor, reason="no_longer_declared")
    return gone


# --- secrets kept for one host --------------------------------------------------------------


def check_host_secret(manifest: Manifest, row: dict[str, Any], host: Any,
                      name: Any) -> Secret:
    """Rule ``a_host_secret_is_for_a_granted_host``: a secret the manifest keeps for each
    owner host, for a host the app holds now."""
    secret = manifest.secret(name) if isinstance(name, str) else None
    if secret is None or not secret.per_host:
        listed = ", ".join(s.name for s in _secret_names(manifest)) or "none"
        raise HostRefused(404, f"{manifest.name} keeps no secret of that name for each of "
                               f"your addresses, so nothing was kept. Those it keeps are: "
                               f"{listed}.")
    if not isinstance(host, str) or host not in granted(manifest, row):
        raise HostRefused(404, f"{manifest.name} was not given that address, so nothing was "
                               "kept. Add the address first.")
    return secret


def set_host_secret(core: Core, app_id: Any, host: Any, name: Any, value: str, *,
                    actor: str = "owner") -> dict[str, Any]:
    from jimmyhq.apps import secrets_entry  # noqa: PLC0415

    with store.service(core).lock:
        manifest, row = _app(core, app_id)
        secret = check_host_secret(manifest, row, host, name)
        try:
            value = secrets_entry.check_value(value,
                                              int(core.settings.get(secrets_entry.MAX_CHARS)))
        except secrets_entry.SecretRefused as error:
            raise HostRefused(error.status, error.detail) from None
        core.secrets.set(manifest.secret_name(secret.name, host), value)
        core.tape.print("apps.host_secret_changed", {
            "app_id": manifest.id, "host": host, "name": secret.name, "action": "set"},
            actor=actor, subject=kinds.subject(manifest.id))
    return {"host": host, "name": secret.name, "set": True}


def clear_host_secret(core: Core, app_id: Any, host: Any, name: Any, *,
                      actor: str = "owner") -> dict[str, Any]:
    with store.service(core).lock:
        manifest, row = _app(core, app_id)
        secret = check_host_secret(manifest, row, host, name)
        if core.secrets.delete(manifest.secret_name(secret.name, host)):
            core.tape.print("apps.host_secret_changed", {
                "app_id": manifest.id, "host": host, "name": secret.name,
                "action": "cleared"}, actor=actor, subject=kinds.subject(manifest.id))
    return {"host": host, "name": secret.name, "set": False}


# --- the routes -----------------------------------------------------------------------------


_NOT_OWN = ("Only JimmyHQ's own screen can change what an app may reach. Nothing was "
            "changed. Open the app's page under Settings, Apps.")
_NO_STORE = {"Cache-Control": "no-store"}


def _refused(error: HostRefused | AppError) -> JSONResponse:
    return JSONResponse({"detail": error.detail}, error.status, headers=_NO_STORE)


def _host_of(raw: bytes) -> Any:
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        body = None
    if not isinstance(body, dict) or set(body) != {"host"}:
        raise HostRefused(422, "Send the address as {\"host\": \"...\"} and nothing else. "
                               "Nothing was added.")
    return body["host"]


def build_router(core: Core) -> APIRouter:
    from jimmyhq.apps import bridge, secrets_entry  # noqa: PLC0415

    router = APIRouter(prefix="/api/apps", tags=["apps"])

    @router.get("/{app_id}/hosts")
    def listed(app_id: str, request: Request,
               _: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            return JSONResponse(listing(core, app_id), headers=_NO_STORE)
        except HostRefused as error:
            return _refused(error)

    @router.post("/{app_id}/hosts")
    async def added(app_id: str, request: Request,
                    who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            raw = await secrets_entry._read_body(request, 4096)  # noqa: SLF001
            return JSONResponse(await add(core, app_id, _host_of(raw), actor=who.actor),
                                headers=_NO_STORE)
        except secrets_entry.SecretRefused:
            return _refused(HostRefused(413, "That is far longer than an address. Nothing "
                                             "was added."))
        except HostRefused as error:
            return _refused(error)

    @router.delete("/{app_id}/hosts/{host}")
    def removed(app_id: str, host: str, request: Request,
                who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            return JSONResponse(remove(core, app_id, host, actor=who.actor),
                                headers=_NO_STORE)
        except HostRefused as error:
            return _refused(error)

    @router.put("/{app_id}/hosts/{host}/secrets/{name}")
    async def put_secret(app_id: str, host: str, name: str, request: Request,
                         who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            # Which app, host and name first, so a body is never read for a refusal.
            manifest, row = await run_in_threadpool(_app, core, app_id)
            check_host_secret(manifest, row, host, name)
            raw = await secrets_entry._read_body(  # noqa: SLF001
                request, await run_in_threadpool(secrets_entry.body_limit, core))
            value = secrets_entry.read_value(raw, int(core.settings.get(
                secrets_entry.MAX_CHARS)))
            done = await run_in_threadpool(set_host_secret, core, app_id, host, name, value,
                                           actor=who.actor)
        except secrets_entry.SecretRefused as error:
            return _refused(HostRefused(error.status, error.detail))
        except HostRefused as error:
            return _refused(error)
        return JSONResponse(done, headers=_NO_STORE)

    @router.delete("/{app_id}/hosts/{host}/secrets/{name}")
    def delete_secret(app_id: str, host: str, name: str, request: Request,
                      who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            return JSONResponse(clear_host_secret(core, app_id, host, name,
                                                  actor=who.actor), headers=_NO_STORE)
        except HostRefused as error:
            return _refused(error)

    return router
