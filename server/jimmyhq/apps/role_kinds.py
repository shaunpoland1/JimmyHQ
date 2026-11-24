"""Event kinds for roles: an app's roles being filled and emptied, the work it gave out,
and a ``record`` tool's receipt. Imported by ``apps.kinds``, so they are registered
wherever the app kinds are.

Each is printed with the Tape subject ``app:<id>``, beside the rest of the app's story.
"""

from __future__ import annotations

from typing import Literal

from jimmyhq.tape.registry import Payload, kind

_TASK = "01JABCDEFGHJKMNPQRSTVWXYZ0"

EmptiedWhy = Literal["owner", "app_removed", "agent_retired", "capability_taken", "role_gone",
                     "moved"]
WorkState = Literal["done", "set_aside", "dropped", "reassigned", "withdrawn"]


@kind("apps.role_filled", example={
    "app_id": "posts", "role": "writer", "agent_id": "agent-b", "created": True,
    "priority_id": "p-app-posts-writer", "charter_sha": "9f2c1e5a7b3d4c6e",
})
class AppRoleFilled(Payload):
    """The owner gave one of an app's roles to an agent: the agent was given the role's
    capability (``app.<id>.<role>``) and its charter as a standing priority. ``created`` is
    true for an agent made for the role. No level on the ladder changes."""

    app_id: str
    role: str
    agent_id: str
    created: bool = False
    priority_id: str
    charter_sha: str  # the start of the sha256 of the charter the agent was given


@kind("apps.role_emptied", example={
    "app_id": "posts", "role": "writer", "agent_id": "agent-b", "reason": "owner",
})
class AppRoleEmptied(Payload):
    """A role is no longer held by that agent: its capability and its charter were taken
    away. The agent itself stays."""

    app_id: str
    role: str
    agent_id: str
    reason: EmptiedWhy = "owner"


@kind("apps.work_given", example={
    "app_id": "posts", "task_id": _TASK, "role": "writer", "agent_id": "agent-b",
    "subject": "piece:12", "due_ms": None,
})
class AppWorkGiven(Payload):
    """An app gave a piece of work to whoever holds one of its roles: a task on that
    agent's list."""

    app_id: str
    task_id: str
    role: str
    agent_id: str
    subject: str | None = None  # as the app sent it
    due_ms: int | None = None


@kind("apps.work_reported", example={
    "app_id": "posts", "task_id": _TASK, "state": "set_aside", "agent_id": "agent-b",
    "note": "Waiting for the source to load.",
})
class AppWorkReported(Payload):
    """Something happened to a piece of work an app gave out that the task list does not
    say by itself: the agent set it aside, or the role was emptied or moved, or the app
    withdrew it. Done, and dropped or reassigned by the owner, are read from the task's
    own events."""

    app_id: str
    task_id: str
    state: WorkState
    agent_id: str | None = None  # who holds it now
    note: str | None = None


@kind("apps.tool_recorded", example={
    "app_id": "posts", "tool": "submit_draft", "agent_id": "agent-b", "role": "writer",
    "ok": True,
})
class AppToolRecorded(Payload):
    """A receipt for one run of a ``record`` tool: an agent wrote into the app's own
    records. Which agent, through which role, and whether the app took it; never what."""

    app_id: str
    tool: str
    agent_id: str
    role: str | None = None
    ok: bool
