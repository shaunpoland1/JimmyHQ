"""Remote access over HTTP, under ``/api/remote``. Who may do what:

- reading the status, and adding or removing the detected name in the allowed
  hosts: the signed-in owner only (a token cannot). Adding it is refused while Funnel
  makes the address public;
- turning the Tailscale mapping on or off: the signed-in owner, and only when the
  request itself comes from this computer, since it changes what this computer
  opens to a network.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.remote import tailscale as ts
from jimmyhq.web.app import is_local_request
from jimmyhq.web.deps import owner

KEY = "access.allowed_hosts"
AT_THE_MACHINE = (
    "This changes what this computer opens to your network, so it can only be done from "
    "this computer itself. Open JimmyHQ there, at http://127.0.0.1 or http://localhost, "
    "and do it from Settings, Access. Or run the command yourself on this computer.")


def not_public(core: Core, found: ts.Detection) -> None:
    """Refuse to let JimmyHQ answer to an address Funnel has opened to the internet:
    adding its name would let every browser on the internet reach the sign-in page."""
    mapped = found.public_entry(core.config.port) if core.config.port else None
    if mapped is not None and not core.config.allow_public:
        raise HTTPException(
            409, "Tailscale Funnel makes this address public, so anyone on the internet "
                 "could reach it. Nothing was changed. On this computer, turn Funnel off "
                 f"with `tailscale funnel --https={mapped.https_port} off`, then try again.")


def _hosts(core: Core) -> list[str]:
    return [str(h).strip().lower() for h in core.settings.get(KEY)]


def is_allowed(core: Core, name: str | None) -> bool:
    if not name:
        return False
    return name in _hosts(core) or name in core.config.allowed_hosts


def add_host(core: Core, name: str, *, actor: str) -> bool:
    """Put the name in the allowed hosts, once. False if it was already allowed."""
    if is_allowed(core, name):
        return False
    core.settings.set(KEY, [*core.settings.get(KEY), name], actor=actor)
    return True


def remove_host(core: Core, name: str, *, actor: str) -> bool:
    kept = [h for h in core.settings.get(KEY) if str(h).strip().lower() != name]
    if len(kept) == len(core.settings.get(KEY)):
        return False
    core.settings.set(KEY, kept, actor=actor)
    return True


def commands(core: Core, found: ts.Detection) -> dict[str, Any]:
    """The commands the owner could type, built from the real port and name."""
    port = core.config.port
    out: dict[str, Any] = {
        "on": ts.shown(ts.serve_args(port)) if port else None,
        "status": "tailscale serve status",
        "allowed_hosts_value": found.name,
    }
    mapped = found.entry(port)
    out["off"] = ts.shown(ts.off_args(mapped.https_port if mapped else port)) if port else None
    return out


def status(core: Core, request: Request, found: ts.Detection | None = None) -> dict[str, Any]:
    found = found or ts.detect(core)
    port = core.config.port
    mapped = found.entry(port) if found.state == "ready" else None
    clash = found.clash(port) if found.state == "ready" and port else None
    problem = ts.PROBLEMS.get(found.state)
    if found.state == "ready" and not found.name:
        problem = ts.NO_NAME
    return {
        "state": found.state,  # not_installed, not_running, needs_login, stopped, ready, unknown
        "problem": problem,  # a sentence that says what to do, or None
        "path_missing": found.setting_missing,
        "backend_state": found.backend_state,
        "name": found.name,
        "https_enabled": found.https_enabled,
        "port": port,
        "serve_known": found.serve_known,
        "mapped": mapped is not None,
        "address": ts.address(found.name, mapped.https_port) if mapped and found.name else None,
        # Funnel is on for one of its addresses: open to the internet
        "public": bool(mapped) and found.public_entry(port) is not None,
        "clash": clash is not None,  # something else is served on the port we would use
        "allowed": is_allowed(core, found.name),
        "from_this_computer": is_local_request(request),  # not from another device
        "commands": commands(core, found),
    }


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/remote", tags=["remote"])

    def local_only(request: Request) -> None:
        if not is_local_request(request):
            raise HTTPException(403, AT_THE_MACHINE)

    def ready(found: ts.Detection) -> str:
        """This computer's name on the tailnet, or a refusal that says what to do."""
        if found.state != "ready":
            raise HTTPException(409, ts.PROBLEMS[found.state])
        if not found.name or found.path is None:
            raise HTTPException(409, ts.NO_NAME)
        return found.name

    @router.get("/status")
    async def get_status(request: Request, _: Principal = Depends(owner)):  # noqa: B008
        return await run_in_threadpool(status, core, request)

    @router.post("/tailscale/serve")
    async def turn_on(request: Request, who: Principal = Depends(owner)):  # noqa: B008
        local_only(request)
        port = core.config.port
        if not port:
            raise HTTPException(409, "JimmyHQ does not know which port it is listening on. "
                                     "Restart it with a fixed port and try again.")

        def work() -> dict[str, Any]:
            found = ts.detect(core)
            name = ready(found)
            not_public(core, found)
            if found.clash(port):
                raise HTTPException(
                    409, f"Your tailnet address on port {port} already serves something else. "
                         "JimmyHQ will not replace it. Turn that off yourself first "
                         "(\"tailscale serve status\" shows what it is), then try again.")
            args = ts.serve_args(port)
            ran_text = None
            if found.entry(port) is None:  # already mapped: nothing to run again
                ran = ts.service(core).runner([found.path or "", *args], ts.CHANGE_TIMEOUT_S)
                if ran.code != 0:
                    raise HTTPException(502, ts.failure(ran, "turn it on"))
                ran_text = ts.shown(args)
                core.tape.print("remote.serve_changed", {"on": True, "port": port},
                                actor=who.actor)
            added = add_host(core, name, actor=who.actor)
            again = ts.detect(core)
            return {"ran": ran_text, "allowed_added": added,
                    "status": status(core, request, again)}

        return await run_in_threadpool(work)

    @router.delete("/tailscale/serve")
    async def turn_off(
        request: Request, remove_host_too: bool = Query(False, alias="remove_host"),
        who: Principal = Depends(owner),  # noqa: B008
    ):
        local_only(request)

        def work() -> dict[str, Any]:
            found = ts.detect(core)
            name = ready(found)
            mapped = found.entry(core.config.port)
            ran_text = None
            if mapped is not None:
                args = ts.off_args(mapped.https_port)
                ran = ts.service(core).runner([found.path or "", *args], ts.CHANGE_TIMEOUT_S)
                if ran.code != 0:
                    raise HTTPException(502, ts.failure(ran, "turn it off"))
                ran_text = ts.shown(args)
                core.tape.print("remote.serve_changed",
                                {"on": False, "port": core.config.port}, actor=who.actor)
            removed = remove_host(core, name, actor=who.actor) if remove_host_too else False
            return {"ran": ran_text, "host_removed": removed,
                    "status": status(core, request, ts.detect(core))}

        return await run_in_threadpool(work)

    @router.post("/allow-host")
    async def allow_host(request: Request, who: Principal = Depends(owner)):  # noqa: B008
        def work() -> dict[str, Any]:
            found = ts.detect(core)
            name = ready(found)
            not_public(core, found)
            return {"added": add_host(core, name, actor=who.actor),
                    "status": status(core, request, found)}

        return await run_in_threadpool(work)

    @router.delete("/allow-host")
    async def forget_host(request: Request, who: Principal = Depends(owner)):  # noqa: B008
        def work() -> dict[str, Any]:
            found = ts.detect(core)
            name = ready(found)
            return {"removed": remove_host(core, name, actor=who.actor),
                    "status": status(core, request, found)}

        return await run_in_threadpool(work)

    return router
