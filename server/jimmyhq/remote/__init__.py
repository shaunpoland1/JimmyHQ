"""Reaching JimmyHQ from the owner's other devices over a private network.

JimmyHQ listens on this computer only. This module reads what Tailscale says
(never changing anything on its own), tells the owner how to open JimmyHQ to
their own tailnet, and, only from this computer and only on a click, runs the one
``tailscale serve`` command that does it and adds the tailnet name to the allowed
hosts. It never runs ``tailscale funnel`` or anything else that opens JimmyHQ to the
public internet. See docs/remote.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from jimmyhq.core import Core

__all__ = ["build"]


def build(core: Core) -> APIRouter:
    from jimmyhq.remote.routes import build_router  # noqa: PLC0415 - keeps import light

    return build_router(core)
