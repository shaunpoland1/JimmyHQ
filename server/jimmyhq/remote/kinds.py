"""Event kinds for reaching JimmyHQ from other devices.

One event each time the owner turns the private-network mapping on or off from
the settings screen. It records that it happened and the local port, never a
machine or network name. (Adding a name to the allowed hosts is an ordinary
setting change and is recorded as one.)
"""

from __future__ import annotations

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind


@kind("remote.serve_changed", example={"on": True, "port": 8440})
class ServeChanged(Payload):
    on: bool  # True: the mapping was turned on. False: it was turned off.
    port: int = Field(ge=1, le=65535)  # JimmyHQ's own port that the mapping points to
