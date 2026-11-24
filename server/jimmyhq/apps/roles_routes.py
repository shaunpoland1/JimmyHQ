"""Roles over HTTP: the owner fills an app's roles, at install and later on its page.

    GET /api/apps/_install/roles/<plan>  owner  the roles a waiting install would like
                                             filled, each charter's text, the agents
    GET /api/apps/<id>/roles          owner  each role, who holds it, the agents, warnings
    PUT /api/apps/<id>/roles          owner  {choices: {<role>: {mode: new, group?} |
                                              {mode: existing, agent_id} | {mode: empty}}}

The install dialog shows the plan's roles before the owner agrees, and sends the choices
once the app is installed: filling a role is never part of what installing is consent to.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from jimmyhq.apps import install, roles
from jimmyhq.apps.store import AppError
from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.web.deps import owner


class Choices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: dict[str, dict[str, Any]]


def build_router(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/apps", tags=["apps"])

    def refused(error: roles.RoleRefused | AppError) -> HTTPException:
        if isinstance(error, AppError):
            return HTTPException(error.status, error.detail)
        return HTTPException(error.status, error.sentence)

    @router.get("/_install/roles/{plan_id}")
    def plan_roles(plan_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            found = install.planned_roles(core, plan_id)
        except AppError as error:
            raise refused(error) from None
        return {"roles": found, "agents": roles.agents_view(roles._village(core))}  # noqa: SLF001

    @router.get("/{app_id}/roles")
    def app_roles(app_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return roles.view(core, app_id)
        except roles.RoleRefused as error:
            raise refused(error) from None

    @router.put("/{app_id}/roles")
    def choose(app_id: str, body: Choices,
               who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        try:
            return roles.apply_choices(core, app_id, body.choices, actor=who.actor)
        except roles.RoleRefused as error:
            raise refused(error) from None

    return router
