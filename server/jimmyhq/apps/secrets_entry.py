"""Typing in an app's secret: the owner only, and write only.

    GET    /api/apps/<id>/secrets          owner   [{name, label, help, set}]
    PUT    /api/apps/<id>/secrets/<name>   owner   {"value": "..."} -> {name, set: true}
    DELETE /api/apps/<id>/secrets/<name>   owner   -> {name, set: false}

A manifest declares the secrets an app needs (``"secrets": [{name, label, help}]``); the
app's program reads one with ``secrets.get``. This is the other half: where the owner
puts one in. Only a name the manifest declares is taken, and the value goes to the core's
secret store as ``app.<id>.<name>`` and nowhere else.

**Never shown again.** No route answers with a value, its length or a hash of it: only
"set" or "not set". The value is not a pydantic field, so a malformed request is never
echoed back in a validation error, and no refusal or log line repeats any of the body.
The Tape receipt (``apps.secret_changed``) says which secret and whether it was set or
cleared.

**Who.** The owner's signed-in session, from JimmyHQ's own pages. A token is refused by
``owner``; an app's frame cannot reach ``/api/`` at all, and a request the browser marks
as coming from a frame or another site is refused as well (``bridge.from_own_pages``).
A full app's program holds an app token, which opens only ``/api/app/v1/``.

A secret kept for each host the owner gives the app (``"for_each": "owner_host"``) is
not here: it is typed in beside its host (``owner_hosts``).

**Removing the app** removes its secrets, whether its data is kept or deleted
(``install.remove``): a reinstalled app asks for them again.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from jimmyhq.apps import bridge, kinds
from jimmyhq.auth import Principal
from jimmyhq.web.deps import owner

if TYPE_CHECKING:
    from jimmyhq.apps.manifest import Manifest, Secret
    from jimmyhq.core import Core

MAX_CHARS = "apps.app_key_max_chars"
_BODY_ROOM = 1024  # what the JSON around a value may weigh


class SecretRefused(Exception):
    """A secret that was not set or cleared. ``detail`` never holds anything the owner
    typed."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- the rules, one function each -----------------------------------------------------------


def find_installed(core: Core, app_id: Any) -> Manifest:
    """The manifest of an installed app, switched on or not: the owner may type a key in
    before switching an app on."""
    app = bridge.find_app(core, app_id) if isinstance(app_id, str) else None
    if app is None or app.manifest.id != app_id:
        raise SecretRefused(404, "No app with that id is installed, so nothing was kept. "
                                 "Install it from the Apps screen first.")
    return app.manifest


def check_declared(manifest: Manifest, name: Any) -> Secret:
    """One of the secrets this app's manifest declares. Any other name is refused: an app
    never gets a secret it did not say it needs, and never another app's."""
    for secret in manifest.secrets:
        if secret.name == name and not secret.per_host:
            return secret
    listed = ", ".join(s.name for s in manifest.secrets if not s.per_host) or "none"
    raise SecretRefused(404, f"{manifest.name} does not declare a secret of that name, so "
                             f"nothing was kept. The secrets it declares are: {listed}.")


def check_value(value: Any, most: int) -> str:
    """A secret as it will be kept: text on one line, of 1 to ``most`` characters, with
    the spaces and line ending a paste brings at either end taken off. The sentences say
    nothing about what was sent."""
    if not isinstance(value, str):
        raise SecretRefused(422, "A secret is text, sent as {\"value\": \"...\"}. Nothing "
                                 "was kept. Paste the key or token as it was given to you.")
    value = value.strip()
    if not value:
        raise SecretRefused(422, "The secret is empty, so nothing was kept. Paste the key "
                                 "or token, or use Clear to remove the one kept now.")
    if len(value) > most:
        raise SecretRefused(422, f"A secret can be at most {most} characters, so nothing "
                                 "was kept. Check that only the key or token was pasted.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise SecretRefused(422, "A secret is one line of text with no control characters, "
                                 "so nothing was kept. Paste only the key or token.")
    return value


