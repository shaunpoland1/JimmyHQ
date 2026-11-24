"""Shared by the roles tests: a full app with roles and charters installed for real, and
agents in the Village as its lens knows them. No network, no child process."""

from __future__ import annotations

from pathlib import Path

from apps_full_helpers import full_app
from apps_install_helpers import install_folder
from jimmyhq.village import lens as vlens
from jimmyhq.village import record as vrecord


def tool(name: str, effect: str = "read") -> dict:
    return {"name": name, "description": f"The {name} tool.",
            "params": {"piece": {"type": "string", "description": "Which piece."}},
            "required": [], "effect": effect}


TOOLS = [tool("brief"), tool("submit_draft", "record"), tool("review_packet"),
         tool("submit_review", "record"), tool("publish", "send")]
ROLES = [
    {"id": "writer", "label": "Writer", "purpose": "Drafts posts from articles.",
     "charter": "roles/writer.md", "tools": ["brief", "submit_draft"], "model": "smart",
     "not_with": ["checker"]},
    {"id": "checker", "label": "Checker", "purpose": "Checks drafts against the source.",
     "charter": "roles/checker.md", "tools": ["review_packet", "submit_review"],
     "model": "smart"},
]
CHARTERS = {"roles/writer.md": "# Writer\n\nWrite from the article's own text.\n",
            "roles/checker.md": "# Checker\n\nCheck every number against the source.\n"}


def roles_folder(tmp_path: Path, app_id: str = "posts", *, roles=None, tools=None,
                 charters=None, permissions=("agents.tools",), **extra) -> Path:
    folder = full_app(tmp_path, app_id, permissions=list(permissions),
                      tools=TOOLS if tools is None else tools,
                      roles=ROLES if roles is None else roles, **extra)
    for path, text in (CHARTERS if charters is None else charters).items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    return folder


def install_roles_app(core, tmp_path: Path, app_id: str = "posts", **kw) -> dict:
    return install_folder(core, roles_folder(tmp_path, app_id, **kw))


def agent(core, agent_id: str, *, name: str | None = None, model: str = "default",
          caps=()) -> str:
    """A living agent, as the Village's record has it."""
    vrecord.born(core, agent_id, 7, "helper")
    vrecord.configured(core, agent_id, {"role": "helper", "beat": {"role": model},
                                        "night": {"role": "smart"}, "local_only": False,
                                        "capabilities": list(caps)}, actor="owner")
    if name:
        vrecord.named(core, agent_id, name)
    return agent_id


def caps(core, agent_id: str) -> list[str]:
    return vlens.config(core, agent_id)["capabilities"]


def priorities_of(core, agent_id: str) -> list[dict]:
    return vlens.priorities(vlens.read(core), agent_id, active_only=True)


def kinds_printed(core, kind: str) -> list:
    return [e for e in core.tape.after(0) if e.kind == kind]
