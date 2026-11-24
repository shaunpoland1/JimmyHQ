"""The wall module. See jimmyhq/modules.py for how a module plugs in."""

from __future__ import annotations

from fastapi import APIRouter

from jimmyhq.core import Core


def build(core: Core) -> APIRouter:
    router = APIRouter(prefix="/api/wall", tags=["wall"])
    return router