def read_value(raw: bytes, most: int) -> str:
    """The value in a request body ``{"value": "..."}``, or a refusal that repeats none
    of it."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        body = None
    if not isinstance(body, dict) or set(body) != {"value"}:
        raise SecretRefused(422, "Send the secret as {\"value\": \"...\"} and nothing "
                                 "else. Nothing was kept.")
    return check_value(body["value"], most)


def body_limit(core: Core) -> int:
    """The most bytes a request to set a secret may weigh: every character escaped."""
    return int(core.settings.get(MAX_CHARS)) * 6 + _BODY_ROOM


# --- what the owner can do ------------------------------------------------------------------


def states(core: Core, manifest: Manifest) -> list[dict[str, Any]]:
    """Each declared secret with its label and help, and whether one is kept now. Never
    the value."""
    kept = set(core.secrets.names())
    return [{"name": s.name, "label": s.label, "help": s.help,
             "set": manifest.secret_name(s.name) in kept}
            for s in manifest.secrets if not s.per_host]


def set_secret(core: Core, app_id: Any, name: Any, value: str, *,
               actor: str = "owner") -> dict[str, Any]:
    """Keep one secret for one app, in the secret store only, and say so on the Tape."""
    manifest = find_installed(core, app_id)
    secret = check_declared(manifest, name)
    value = check_value(value, int(core.settings.get(MAX_CHARS)))
    core.secrets.set(manifest.secret_name(secret.name), value)
    core.tape.print("apps.secret_changed", {"app_id": manifest.id, "name": secret.name,
                                            "action": "set"},
                    actor=actor, subject=kinds.subject(manifest.id))
    return {"name": secret.name, "set": True}


def clear_secret(core: Core, app_id: Any, name: Any, *, actor: str = "owner"
                 ) -> dict[str, Any]:
    """Remove one secret of one app. Clearing one that is not kept changes nothing and
    leaves no receipt."""
    manifest = find_installed(core, app_id)
    secret = check_declared(manifest, name)
    if core.secrets.delete(manifest.secret_name(secret.name)):
        core.tape.print("apps.secret_changed", {"app_id": manifest.id, "name": secret.name,
                                                "action": "cleared"},
                        actor=actor, subject=kinds.subject(manifest.id))
    return {"name": secret.name, "set": False}


# --- the routes -----------------------------------------------------------------------------


def _refused(error: SecretRefused) -> JSONResponse:
    return JSONResponse({"detail": error.detail}, error.status,
                        headers={"Cache-Control": "no-store"})


_NOT_OWN = ("Only JimmyHQ's own screen can change an app's secrets. Nothing was kept. "
            "Open the app's page under Settings, Apps.")
_FAULT = ("JimmyHQ could not keep the secret because of a fault of its own. Nothing is "
          "known to have changed. Try again; if it keeps happening, look at the server's "
          "log.")


async def _read_body(request: Request, most: int) -> bytes:
    declared = request.headers.get("content-length", "")
    too_large = SecretRefused(413, "That is far longer than a secret can be, so nothing "
                                   "was kept. Paste only the key or token.")
    if declared.isdigit() and int(declared) > most:
        raise too_large
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > most:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/apps", tags=["apps"])

    @router.get("/{app_id}/secrets")
    def listed(app_id: str, request: Request,
               _: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            manifest = find_installed(core, app_id)
        except SecretRefused as error:
            return _refused(error)
        return JSONResponse({"secrets": states(core, manifest)},
                            headers={"Cache-Control": "no-store"})

    @router.put("/{app_id}/secrets/{name}")
    async def put(app_id: str, name: str, request: Request,
                  who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            # Which app and which name first, so a body is never read for a refusal.
            manifest = await run_in_threadpool(find_installed, core, app_id)
            check_declared(manifest, name)
            raw = await _read_body(request, await run_in_threadpool(body_limit, core))
            value = read_value(raw, int(core.settings.get(MAX_CHARS)))
            done = await run_in_threadpool(set_secret, core, app_id, name, value,
                                           actor=who.actor)
        except SecretRefused as error:
            return _refused(error)
        except OSError:
            return JSONResponse({"detail": _FAULT}, 500)
        return JSONResponse(done, headers={"Cache-Control": "no-store"})

    @router.delete("/{app_id}/secrets/{name}")
    def delete(app_id: str, name: str, request: Request,
               who: Principal = Depends(owner)):  # noqa: B008
        if not bridge.from_own_pages(request):
            return JSONResponse({"detail": _NOT_OWN}, 403)
        try:
            done = clear_secret(core, app_id, name, actor=who.actor)
        except SecretRefused as error:
            return _refused(error)
        except OSError:
            return JSONResponse({"detail": _FAULT}, 500)
        return JSONResponse(done, headers={"Cache-Control": "no-store"})

    return router
