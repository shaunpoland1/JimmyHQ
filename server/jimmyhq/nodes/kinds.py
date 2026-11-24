"""Event kinds for nodes: a second machine joining, what it offers, and leaving.

A payload names the machine as the owner named it and says what it offers. It
never carries an address, a token or a join code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

_OFFER = Literal["models", "browser"]


@kind("node.joined", example={"node_id": "01JEXAMPLE0000000000000000", "name": "example-box",
                              "version": "0.1.0"})
class NodeJoined(Payload):
    """A machine exchanged a join code for a node token."""

    node_id: str
    name: str = Field(max_length=16)
    version: str | None = Field(None, max_length=40)


@kind("node.offered", example={
    "node_id": "01JEXAMPLE0000000000000000", "name": "example-box", "version": "0.1.0",
    "protocol": 1, "offers": ["models"], "models": ["big"], "browsers": [],
})
class NodeOffered(Payload):
    """What a node offers now, and the version it runs. The whole list, so the
    latest event wins. Printed when either changes, not on every heartbeat."""

    node_id: str
    name: str = Field(max_length=16)
    version: str = Field(max_length=40)
    protocol: int
    offers: list[_OFFER] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list, max_length=16)
    browsers: list[str] = Field(default_factory=list, max_length=16)


@kind("node.left", example={"node_id": "01JEXAMPLE0000000000000000", "name": "example-box",
                            "reason": "removed"})
class NodeLeft(Payload):
    """A node's token was revoked: the owner removed it, or it left by itself."""

    node_id: str
    name: str = Field(max_length=16)
    reason: Literal["removed", "left"]
