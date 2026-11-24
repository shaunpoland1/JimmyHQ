"""The Village over HTTP: what the Village screen reads and what the owner may do.

The web process never runs an agent. A write goes to the Tape (a chat line, a
priority, a configuration) or into the ``commands`` table (birth, retirement,
rollback), and the village process acts on it. A read comes from the lens, from the
agent's folder, or from the village's event log.

Every route that takes an agent id checks it against the roster before it is used in
a path, and every file route resolves its path and refuses anything that lands outside
the folder it serves.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from jimmyhq.auth import Principal
from jimmyhq.core import Core
from jimmyhq.models.shapes import ROLES
from jimmyhq.village import downloads, lens, record, roster
from jimmyhq.village import log as village_log
from jimmyhq.village.memory import diary, impressions, snapshots
from jimmyhq.village.memory.store import SECTIONS, MemoryStore
from jimmyhq.village.world.body import NEEDS, compute_mood
from jimmyhq.web.deps import owner, reader

READ_BYTES = 300_000  # the most of one file sent to the screen
LIST_ITEMS = 2000
CHAT_PAGE = 200
CHAT_CHARS = 20_000
EVENTS_PAGE = 200
EVENTS_MOST = 500
ROOM_LINES = 200
BOARD_POSTS = 200
DAY_S = 86400.0
VITALS_POINTS = 200  # the most points one vitals history sends
VITALS_READ = 20_000  # the most need changes read to make it
_KIND = re.compile(r"[a-z][a-z0-9_]{0,63}")
_CAPABILITY = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
_EVENT = re.compile(r"evt-(\d{1,18})")
_DRIVE = re.compile(r"[A-Za-z]:")
BEAT_ROLES = tuple(r for r in ROLES if r != "embeddings")
MODE_LINES = {"work": "Working", "explore": "Exploring", "outlet": "Taking a break",
              "social": "In the break room", "rest": "Resting"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelChoice(_Body):
    role: str | None = None
    provider_id: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)


class Models(_Body):
    beat: ModelChoice | None = None
    night: ModelChoice | None = None


class NewAgent(_Body):
    role: str = Field(default="", max_length=80)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    models: Models = Field(default_factory=Models)
    local_only: bool = False


class Config(_Body):
    role: str = Field(default="", max_length=80)
    beat: ModelChoice | None = None
    night: ModelChoice | None = None
    local_only: bool = False
    capabilities: list[str] = Field(default_factory=list, max_length=50)


class Retire(_Body):
    note: str = Field(default="", max_length=1000)


class Rollback(_Body):
    to: int = Field(ge=1)
    diary: bool = False
    note: str = Field(default="", max_length=1000)


class ChatLine(_Body):
    text: str
    urgent: bool = False


class NewPriority(_Body):
    title: str
    why: str = Field(default="", max_length=2000)
    details: str = Field(default="", max_length=8000)
    agents: list[str] = Field(default_factory=list, max_length=50)
    active: bool = True


class PriorityChange(_Body):
    title: str | None = None
    why: str | None = Field(default=None, max_length=2000)
    details: str | None = Field(default=None, max_length=8000)
    agents: list[str] | None = Field(default=None, max_length=50)
    active: bool | None = None


# --- pure helpers -----------------------------------------------------------------


def inside(root: Path, rel: str) -> Path:
    """The real path of ``rel`` under ``root``, or a 400. Nothing absolute, nothing with
    ``..`` or a hidden name, and nothing that resolves (through a link, say) to a
    place outside ``root``."""
    rel = (rel or "").replace("\\", "/")
    if "\x00" in rel or rel.startswith("/") or _DRIVE.match(rel):
        raise HTTPException(400, "That path is not inside this folder.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." or p.startswith(".") for p in parts):
        raise HTTPException(400, "That path is not inside this folder.")
    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "That path is not inside this folder.")
    return target


def listing(root: Path, rel: str) -> dict[str, Any]:
    """A folder's contents, folders first. Hidden names, and links that lead out of
    ``root``, are left out."""
    base, folder = root.resolve(), inside(root, rel)
    if not folder.is_dir():
        raise HTTPException(404, f"There is no folder {rel or '/'} here.")
    items = []
    for entry in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        real = entry.resolve()
        if entry.name.startswith(".") or (base not in real.parents):
            continue
        stat = real.stat()
        is_dir = real.is_dir()
        items.append({"name": entry.name, "path": entry.relative_to(base).as_posix()
                      if base in entry.parents else real.relative_to(base).as_posix(),
                      "dir": is_dir, "size": None if is_dir else stat.st_size,
                      "modified": round(stat.st_mtime * 1000)})
        if len(items) >= LIST_ITEMS:
            break
    return {"path": folder.relative_to(base).as_posix() if folder != base else "",
            "items": items}


def read_text(root: Path, rel: str) -> dict[str, Any]:
    """One file as text, cut at ``READ_BYTES``."""
    base, target = root.resolve(), inside(root, rel)
    if not target.is_file():
        raise HTTPException(404, f"There is no file {rel or '/'} here.")
    size = target.stat().st_size
    with target.open("rb") as f:
        data = f.read(READ_BYTES)
    return {"path": target.relative_to(base).as_posix(), "size": size,
            "modified": round(target.stat().st_mtime * 1000), "truncated": size > READ_BYTES,
            "text": data.decode("utf-8", errors="replace")}


def status_line(state: dict[str, Any] | None) -> str:
    """One line on what an agent is doing, from its saved body."""
    if not state:
        return "Not started yet"
    if state.get("asleep"):
        kind = state.get("sleep_kind")
        return "Taking a nap" if kind == "nap" else "Asleep"
    line = MODE_LINES.get(state.get("mode"), "Awake")
    task = state.get("current_task")
    return f"{line} on {task}" if task and state.get("mode") == "work" else line


priority_id = lens.priority_id


def mood_of(state: dict[str, Any] | None) -> dict[str, float] | None:
    """Valence and arousal from the saved needs and the agent's own mood weights."""
    weights = ((state or {}).get("temperament") or {}).get("mood_weights")
    needs = (state or {}).get("needs") or {}
    if not weights or any(n not in needs or n not in weights for n in NEEDS):
        return None
    valence, arousal = compute_mood(needs, weights)
    return {"valence": valence, "arousal": arousal}


