"""Apps over HTTP. This is the whole table; ``apps.build`` joins the three routers.

About all apps (a word here starts with an underscore, which an app's id never can, so
no app can take the place of one of these whatever it is called)::

    GET  /api/apps                    reader  what the shell draws: ids, names, screens
    GET  /api/apps/_installed         owner   every app, with what was consented to
    GET  /api/apps/_examples          owner   the example apps that ship with JimmyHQ
    POST /api/apps/_install/plan      owner   {folder} | {url, commit | ref} | {example}
    POST /api/apps/_install/confirm   owner   {plan, fingerprint, typed_name?}
    POST /api/apps/_install/cancel    owner   {plan}

About one app::

    GET  /api/apps/<id>               owner   its detail
    GET  /api/apps/<id>/described     owner   its tables and views, for JimmyHQ's own screen
    POST /api/apps/<id>/enable        owner
    POST /api/apps/<id>/disable       owner
    POST /api/apps/<id>/remove        owner   {keep_data?, erase_record?}
    POST /api/apps/<id>/update/plan   owner   {} | {folder} | {url?, commit | ref} | {example}
    POST /api/apps/<id>/update/confirm owner  {plan, fingerprint, typed_name?}  (``update``)
    POST /api/apps/<id>/update/check  owner   look for a newer version now
    POST /api/apps/<id>/rollback      owner   {version, restore_data?}
    POST /api/apps/<id>/mount         owner   {screen?}                    (``files``)
    POST /api/apps/<id>/bridge/<call> owner   {id?, call?, args}           (``bridge``)
    GET  /api/apps/<id>/secrets       owner   set or not set, never a value (``secrets_entry``)
    PUT  /api/apps/<id>/secrets/<name> owner  {value}; DELETE clears it    (``secrets_entry``)
    POST /api/apps/<id>/files         owner   a picture's bytes            (``uploads``)
    GET  /api/apps/<id>/files/<file>  owner   one of its pictures          (``uploads``)

and, with no credential at all, ``GET /appfiles/<mount>/<path>`` (``files``).

Only ``GET /api/apps`` is open to a token, and only to read. A token cannot install,
remove, switch, mount, relay a call or read what was consented to.

Installing is two calls. ``POST /_install/plan`` fetches and checks the app and answers
with what the owner must read; ``POST /_install/confirm`` carries that plan's id and
fingerprint back, and only then is anything installed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from jimmyhq.apps import install, store
from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.web.deps import owner, reader

# The first step of every address about all apps. None of them can be an app's id.
COLLECTION_WORDS = ("_installed", "_examples", "_install")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanBody(_Body):
    folder: str | None = None
    url: str | None = None
    commit: str | None = None
    ref: str | None = None
    example: str | None = None


class ConfirmBody(_Body):
    plan: str
    fingerprint: str
    typed_name: str | None = None


class CancelBody(_Body):
    plan: str


class RemoveBody(_Body):
    keep_data: bool | None = None
    erase_record: bool | None = None  # what it wrote on the Tape; with deleted data, yes


def _refused(error: store.AppError) -> HTTPException:
    return HTTPException(error.status, error.detail)


def plan_source(body: PlanBody) -> str:
    """Which one source a plan request names: "folder", "url" or "example"."""
    named = [name for name in ("folder", "url", "example") if getattr(body, name)]
    if len(named) != 1 or (named[0] != "url" and (body.commit or body.ref)):
        raise HTTPException(422, (
            "Give one of: a folder on this computer, the https address of a git "
            "repository with its commit, or the name of an example. Nothing was "
            "installed."))
    return named[0]


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/apps", tags=["apps"])

    @router.get("")
    def shell(_: Principal = Depends(reader)) -> dict[str, Any]:  # noqa: B008
        # refresh_s: how often the shell looks again, since a count changes with no event.
        return {"apps": store.shell_list(core),
                "refresh_s": core.settings.get("apps.count_refresh_s")}

    @router.get("/_installed")
    def installed(_: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return {"apps": install.listing(core)}

    @router.get("/_examples")
    def examples(_: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        have = store.installed(core)
        return {"examples": [{**e, "installed": e["id"] in have}
                             for e in install.examples()]}

    @router.post("/_install/plan")
    def plan(body: PlanBody, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        source = plan_source(body)
        try:
            if source == "folder":
                return install.plan_folder(core, body.folder or "")
            if source == "example":
                return install.plan_example(core, body.example)
            return install.plan_git(core, body.url or "", commit=body.commit, ref=body.ref)
        except store.AppError as error:
            raise _refused(error) from None

    @router.post("/_install/confirm")
    def confirm(body: ConfirmBody, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return install.confirm(core, body.plan, body.fingerprint,
                                   typed_name=body.typed_name, actor=who.actor)
        except store.AppError as error:
            raise _refused(error) from None

    @router.post("/_install/cancel")
    def cancel(body: CancelBody, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return {"cancelled": install.cancel(core, body.plan)}
        except store.AppError as error:
            raise _refused(error) from None

    @router.get("/{app_id}")
    def one(app_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return install.detail(core, app_id)
        except store.AppError as error:
            raise _refused(error) from None

    @router.get("/{app_id}/described")
    def described(app_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            store.need(core, app_id)
            m = store.manifest_of(core, app_id)
        except store.AppError as error:
            raise _refused(error) from None
        return {"id": m.id, "name": m.name, "tables": m.tables, "views": list(m.views)}

    @router.post("/{app_id}/enable")
    def enable(app_id: str, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return install.enable(core, app_id, actor=who.actor)
        except store.AppError as error:
            raise _refused(error) from None

    @router.post("/{app_id}/disable")
    def disable(app_id: str, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return install.disable(core, app_id, actor=who.actor)
        except store.AppError as error:
            raise _refused(error) from None

    @router.post("/{app_id}/remove")
    def remove(app_id: str, body: RemoveBody | None = None,
               who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return install.remove(core, app_id, keep_data=body.keep_data if body else None,
                                  erase_record=body.erase_record if body else None,
                                  actor=who.actor)
        except store.AppError as error:
            raise _refused(error) from None

    return router
