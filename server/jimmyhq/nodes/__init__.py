"""Nodes: a second machine joins this install to lend it models, while the agents
and every store stay here on the hub. See docs/nodes.md.

Stage 1. A node offers model servers (and, when the browser package lands,
browsers). It holds one node token, which opens the node link and nothing else.
An install that never makes a join code is unchanged: the link does not answer,
and nothing here runs.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import APIRouter

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.nodes import protocol
from jimmyhq.nodes.service import RELAY_SECRET, Hub

__all__ = ["Admitted", "Hub", "admit", "build", "hub"]

PREFIX = "/api/nodes/"
_GUARDED = ("join", "link", "relay")


def hub(core: Core) -> Hub:
    return core.service("nodes", Hub)


def build(core: Core) -> APIRouter:
    from jimmyhq.nodes.routes import build_router  # noqa: PLC0415 - keeps import light

    return build_router(core, hub(core))


@dataclass(frozen=True)
class Admitted:
    """What the gate decided for a request on the node link."""

    principal: Principal | None = None  # who it is, when a credential was needed and good
    node: tuple[str, str] | None = None  # (node id, name) for a node's own requests
    refusal: tuple[int, str] | None = None
    open: bool = False  # needs no credential: the join, whose code is the credential


def _bearer(authorization: str | None) -> str | None:
    value = authorization or ""
    return value[7:].strip() if value.lower().startswith("bearer ") else None


def admit(core: Core, path: str, authorization: str | None, client: str) -> Admitted | None:
    """Decide who may pass on the node link. None means the path is not part of
    the link (the owner's own node routes), and the ordinary rules apply.

    The link has its own credentials and takes no other: a node's token on
    ``/api/nodes/link``, this install's relay key on ``/api/nodes/relay``. An
    owner session, an API token and a door token are never looked at here, and
    a node token is never looked at anywhere else.
    """
    if not path.startswith(PREFIX):
        return None
    part = path[len(PREFIX):].split("/", 1)[0]
    if part not in _GUARDED:
        return None
    not_joined = Admitted(refusal=(
        401, "This machine is not joined to this hub, or was removed from it. Nothing was "
             "done. Join again with a new code from Settings, Nodes on the hub."))
    if not core.auth.nodes_in_use():
        # No machine has joined and no join code is waiting: there is no link. A
        # node that was the last to be removed is still told so, so that it stops.
        return not_joined if part == "link" else Admitted(
            refusal=(404, "There is nothing at this address."))
    if part == "join":
        return Admitted(open=True)
    token = _bearer(authorization)
    if part == "relay":
        key = core.secrets.get(RELAY_SECRET)
        if key and token and hmac.compare_digest(key.encode(), token.encode()):
            return Admitted(principal=Principal("relay", frozenset({"relay"})))
        return Admitted(refusal=(401, "Only this machine's own processes can use the relay."))
    found = core.auth.check_node_token(token)
    if found is None:
        # Only a token that is not known is held back, so guessing from one
        # address can never lock a joined node out.
        if core.auth.node_throttled(client):
            return Admitted(refusal=(429, "Too many requests with a token that is not "
                                          "known. Nothing was done. Try again in 15 minutes."))
        core.auth.note_node_failure(client)
        return not_joined
    return Admitted(principal=Principal(f"node:{found[1]}", frozenset({"node"})), node=found)


assert protocol.LINK.startswith(PREFIX)  # noqa: S101 - the gate and the routes must agree