def ms(seconds: Any) -> int | None:
    """A world time (float seconds) as unix milliseconds, for the screen."""
    return None if seconds is None else round(float(seconds) * 1000)


def night_wake_ms(state: dict[str, Any], wake_hour: float, tz: str, now: float) -> int | None:
    """When a night sleeper is first likely to be up: the next time it is ``wake_hour``
    by the agent's own body clock (its clock runs ``sleep_offset_minutes`` behind the
    wall). None once that hour has passed, because it then wakes when rested."""
    offset = float((state.get("temperament") or {}).get("sleep_offset_minutes", 0)) / 60
    zone = ZoneInfo(tz) if tz else None
    here = datetime.fromtimestamp(now, zone or UTC)
    if not tz:
        here = here.astimezone()
    if wake_hour <= (here.hour + here.minute / 60 - offset) % 24 < 12:
        return None
    at = (wake_hour + offset) % 24
    target = datetime.combine(here.date(), day_time(int(at), round(at % 1 * 60) % 60),
                              tzinfo=here.tzinfo)
    if target <= here:
        target = datetime.combine(here.date() + timedelta(days=1), target.timetz().replace(
            tzinfo=None), tzinfo=here.tzinfo)
    return round(target.timestamp() * 1000)


def vitals_points(events: list[Any], before: list[Any]) -> list[dict[str, Any]]:
    """Each need change as a row of all five needs, in milliseconds, cut to at most
    ``VITALS_POINTS`` rows by keeping the last row of each equal slice of time."""
    now: dict[str, float | None] = dict.fromkeys(NEEDS)
    for e in before:  # oldest first, so the newest value of each need wins
        if e.data.get("need") in now:
            now[e.data["need"]] = e.data.get("value")
    rows = []
    for e in events:
        if e.data.get("need") in now:
            now[e.data["need"]] = e.data.get("value")
            rows.append({"t": round(e.ts * 1000), **now})
    for i, row in enumerate(rows):  # a need not seen before the window takes its first value
        for n in NEEDS:
            if row[n] is None:
                row[n] = next((r[n] for r in rows[i:] if r[n] is not None), None)
    if len(rows) > VITALS_POINTS:
        first, last = rows[0]["t"], rows[-1]["t"]
        width = (last - first) / VITALS_POINTS or 1
        kept: dict[int, dict[str, Any]] = {}
        for row in rows:
            kept[min(VITALS_POINTS - 1, int((row["t"] - first) / width))] = row
        rows = list(kept.values())
    return rows


