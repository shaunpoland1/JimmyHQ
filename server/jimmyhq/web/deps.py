"""Shared request dependencies for every module's routes.

The gate middleware in ``web.app`` has already identified who is asking and
put a ``Principal`` on the request. These turn that into the three access
levels a route can ask for.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from jimmyhq.auth import Principal
from jimmyhq.core import Core


def core_of(request: Request) -> Core:
    return request.app.state.core


def principal(request: Request) -> Principal:
    return request.state.principal


def reader(who: Principal = Depends(principal)) -> Principal:  # noqa: B008
    """The owner, or a token with the ``read`` scope."""
    if not who.can("read"):
        raise HTTPException(403, "This token cannot read.")
    return who


def writer(who: Principal = Depends(principal)) -> Principal:  # noqa: B008
    """The owner, or a token with the ``write`` scope."""
    if not who.can("write"):
        raise HTTPException(403, "This token cannot make changes.")
    return who


def owner(who: Principal = Depends(principal)) -> Principal:  # noqa: B008
    """Only the signed-in owner: settings, credentials and decisions."""
    if not who.is_owner:
        raise HTTPException(403, "Only the signed-in owner can do this.")
    return who


def node(request: Request, who: Principal = Depends(principal)) -> tuple[str, str]:  # noqa: B008
    """A joined machine on the node link: (node id, name). The gate lets a node's
    token through on ``/api/nodes/link`` only, so this guards nothing else."""
    found = getattr(request.state, "node", None)
    if "node" not in who.scopes or found is None:
        raise HTTPException(403, "Only a joined node can do this.")
    return found


def relay(who: Principal = Depends(principal)) -> Principal:  # noqa: B008
    """One of this install's own processes, calling a model on a node."""
    if who.actor != "relay":
        raise HTTPException(403, "Only this machine's own processes can use the relay.")
    return who
