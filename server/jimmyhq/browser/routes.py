"""What the owner's Browser tab reads, and the two choices the owner makes there.

    GET /api/village/agents/{id}/browser            tabs, address, title, limits, level
    GET /api/village/agents/{id}/browser/picture    the newest picture of the page
    PUT /api/village/agents/{id}/browser            the level for forms; the allow-list

The owner watches and does not drive: nothing here clicks, types or opens a page. The
web process holds no browser. It reads the two files the village process leaves under
the agent's folder, and counts today's use from the village log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from jimmyhq.auth import Principal
from jimmyhq.browser import CAPABILITY, config, engine, session
from jimmyhq.core import Core
from jimmyhq.settings import SettingError
from jimmyhq.web.deps import owner

OPENED, ACTED, ASKED = "browser_opened", "browser_acted", "browser_asked"


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submit_level: str | None = Field(None, max_length=20)
    allow_hosts: list[str] | None = Field(None, max_length=200)


def day_start(core: Core) -> float:
    """Midnight today on the owner's clock, in seconds: where the day's limits count from."""
    tz = str(core.settings.get("owner.timezone") or "")
    try:
        now = datetime.now(ZoneInfo(tz)) if tz else datetime.now(UTC).astimezone()
    except (KeyError, ValueError):
        now = datetime.now(UTC).astimezone()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def state(core: Core, row: dict[str, Any], folder: Any) -> dict[str, Any]:
    from jimmyhq import village
    from jimmyhq.village.comms import load_json

    agent_id = row["id"]
    status = session.read_status(folder)
    log, since = village.log(core), day_start(core)

    def used(kind: str) -> int:
        return log.count(agent=agent_id, kinds=[kind], since=since)

    waiting = [{"card_id": card_id, "host": p.get("host"),
                "asked_ms": int(float(p.get("asked") or 0) * 1000)}
               for card_id, p in load_json(session.folder(folder) / "pending.json").items()
               if p.get("status") == "open"]
    problem = engine.problem(core)
    return {
        "agent_id": agent_id,
        "held": CAPABILITY in (row["config"].get("capabilities") or []),
        "ready": problem is None, "problem": problem,
        "running": bool(status.get("running")),
        "updated_ms": status.get("updated_ms"),
        "picture_ms": status.get("picture_ms") if status.get("running") else None,
        "tabs": status.get("tabs") or [],
        "refused": int(status.get("refused") or 0),
        "refused_last": status.get("refused_last") or [],
        "submit_level": config.submit_level(core, agent_id),
        "allow_hosts": config.allow_hosts(core, agent_id),
        "waiting": waiting,
        "limits": {
            "pages": {"used": used(OPENED), "cap": core.settings.get("browser.pages_per_day")},
            "actions": {"used": used(ACTED),
                        "cap": core.settings.get("browser.actions_per_day")},
            "requests": {"used": used(ASKED),
                         "cap": core.settings.get("browser.requests_per_day")},
        },
    }


def build_router(core: Core) -> APIRouter:
    from jimmyhq.village import lens, roster

    router = APIRouter(prefix="/api/village/agents/{agent_id}/browser", tags=["browser"])

    def found(agent_id: str, *, alive: bool = False) -> tuple[dict[str, Any], Any]:
        row = lens.read(core)["agents"].get(agent_id) if roster.valid_id(agent_id) else None
        if row is None:
            raise HTTPException(404, "There is no agent with that id.")
        retired = row["retired_ts"] is not None
        if alive and retired:
            raise HTTPException(409, "That agent has retired. Nothing was changed.")
        return row, roster.agent_dir(core, agent_id, retired=retired)

    @router.get("")
    def watch(agent_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return state(core, *found(agent_id))

    @router.get("/picture")
    def picture(agent_id: str, _: Principal = Depends(owner)) -> FileResponse:  # noqa: B008
        _row, folder = found(agent_id)
        path = session.folder(folder) / session.PICTURE
        if not session.read_status(folder).get("running") or not path.is_file():
            raise HTTPException(404, "There is no picture: this agent's browser is not "
                                     "showing a page right now.")
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "no-store"})

    @router.put("")
    def change(agent_id: str, body: Change,
               who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        row, folder = found(agent_id, alive=True)
        if body.submit_level is not None and body.submit_level not in config.LEVELS:
            raise HTTPException(422, "The level is one of: read, approve, alone. "
                                     "Nothing was changed.")
        if body.allow_hosts is not None:
            try:
                config.set_allow_hosts(core, agent_id, body.allow_hosts, actor=who.actor)
            except SettingError as e:
                raise HTTPException(422, f"{e} Nothing was changed.") from None
        if body.submit_level is not None:
            config.set_submit_level(core, agent_id, body.submit_level, by=who.actor)
        return state(core, row, folder)

    return router