def capability_list(core: Core) -> list[dict[str, Any]]:
    """The capabilities the tool catalog knows: those a tool is offered under. Each says
    what the agent can then do and what leaves the machine, and, when it cannot work
    yet, what is missing and which settings section fixes it."""
    from jimmyhq.apps.plugs import tools as app_tools
    from jimmyhq.village import capabilities, tools
    return [*capabilities.describe(core, tools.specs()), *app_tools.capabilities(core)]


def event_out(e: Any) -> dict[str, Any]:
    return {"id": e.id, "ts": round(e.ts * 1000), "kind": e.kind, "agent": e.agent,
            "data": e.data}


def choice_ok(choice: ModelChoice | None, label: str) -> dict[str, Any] | None:
    """A model choice as stored, or a 422 that says how to write one."""
    if choice is None:
        return None
    if bool(choice.provider_id) != bool(choice.model):
        raise HTTPException(422, f"The {label} model needs both a provider and a model "
                                 "name, or neither. Nothing was changed.")
    if choice.provider_id:
        return {"provider_id": choice.provider_id, "model": choice.model}
    if choice.role not in BEAT_ROLES:
        raise HTTPException(422, f"The {label} model's role must be one of "
                                 f"{', '.join(BEAT_ROLES)}. Nothing was changed.")
    return {"role": choice.role}


# --- the router -------------------------------------------------------------------


