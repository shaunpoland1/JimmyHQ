"""The stored view of nodes: who has joined, and what each last said it offers."""

from __future__ import annotations

from typing import Any

from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event


class NodesLens(Lens):
    name = "nodes.roster"
    version = 1
    kinds = ("node.joined", "node.offered", "node.left")

    def initial(self) -> dict[str, Any]:
        return {"nodes": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        payload = event.payload
        nodes = state["nodes"]
        if event.kind == "node.joined":
            nodes[payload["node_id"]] = {
                "id": payload["node_id"], "name": payload["name"], "joined_ms": event.ts,
                "version": payload.get("version"), "protocol": None, "offers": [],
                "models": [], "browsers": [],
            }
        elif event.kind == "node.offered":
            node = nodes.get(payload["node_id"])
            if node is not None:
                for field in ("version", "protocol", "offers", "models", "browsers"):
                    node[field] = payload[field]
        elif event.kind == "node.left":
            nodes.pop(payload["node_id"], None)
        return state
