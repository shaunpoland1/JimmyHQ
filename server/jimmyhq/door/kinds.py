"""Event kinds for the MCP door.

A client's permissions and every call it makes are events. A token is never here: its
digest lives in the access database. A receipt carries names, sizes and the id of what a
write created, never the text of an argument or of an answer.

The two message kinds do carry text: a line an outside client sent to an agent, and an
agent's answer. They are the record of a conversation, as ``village.said`` is, and the
owner can have one erased like any other event.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

_ID = "01JABCDEFGHJKMNPQRSTVWXYZ0"
_OPTIONS = {"bench_bodies": False, "addresses": False, "task_areas": [],
            "calls_per_minute": None, "messages_need_approval": False}


class Options(Payload):
    """What a client may see beyond the plain answer, and its own pace."""

    bench_bodies: bool = False  # card summaries and bodies, not only titles and kinds
    addresses: bool = False  # web and mail addresses left in what it is shown
    task_areas: list[str] = Field(default_factory=list)  # empty means every area
    calls_per_minute: int | None = None  # None means the ``door.calls_per_minute`` setting
    # True: a message to an agent is a Bench card first, delivered only on approval.
    messages_need_approval: bool = False


@kind("door.client.added", example={
    "client_id": _ID, "name": "helper", "about": "An assistant on another machine.",
    "tools": {"whoami": None}, "options": _OPTIONS,
})
class ClientAdded(Payload):
    """The owner named a new outside client. ``tools`` maps each tool it may use to its
    own daily limit, or None for the tool's setting. A new client has no tools."""

    client_id: str
    name: str
    about: str = ""
    tools: dict[str, int | None] = Field(default_factory=dict)
    options: Options = Field(default_factory=Options)


@kind("door.client.changed", example={
    "client_id": _ID, "about": "An assistant on another machine.",
    "tools": {"tasks_list": None, "tasks_add": 5}, "options": _OPTIONS,
})
class ClientChanged(Payload):
    """What a client may do, as it now stands (the whole of it, not a change)."""

    client_id: str
    about: str = ""
    tools: dict[str, int | None] = Field(default_factory=dict)
    options: Options = Field(default_factory=Options)


@kind("door.client.rotated", example={"client_id": _ID})
class ClientRotated(Payload):
    """A client was given a new token. The old one stopped working at once."""

    client_id: str


@kind("door.client.revoked", example={"client_id": _ID})
class ClientRevoked(Payload):
    """A client was shut out for good. Its name is not given to another."""

    client_id: str


@kind("door.called", example={
    "client_id": _ID, "client": "helper", "tool": "tasks_add", "ok": True, "rule": "",
    "write": True, "in_bytes": 212, "out_chars": 148, "ms": 12,
    "created": "task:01JABCDEFGHJKMNPQRSTVWXYZ1",
})
class Called(Payload):
    """One call through the door. ``rule`` names the rule that refused it, when one
    did. ``created`` is what a write made. No argument and no answer is kept. For a tool
    of an installed app, ``app_id`` and ``app_tool`` say which (``tool`` is then the name
    the client called it by, ``app_<id>_<name>``). ``reached`` is True when the app was
    asked: such a call counts toward the day's limit whether it worked, failed or was
    never answered."""

    client_id: str
    client: str
    tool: str
    ok: bool
    rule: str = ""
    write: bool = False
    in_bytes: int = 0
    out_chars: int = 0
    ms: int = 0
    created: str | None = None
    app_id: str | None = None
    app_tool: str | None = None
    reached: bool = False


@kind("door.messaged", example={
    "client_id": _ID, "client": "helper", "agent_id": "agent-a",
    "text": "The report you asked about is ready.",
})
class Messaged(Payload):
    """An outside client left a message for an agent. The agent reads it as untrusted
    text: it is information, and can make nothing happen by itself."""

    client_id: str
    client: str
    agent_id: str
    text: str


@kind("door.replied", example={
    "client_id": _ID, "client": "helper", "agent_id": "agent-a", "text": "Thank you.",
})
class Replied(Payload):
    """An agent answered an outside client. The client reads it the next time it asks."""

    client_id: str
    client: str
    agent_id: str
    text: str


Direction = Literal["in", "out"]