def build_router(core: Core) -> APIRouter:  # noqa: C901
    router = APIRouter(prefix="/api/village", tags=["village"])

    def roster_row(agent_id: str, *, alive: bool = False) -> dict[str, Any]:
        """The agent's row in the lens, or a 404. The id is checked here, before any
        route builds a path from it."""
        row = lens.read(core)["agents"].get(agent_id) if roster.valid_id(agent_id) else None
        if row is None:
            raise HTTPException(404, "There is no agent with that id.")
        if alive and row["retired_ts"] is not None:
            raise HTTPException(409, "That agent has retired. Nothing was changed.")
        return row

    def folder(row: dict[str, Any]) -> Path:
        return roster.agent_dir(core, row["id"], retired=row["retired_ts"] is not None)

    def body_state(row: dict[str, Any]) -> dict[str, Any] | None:
        try:
            from jimmyhq.village.world.state import load_json
            return load_json(folder(row) / "state.json")
        except (OSError, ValueError):
            return None

    def wake_at(state: dict[str, Any] | None) -> int | None:
        """Unix milliseconds when a resting, napping or night-sleeping agent will be up."""
        if not state:
            return None
        if state.get("asleep"):
            kind = state.get("sleep_kind")
            if kind == "nap":
                return ms(state.get("nap_until"))
            if kind != "night":
                return None
            wake = core.settings.get("village.sleep.wake_hour")
            tz = str(core.settings.get("owner.timezone") or "")
            try:
                return night_wake_ms(state, float(wake), tz, time.time())
            except (ValueError, TypeError, OSError):
                return None
        return ms(state.get("rest_until")) if state.get("mode") == "rest" else None

    def summary(row: dict[str, Any], chat: list[dict[str, Any]]) -> dict[str, Any]:
        state = body_state(row)
        return {
            "id": row["id"], "name": row["name"], "role": row["role"],
            "retired": row["retired_ts"] is not None,
            "status_line": "Retired" if row["retired_ts"] is not None else status_line(state),
            "mode": (state or {}).get("mode"), "asleep": bool((state or {}).get("asleep")),
            "needs": {k: round(float(x), 1) for k, x in ((state or {}).get("needs") or {}).items()},
            "mood": mood_of(state), "sleep_kind": (state or {}).get("sleep_kind"),
            "wake_at": wake_at(state),
            "current_task": (state or {}).get("current_task"),
            "waiting": len((state or {}).get("waiting") or {}),
            "unanswered": lens.unanswered(chat),
        }

    # --- agents -------------------------------------------------------------------

    @router.get("/agents")
    def agents(retired: bool = False, _: Principal = Depends(reader)) -> list[dict[str, Any]]:  # noqa: B008
        state = lens.read(core)
        rows = [state["agents"][i] for i in state["order"]] if retired else lens.living(state)
        return [summary(r, state["chat"].get(r["id"], [])) for r in rows]

    @router.get("/agents/{agent_id}")
    def agent_card(agent_id: str, _: Principal = Depends(reader)) -> dict[str, Any]:  # noqa: B008
        row = roster_row(agent_id)
        state, home = body_state(row), folder(row)
        out = summary(row, lens.read(core)["chat"].get(agent_id, []))
        temper = (state or {}).get("temperament") or {}
        store = MemoryStore(home)
        out.update(
            born_ts=row["born_ts"], retired_ts=row["retired_ts"], seed=row["seed"],
            vitals={"sleep_kind": (state or {}).get("sleep_kind"),
                    "parked": sorted((state or {}).get("parked") or {}),
                    "rest_until": ms((state or {}).get("rest_until"))},
            config=row["config"], temperament=temper,
            last_night=row["last_night"], last_restore=row["last_restore"],
            counts={"memory": len(store.items), "candidates": len(store.candidates()),
                    "diary_days": len(diary.days(home)),
                    "snapshots": len(snapshots.list_all(home)),
                    "events_today": village_log(core).counts_by_kind(
                        agent=agent_id, since=time.time() - DAY_S)})
        return out

    @router.get("/agents/{agent_id}/vitals")
    def vitals(agent_id: str, hours: int = Query(24, ge=1, le=168),
               _: Principal = Depends(reader)) -> list[dict[str, Any]]:  # noqa: B008
        roster_row(agent_id)
        log, since = village_log(core), time.time() - hours * 3600
        before = log.query(agent=agent_id, kinds=["need_change"], until=since, limit=50)
        found = log.query(agent=agent_id, kinds=["need_change"], since=since,
                          limit=VITALS_READ)
        return vitals_points(found, before)

    @router.get("/capabilities")
    def capabilities(_: Principal = Depends(reader)) -> list[dict[str, Any]]:  # noqa: B008
        return capability_list(core)

    @router.post("/agents")
    def birth(body: NewAgent, who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        payload = {"role": body.role.strip(), "seed": body.seed, "local_only": body.local_only,
                   "models": {"beat": choice_ok(body.models.beat, "everyday"),
                              "night": choice_ok(body.models.night, "night")}}
        return {"command_id": roster.submit(core, roster.BIRTH, None, payload, by=who.actor)}

    @router.put("/agents/{agent_id}/config")
    def configure(agent_id: str, body: Config,
                  who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        roster_row(agent_id, alive=True)
        bad = [c for c in body.capabilities if not _CAPABILITY.fullmatch(c)]
        if bad:
            raise HTTPException(422, f"{bad[0]!r} is not a capability name. Nothing was changed.")
        config = lens.clean_config({
            "role": body.role.strip(), "beat": choice_ok(body.beat, "everyday"),
            "night": choice_ok(body.night, "night"), "local_only": body.local_only,
            "capabilities": list(dict.fromkeys(body.capabilities))})
        record.configured(core, agent_id, config, actor=who.actor)
        return config

    @router.post("/agents/{agent_id}/retire")
    def retire(agent_id: str, body: Retire | None = None,
               who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        roster_row(agent_id, alive=True)
        payload = {"note": (body.note if body else "").strip()}
        return {"command_id": roster.submit(core, roster.RETIRE, agent_id, payload,
                                            by=who.actor)}

    @router.get("/commands/{command_id}")
    def command(command_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        found = roster.get(core, command_id)
        if found is None:
            raise HTTPException(404, "There is no command with that id.")
        return {"done": found["done_ts"] is not None, "result": found["result"],
                "kind": found["kind"], "agent": found["agent"]}

    # --- priorities ---------------------------------------------------------------

    def check_agents(ids: list[str]) -> list[str]:
        known = lens.read(core)["agents"]
        unknown = [a for a in ids if a not in known]
        if unknown:
            raise HTTPException(422, f"There is no agent {unknown[0]!r}. Nothing was changed.")
        return list(dict.fromkeys(ids))

    def title_ok(title: str) -> str:
        title = " ".join(title.split())
        if not title or len(title) > 200:
            raise HTTPException(422, "A priority needs a title of 1 to 200 characters. "
                                     "Nothing was changed.")
        return title

    @router.get("/priorities")
    def priorities(_: Principal = Depends(reader)) -> list[dict[str, Any]]:  # noqa: B008
        return lens.priorities(lens.read(core))

    @router.post("/priorities")
    def add_priority(body: NewPriority,
                     who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        title = title_ok(body.title)
        state = lens.read(core)
        pid = priority_id(title, set(state["priorities"]))
        record.prioritized(core, {
            "priority_id": pid, "title": title, "why": body.why.strip(),
            "details": body.details.strip(), "agents": check_agents(body.agents),
            "active": body.active, "suggested_by": None}, actor=who.actor)
        return lens.read(core)["priorities"][pid]

    @router.put("/priorities/{pid}")
    def change_priority(pid: str, body: PriorityChange,
                        who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        now = lens.read(core)["priorities"].get(pid)
        if now is None:
            raise HTTPException(404, "There is no priority with that id.")
        record.prioritized(core, {
            "priority_id": pid,
            "title": now["title"] if body.title is None else title_ok(body.title),
            "why": now["why"] if body.why is None else body.why.strip(),
            "details": now["details"] if body.details is None else body.details.strip(),
            "agents": now["agents"] if body.agents is None else check_agents(body.agents),
            "active": now["active"] if body.active is None else body.active,
            "suggested_by": now["suggested_by"]}, actor=who.actor)
        return lens.read(core)["priorities"][pid]

    # --- chat ---------------------------------------------------------------------

    @router.get("/agents/{agent_id}/chat")
    def chat(agent_id: str, before: int | None = None,
             _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        row = roster_row(agent_id)
        lines = lens.read(core)["chat"].get(agent_id, [])
        if before is not None:
            held = [m for m in lines if m["seq"] < before]
            if not held and (not lines or before <= lines[0]["seq"]):
                # Older than the lens keeps: one indexed read of this agent's own lines.
                older = core.tape.by_subject(record.subject(agent_id), kind="village.said",
                                             before=before, limit=CHAT_PAGE, newest_first=True)
                held = [{"seq": e.seq, "id": e.id, "ts": e.ts, "sender": e.payload["sender"],
                         "text": e.payload["text"], "urgent": bool(e.payload.get("urgent"))}
                        for e in reversed(older) if e.readable]  # not an erased line
            lines = held
        lines = lines[-CHAT_PAGE:]
        return {"messages": lines, "asleep": bool((body_state(row) or {}).get("asleep")),
                "more": bool(lines) and lines[0]["seq"] > 1 and len(lines) >= CHAT_PAGE}

    @router.post("/agents/{agent_id}/chat")
    def say(agent_id: str, body: ChatLine,
            _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        row = roster_row(agent_id, alive=True)
        text = body.text.strip()
        if not text or len(text) > CHAT_CHARS:
            raise HTTPException(422, f"A message needs 1 to {CHAT_CHARS} characters. "
                                     "Nothing was sent.")
        event = record.said(core, agent_id, record.OWNER, text, urgent=body.urgent)
        asleep = bool((body_state(row) or {}).get("asleep"))
        return {"seq": event.seq, "id": event.id, "ts": event.ts, "sender": record.OWNER,
                "text": text, "urgent": body.urgent,
                "delivered": "when it wakes" if asleep and not body.urgent else "now"}

    # --- what an agent keeps --------------------------------------------------------

    @router.get("/agents/{agent_id}/diary")
    def agent_diary(agent_id: str, day: str = "",
                    _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        home = folder(roster_row(agent_id))
        days = diary.days(home)
        if day and not diary.valid_day(day):
            raise HTTPException(422, "A day is written YYYY-MM-DD.")
        day = day or (days[-1] if days else "")
        return {"days": days, "day": day, "entries": diary.entries(home, day) if day else []}

    @router.get("/agents/{agent_id}/memory")
    def agent_memory(agent_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        row = roster_row(agent_id)
        home = folder(row)
        store = MemoryStore(home)
        return {
            "sections": {s: sorted(store.section(s), key=lambda i: i["id"]) for s in SECTIONS},
            "candidates": store.candidates(),
            # A blank impression is no impression at all.
            "impressions": [{"about": who, "text": text} for who in impressions.known(home)
                            if (text := impressions.read(home, who).strip())],
            "last_night": row["last_night"],
        }

    @router.get("/agents/{agent_id}/snapshots")
    def agent_snapshots(agent_id: str, _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return {"snapshots": list(reversed(snapshots.list_all(folder(roster_row(agent_id)))))}

    @router.post("/agents/{agent_id}/rollback")
    def rollback(agent_id: str, body: Rollback,
                 who: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        row = roster_row(agent_id, alive=True)
        if snapshots.manifest(folder(row), body.to) is None:
            raise HTTPException(404, f"There is no snapshot {body.to} for this agent. "
                                     "Nothing was changed.")
        payload = {"to": body.to, "diary": body.diary, "note": body.note.strip()}
        return {"command_id": roster.submit(core, roster.ROLLBACK, agent_id, payload,
                                            by=who.actor)}

    # --- files --------------------------------------------------------------------

    @router.get("/agents/{agent_id}/files")
    def agent_files(agent_id: str, path: str = "",
                    _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        root = folder(roster_row(agent_id)) / "home" / "files"
        if not root.is_dir():
            return {"path": "", "items": []}
        return listing(root, path)

    @router.get("/agents/{agent_id}/files/read")
    def agent_file(agent_id: str, path: str,
                   _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        return read_text(folder(roster_row(agent_id)) / "home" / "files", path)

    def send_file(root: Path, rel: str) -> FileResponse:
        target = inside(root, rel)
        if not target.is_file():
            raise HTTPException(404, f"There is no file {rel or '/'} here.")
        return FileResponse(target, media_type=downloads.media_type(target.name),
                            headers=downloads.headers(target.name))

    def send_zip(root: Path, rel: str, label: str) -> StreamingResponse:
        base, target = root.resolve(), inside(root, rel)
        if not target.is_dir():
            raise HTTPException(404, f"There is no folder {rel or '/'} here.")
        cap = int(core.settings.get(downloads.CAP_SETTING)) * 1024 * 1024
        try:
            handle, size = downloads.build_zip(base, target, cap)
        except downloads.TooBig as exc:
            raise HTTPException(413, str(exc)) from None
        name = (target.name if target != base else label) + ".zip"
        return StreamingResponse(downloads.stream(handle), media_type="application/zip",
                                 headers=downloads.headers(name, size))

    @router.get("/agents/{agent_id}/files/download")
    def agent_file_download(agent_id: str, path: str,
                            _: Principal = Depends(owner)) -> FileResponse:  # noqa: B008
        return send_file(folder(roster_row(agent_id)) / "home" / "files", path)

    @router.get("/agents/{agent_id}/files/zip")
    def agent_files_zip(agent_id: str, path: str = "",
                        _: Principal = Depends(owner)) -> StreamingResponse:  # noqa: B008
        root = folder(roster_row(agent_id)) / "home" / "files"
        if not root.is_dir():
            raise HTTPException(404, "This agent has no files yet.")
        return send_zip(root, path, f"{agent_id}-files")

    @router.get("/shared/download")
    def shared_download(path: str, _: Principal = Depends(owner)) -> FileResponse:  # noqa: B008
        return send_file(roster.shared_dir(core), path)

    @router.get("/shared/zip")
    def shared_zip(path: str = "", _: Principal = Depends(owner)) -> StreamingResponse:  # noqa: B008
        root = roster.shared_dir(core)
        if not root.is_dir():
            raise HTTPException(404, "Nothing has been shared yet.")
        return send_zip(root, path, "shared-folders")

    @router.get("/shared")
    def shared(path: str = "", _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        root = roster.shared_dir(core)
        if not root.is_dir():
            return {"path": "", "items": []}
        if inside(root, path).is_file():
            return read_text(root, path)
        return listing(root, path)

    # --- talk and events ------------------------------------------------------------

    @router.get("/breakroom")
    def breakroom(_: Principal = Depends(reader)) -> dict[str, Any]:  # noqa: B008
        events = village_log(core).query(kinds=["breakroom", "social_unanswered"],
                                         limit=ROOM_LINES)
        return {"lines": [{"id": e.id, "ts": round(e.ts * 1000), "agent": e.agent,
                           "kind": e.kind,
                           "text": e.data.get("text", "(nobody answered; went back to work)")}
                          for e in events]}

    @router.get("/board")
    def board(_: Principal = Depends(reader)) -> dict[str, Any]:  # noqa: B008
        log = village_log(core)
        resolved = {e.data.get("post"): e for e in log.query(kinds=["board_resolved"],
                                                             limit=BOARD_POSTS * 2)}
        posts = []
        for e in log.query(kinds=["board_post"], limit=BOARD_POSTS):
            done = resolved.get(e.id)
            posts.append({"id": e.id, "ts": round(e.ts * 1000), "agent": e.agent,
                          "kind": e.data.get("post_kind"), "text": e.data.get("text", ""),
                          "open": done is None,
                          "resolved": None if done is None else {
                              "by": done.agent, "ts": round(done.ts * 1000),
                              "note": done.data.get("note", "")}})
        return {"posts": posts}

    @router.get("/agents/{agent_id}/events")
    def events(agent_id: str, kinds: str = "", before: str = "", limit: int = EVENTS_PAGE,
               _: Principal = Depends(owner)) -> dict[str, Any]:  # noqa: B008
        roster_row(agent_id)
        wanted = [k for k in kinds.split(",") if k]
        if any(not _KIND.fullmatch(k) for k in wanted):
            raise HTTPException(422, "Event kinds are lower-case words separated by commas.")
        limit = max(1, min(int(limit), EVENTS_MOST))
        log = village_log(core)
        if not before:
            found = log.query(agent=agent_id, kinds=wanted or None, limit=limit)
        else:
            m = _EVENT.fullmatch(before)
            edge = log.get(before) if m else None
            if edge is None:
                raise HTTPException(422, "\"before\" is an event id from this list, like evt-12.")
            # Several events can share a millisecond, so read a little past the edge
            # and keep only those that really come before it.
            found = [e for e in log.query(agent=agent_id, kinds=wanted or None, until=edge.ts,
                                          limit=limit + 100)
                     if int(e.id[4:]) < int(m.group(1))][-limit:]
        return {"events": [event_out(e) for e in found]}

    return router
