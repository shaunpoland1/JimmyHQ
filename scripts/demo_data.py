"""Fill an empty data folder with an invented household, for screenshots and trying JimmyHQ out.

    JIMMYHQ_DATA=/tmp/jimmyhq-demo JIMMYHQ_PORT=8478 uv run python scripts/demo_data.py
    ... demo_data.py --serve         # fill, then keep JimmyHQ running (Ctrl-C stops it)
    ... demo_data.py --serve-only    # run a folder that was filled before

It refuses to run unless JIMMYHQ_DATA is set and names a folder that does not exist or is
empty, and it never uses port 8440. Everything in it is invented. The owner is "Alex"; the
password is public (`DEMO_PASSWORD` in `_demo_common.py`) and protects nothing.

How it fills the folder, in three steps. Each goes through the code the product uses:

1. With JimmyHQ stopped, it opens the data folder the way JimmyHQ does and writes the
   first nine days of history through the same functions the screens call: the owner
   account, settings, a model provider, tasks, memory notes and facts, and Bench cards.
   The Tape's clock is moved back for this step, so the history is spread over nine days
   instead of one minute. Usage lines (`model.called`) are printed with `Tape.print`, as the
   models module prints them after a real answer.
2. It starts JimmyHQ (and a small pretend model that answers from a fixed list), then uses
   the web API as the owner: three Village agents, a chat thread, and each first-party app
   installed and filled by the calls its own page makes (`scripts/demo_seed/<app>.py`).
3. It stops JimmyHQ, by the pid it started, and gives the three agents names by printing the
   same `village.named` event that an agent's first night prints (a model is needed for
   that, and the demo has none).

Times: the history is placed relative to the moment the script runs (nine days back up to
a few minutes ago), so the content is the same every run and only the dates move. Apps date
their own content from the day they are filled.

Where it does not use the product's own path: nothing is written into a database directly.
The one stand-in is the agents' names (step 3). What the apps could not be filled with
honestly is listed in each sample module's `NOT_SEEDED` and printed at the end.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _demo_common as common  # noqa: E402

TIMEZONE = "Europe/London"
REPO = Path(__file__).resolve().parents[1]


def present_apps() -> list[str]:
    """The first-party apps in this checkout that have sample content (a module of the same
    name in `scripts/demo_seed/`), in name order. Nothing here names an app: what is on disk
    decides, so a copy of the repository without some apps fills the apps it has."""
    seeded = {p.stem for p in (Path(__file__).resolve().parent / "demo_seed").glob("*.py")}
    folders = {p.parent.name for p in (REPO / "apps").glob("*/jimmyhq.app.json")}
    return sorted((folders & seeded) - {"__init__"})


APPS = tuple(present_apps())


def not_seeded() -> list[str]:
    """What the apps cannot be filled with through their own calls: each sample module's own
    `NOT_SEEDED` lines (see its docstring), printed at the end."""
    lines: list[str] = []
    for app_id in APPS:
        lines += list(getattr(importlib.import_module(f"demo_seed.{app_id}"), "NOT_SEEDED", []))
    return lines


AGENTS = [
    ("agent-a", "Wren", "Household researcher", 11),
    ("agent-b", "Moss", "Letters and forms", 22),
    ("agent-c", "Fern", "Garden and home planner", 33),
]
HOUR = 3_600_000
DAY = 24 * HOUR


# ----------------------------------------------------------------------------------------
# Step 1: nine days of history, with the Tape's clock moved back
# ----------------------------------------------------------------------------------------


class TimeTravel:
    """Moves the clock the Tape stamps events with. Nothing else is touched."""

    def __init__(self) -> None:
        from jimmyhq import ids
        from jimmyhq.tape import registry

        self.modules = (ids, registry)
        self.now = int(time.time() * 1000)
        self.at(self.now)

    def at(self, ms: int) -> int:
        self.t = ms
        for module in self.modules:
            module.now_ms = lambda: self.t  # type: ignore[attr-defined]
        return ms

    def back(self, hours: float) -> int:
        return self.at(self.now - int(hours * HOUR))

    def restore(self) -> None:
        from jimmyhq import ids
        from jimmyhq.tape import registry

        real = lambda: int(time.time_ns() // 1_000_000)  # noqa: E731
        for module in (ids, registry):
            module.now_ms = real  # type: ignore[attr-defined]


TASKS = [
    # (days from today, title, area, repeat, notes, hours ago it was finished or None)
    (0, "Put the recycling out", "home", "FREQ=WEEKLY;BYDAY=TH",
     "Blue box and the paper bag.", None),
    (0, "Call the engineer about the boiler service", "home", None,
     "Number is in the Home note. Ask for a morning.", None),
    (0, "Pick up the prescription", "health", None, "", None),
    (1, "Water the greenhouse", "garden", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "", None),
    (2, "Renew the car insurance", "money", None,
     "The renewal letter is in the grey folder. Compare two quotes first.", None),
    (3, "Plan the garden beds for spring", "garden", None, "", None),
    (4, "Book the dentist for both of us", "health", None, "", None),
    (6, "Sort the photos from the coast trip", "home", None, "", None),
    (-1, "Reply to the school about the trip form", "family", None, "", None),
    (9, "Clear the gutters", "home", None, "Wait for a dry week.", None),
    (0, "Check the smoke alarm batteries", "home", None, "", 6),
    (0, "Pay the water bill", "money", None, "", 52),
    (0, "Return the library books", "family", None, "", 14),
    (0, "Order compost", "garden", None, "", 3),
    (0, "Book the car MOT", "money", None, "", 4),
]

NOTES = {
    "shared/about-me.md": (
        "# About me\n\nI am Alex. I live with my partner and two cats in a small terraced house. "
        "I work from home on Tuesdays and Thursdays.\n\n- I like the day planned the evening "
        "before.\n- I prefer short answers, with the reason in one line.\n- Weekends are for the "
        "garden and long walks.\n"),
    "shared/preferences.md": (
        "# Preferences\n\n- Meetings and calls before noon, never on Fridays.\n- Ask before "
        "anything is sent for me. Drafts are fine.\n- Quiet hours: 21:00 to 07:00.\n- Metric "
        "units, 24-hour clock.\n"),
    "shared/projects.md": (
        "# Projects\n\n## Garden beds\nFour raised beds along the south fence. Tomatoes and "
        "beans in the two sunny ones, salad leaves and herbs in the others.\n\n## Boiler service"
        "\nDue this month. Last one was done by a local engineer; the notes are in the Home "
        "note.\n\n## Coast trip\nThree nights in spring. Still to book: the cottage and the "
        "train.\n"),
    "shared/home.md": (
        "# Home\n\n- Boiler: service every autumn. Ask for a morning visit.\n- Bins: recycling "
        "on Thursdays, general waste every second Monday.\n- Spare key is with the neighbour at "
        "number 12.\n- Smoke alarms: check batteries in March and September.\n"),
    "shared/recipes/lentil-soup.md": (
        "# Lentil soup\n\nOnion, carrot, celery, a cup of red lentils, a tin of tomatoes, stock. "
        "Simmer 25 minutes. Better the next day.\n"),
}

FACTS = [
    ("preference", "Prefers a short answer with the reason in one line."),
    ("preference", "Does not want calls on Fridays."),
    ("routine", "Plans the next day in the evening, around nine."),
    ("routine", "Recycling goes out on Thursday evening."),
    ("person", "Number 12 holds the spare house key."),
    ("project", "The south fence garden has four raised beds."),
    ("decision", "Hosted models are used only when asked for by name."),
    ("home", "The boiler is serviced every autumn, in the morning."),
]

# (hours before now, caller, role, input tokens, output tokens, ms)
USAGE = [
    (200, "chat.reply", "default", 380, 210, 4100), (197, "chat.reply", "default", 520, 260, 5200),
    (180, "memory.mining", "cheap", 1800, 150, 7600),
    (150, "village.beat", "default", 900, 120, 3300),
    (149, "village.beat", "default", 940, 95, 2900), (124, "chat.reply", "default", 410, 330, 6100),
    (100, "village.night", "smart", 4200, 620, 21000),
    (98, "memory.mining", "cheap", 1650, 140, 7100),
    (76, "chat.reply", "default", 460, 180, 3900), (75, "chat.reply", "default", 700, 240, 5000),
    (52, "village.beat", "default", 880, 110, 3100),
    (51, "village.beat", "default", 910, 105, 3200),
    (30, "chat.reply", "default", 350, 140, 3000), (28, "memory.mining", "cheap", 1700, 160, 7400),
    (9, "village.beat", "default", 930, 100, 3000), (8, "chat.reply", "default", 560, 290, 5600),
    (3, "village.beat", "default", 900, 90, 2800), (2, "chat.reply", "default", 430, 200, 4000),
]


def card(core, *, hours_ago: float, travel: TimeTravel, filed_by: str, **fields) -> str:
    from jimmyhq.bench import cards

    travel.back(hours_ago)
    return cards.file_card(core, filed_by=filed_by, **fields)


def history(core, travel: TimeTravel, port: int) -> None:
    """Nine days of a household, written through the product's own functions."""
    from jimmyhq import memory
    from jimmyhq.bench import cards
    from jimmyhq.models import brain
    from jimmyhq.tasks import service as tasks_service

    zone = ZoneInfo(TIMEZONE)
    today = datetime.fromtimestamp(travel.now / 1000, zone).date()
    owner = "owner"

    # Day 0: the account and the choices made in the welcome steps.
    travel.back(9 * 24 - 1)
    core.auth.create_owner(common.DEMO_PASSWORD)
    core.tape.print("owner.created", {}, actor=owner)
    for key, value in (("owner.name", common.DEMO_OWNER), ("owner.timezone", TIMEZONE),
                       ("owner.week_starts", "monday"), ("appearance.theme", "dark"),
                       ("wall.clock_24h", True),
                       # Agents take a turn every ten minutes or so, so the demo is calm.
                       ("village.heartbeat_min_s", 600), ("village.heartbeat_max_s", 1800),
                       ("setup.welcome", "done")):
        core.settings.set(key, value, actor=owner)

    # The models: one on this computer, and a hosted one that is switched off.
    travel.back(9 * 24 - 2)
    models = brain(core)
    models.save_provider({
        "id": "demo-local", "type": "openai_compatible", "label": "Model on this computer",
        "base_url": f"http://127.0.0.1:{port + 1}/v1", "local": True}, actor=owner)
    models.save_provider({
        "id": "hosted", "type": "anthropic", "label": "Hosted model (not in use)",
        "enabled": False}, actor=owner)
    target = {"provider_id": "demo-local", "model": common.MODEL_NAME}
    for role in ("default", "cheap", "smart"):
        models.set_role(role, validate_target(target), [], actor=owner)

    # Memory: notes written by hand, then facts the owner confirmed.
    travel.back(8 * 24)
    for path, text in NOTES.items():
        memory.save_note(core, path, text, actor=owner)
        travel.at(travel.t + 7 * 60_000)
    for kind, text in FACTS:
        memory.remember(core, text, kind=kind, actor=owner, confirmed=True)
        travel.at(travel.t + 11 * 60_000)

    # Tasks: some finished over the week, the rest open with dates around today.
    svc = tasks_service(core)
    svc.clock = lambda: travel.t
    made = []
    for offset, title, area, repeat, notes, finished in TASKS:
        travel.back(7 * 24 - len(made) * 3)
        made.append((svc.create(title, actor=owner, notes=notes, area=area, repeat=repeat,
                                due=(today + timedelta(days=offset)).isoformat()), finished))
    for task, finished in made:
        if finished is not None:
            travel.back(finished)
            svc.complete(task["id"], actor=owner)
    # A task for an agent, and one pushed to later: the rows look lived in.
    travel.back(30)
    svc.push(made[7][0]["id"], today + timedelta(days=7), actor=owner)

    # Usage lines, as the models module prints them after an answer.
    for hours, caller, role, tokens_in, tokens_out, ms in USAGE:
        travel.back(hours)
        core.tape.print("model.called", {
            "provider_id": "demo-local", "provider_type": "openai_compatible",
            "model": common.MODEL_NAME, "role": role, "caller": caller, "duration_ms": ms,
            "input_tokens": tokens_in, "output_tokens": tokens_out, "local": True,
            "streamed": caller == "chat.reply"}, actor=f"system:{caller.split('.')[0]}")

    # Bench: a few decided earlier in the week, then what waits now.
    old = card(core, hours_ago=6 * 24, travel=travel, filed_by="agent:agent-a",
               kind="research.finding",
               source="Wren", title="Three quotes for the loft insulation",
               summary="One is a third cheaper and has good reviews.")
    travel.back(6 * 24 - 5)
    cards.decide(core, old, action="approve")
    old = card(core, hours_ago=4 * 24, travel=travel, filed_by="agent:agent-b",
               kind="letters.draft",
               source="Moss", title="Letter to the council about the pavement",
               summary="A short, polite letter with the photos attached.")
    travel.back(4 * 24 - 3)
    cards.decide(core, old, action="no", reason="I will phone them instead.")
    card(core, hours_ago=3 * 24, travel=travel, filed_by="agent:agent-c", kind="garden.done",
         source="Fern", lane="done", undo=True, title="Added the compost order to your list",
         summary="Order compost is on Tasks, due on Saturday.")
    card(core, hours_ago=26, travel=travel, filed_by="agent:agent-b", kind="letters.done",
         source="Moss", lane="done", title="Filed the water bill in the grey folder note",
         summary="Marked as paid in the Home note.")
    waiting = card(core, hours_ago=22, travel=travel, filed_by="agent:agent-c", kind="garden.plan",
                   source="Fern", title="Spring plan for the four raised beds",
                   summary="Tomatoes and beans in the sunny beds, salad and herbs in the others.",
                   body=("Beds 1 and 2 (full sun): tomatoes, then runner beans on the "
                         "fence.\n\nBeds 3 and 4: salad leaves, rocket and herbs. Sow every "
                         "three weeks from April.\n\nCompost first, in the second week of "
                         "March."))
    travel.back(21)
    cards.decide(core, waiting, action="not_yet", note="Ask me again after the boiler is done.")

    # What needs the owner now: filed over the last day.
    card(core, hours_ago=19, travel=travel, filed_by="agent:agent-a", kind="research.finding",
         source="Wren", title="Boiler service: two local engineers have a free morning this week",
         summary="Both are insured and have recent good reviews. One is cheaper by 15 pounds.",
         body=("I looked at the two engineers who serviced boilers on your street.\n\n"
               "Engineer A: Thursday morning, 70 pounds, 4.8 from 61 reviews.\n"
               "Engineer B: Friday morning, 85 pounds, 4.9 from 112 reviews.\n\n"
               "Neither asked for a deposit."),
         why="Your Home note says the boiler is serviced every autumn, in the morning.",
         details=[{"label": "Source", "text": "Public review sites, read today"},
                  {"label": "Cost to you", "text": "Nothing until you book"}])
    card(core, hours_ago=11, travel=travel, filed_by="agent:agent-b", kind="letters.draft",
         source="Moss", title="Reply to the school about the trip form",
         summary="A short reply that confirms attendance and the dietary note.",
         body=("Dear Mrs Example,\n\nThank you for the trip form. We confirm that both children "
               "will attend on the 14th. One has a nut allergy, as noted on the form.\n\n"
               "Kind regards,\nAlex"),
         why="The school asked for an answer by Friday. Nothing is sent until you approve.")
    card(core, hours_ago=7, travel=travel, filed_by="system:memory", kind="memory.fact",
         source="Memory", title="Remember: the cats are due their jabs in April?",
         summary="Found in the vet's email you saved to the Home note.",
         why="You have not confirmed this fact yet. Say no and it is forgotten.")
    card(core, hours_ago=5, travel=travel, filed_by="agent:agent-c", kind="garden.plan",
         source="Fern", title="Order seed potatoes and a bag of compost?",
         summary="About 24 pounds at the garden centre on Saturday, or delivered for 31.",
         body="Two kinds of seed potato are enough for one bed. I can add both to your list.")
    card(core, hours_ago=2, travel=travel, filed_by="agent:agent-a", kind="calendar.invite",
         source="Wren", title="Add the dentist to the calendar: Tuesday 10:00?",
         summary="The surgery's email offered Tuesday at 10:00 or Wednesday at 15:30.",
         why="Your preferences say calls and meetings before noon.")
    travel.back(0.5)
    core.tape.print("setting.updated", {"key": "wall.show_seconds", "value": False},
                    actor=owner)
    travel.restore()


def validate_target(target: dict):
    from jimmyhq.models import validate

    return validate.target(target, "The first choice")


# ----------------------------------------------------------------------------------------
# Step 2: with JimmyHQ running
# ----------------------------------------------------------------------------------------

CHAT = [
    ("Boiler and the week ahead",
     ["Is the boiler service due this month?", "What does my week look like?"]),
    ("Dinner this week", ["Three meal ideas from what is in the cupboard?"]),
]


def village(api: common.Api) -> None:
    for _, _, role, seed in AGENTS:
        out = api.post("/api/village/agents", {
            "role": role, "seed": seed, "local_only": True,
            "models": {"beat": {"role": "default"}, "night": {"role": "smart"}}})
        wait_for_command(api, out["command_id"])
    api.post("/api/village/priorities", {
        "title": "Get the boiler serviced before the cold weather",
        "why": "It is due this month and the engineers are booking up.",
        "agents": ["agent-a", "agent-b"]})
    api.post("/api/village/priorities", {
        "title": "Plan the spring garden",
        "why": "Seed potatoes and compost sell out in March.", "agents": ["agent-c"]})


def wait_for_command(api: common.Api, command_id: str, seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = api.get(f"/api/village/commands/{command_id}")
        if found["done"]:
            if not (found["result"] or {}).get("ok"):
                raise SystemExit(f"The village refused: {found['result']}")
            return
        time.sleep(0.5)
    raise SystemExit("The village process did not answer in time.")


def chat(api: common.Api) -> None:
    for title, messages in CHAT:
        conversation = api.post("/api/chat/conversations", {"title": title})["id"]
        for text in messages:
            stream_message(api, conversation, text)


def stream_message(api: common.Api, conversation: str, text: str) -> None:
    with api.http.stream("POST", f"/api/chat/conversations/{conversation}/messages",
                         json={"text": text}) as answer:
        if answer.status_code >= 400:
            answer.read()
            raise RuntimeError(f"chat answered {answer.status_code}: {answer.text[:200]}")
        for _ in answer.iter_lines():
            pass


def install_apps(api: common.Api) -> dict[str, str]:
    """Install each first-party app the way the Apps screen does: ask for the plan, then give
    the consent it shows. Returns the app ids installed."""
    done = {}
    for app_id in APPS:
        plan = api.post("/api/apps/_install/plan", {"example": app_id})
        name = plan["app"]["name"]
        api.post("/api/apps/_install/confirm", {
            "plan": plan["plan"], "fingerprint": plan["fingerprint"], "typed_name": name})
        done[app_id] = name
        print(f"installed {app_id}", flush=True)
    return done


def wait_for_programs(api: common.Api, ids: list[str], seconds: int = 240) -> None:
    deadline = time.time() + seconds
    waiting = set(ids)
    while waiting and time.time() < deadline:
        for app_id in sorted(waiting):
            health = api.get(f"/api/apps/{app_id}/health")
            if health.get("state") == "running":
                waiting.discard(app_id)
        time.sleep(2)
    if waiting:
        print(f"These programs were not answering in time: {sorted(waiting)}", flush=True)


def seed_apps(api: common.Api, port: int) -> list[str]:
    from datetime import UTC

    zone = ZoneInfo(TIMEZONE)
    now = datetime.now(UTC)
    today = now.astimezone(zone).date().isoformat()
    now_ms = int(now.timestamp() * 1000)

    def call_for(app_id: str):
        def call(name: str, /, **args):
            return api.app_call(app_id, name, **args)
        return call

    problems = []
    for app_id in APPS:
        module = importlib.import_module(f"demo_seed.{app_id}")
        for key, value in module.SETTINGS.items():
            api.put(f"/api/settings/app.{app_id}.{key}", {"value": value})
        kwargs = {}
        if "post" in module.seed.__code__.co_varnames[:module.seed.__code__.co_argcount]:
            kwargs["post"] = inbound_poster(api, app_id, port)
        try:
            module.seed(call_for(app_id), today, now_ms, **kwargs)
            print(f"filled {app_id}", flush=True)
        except Exception as error:  # noqa: BLE001 - one app must not stop the rest
            problems.append(f"{app_id}: {error}")
            print(f"could not fill {app_id}: {error}", flush=True)
    return problems


def inbound_poster(api: common.Api, app_id: str, port: int):
    """What a device would do: make a key for the app, open its door, and post to it."""
    import httpx

    made = api.post(f"/api/apps/{app_id}/inbound/keys", {"label": "Demo hub"})
    api.put(f"/api/apps/{app_id}/inbound/open", {"open": True})
    key = made["key"]

    def post(name: str, payload) -> None:
        for _ in range(6):
            answer = httpx.post(f"http://127.0.0.1:{port}/api/in/{app_id}/{name}",
                                headers={"Authorization": f"Bearer {key}"}, json=payload,
                                timeout=60)
            if answer.status_code != 429:  # the app's own limit: wait as a device is told to
                break
            time.sleep(int(answer.headers.get("Retry-After", "60")) + 1)
        if answer.status_code >= 400:
            raise RuntimeError(f"{app_id}/{name} answered {answer.status_code}")
    return post


# ----------------------------------------------------------------------------------------


def serve(data: Path, port: int) -> int:
    """Run JimmyHQ on the demo folder, with the demo model, until Ctrl-C."""
    with common.pretend_model(port + 1):
        proc = common.start_server(data, port, data.parent / f"{data.name}-server.log")
        print(f"Open http://127.0.0.1:{port} and press Ctrl-C here to stop.", flush=True)
        try:
            while proc.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            common.stop_server(proc)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--serve", action="store_true",
                        help="after filling, keep JimmyHQ and the demo model running")
    parser.add_argument("--serve-only", action="store_true",
                        help="run JimmyHQ and the demo model on a folder already filled")
    args = parser.parse_args()
    if args.serve_only:
        data, port = common.environment(fresh=False)
        return serve(data, port)
    data, port = common.environment(fresh=True)
    data.mkdir(parents=True, exist_ok=True)
    print(f"Filling {data} (port {port})", flush=True)

    # Step 1, JimmyHQ stopped.
    from jimmyhq import config
    from jimmyhq.core import open_core

    travel = TimeTravel()
    core = open_core(config.load())
    try:
        history(core, travel, port)
    finally:
        travel.restore()
        core.close()
    (data / common.MARKER).write_text(common.MARKER_TEXT, encoding="utf-8")

    # Step 2, JimmyHQ running.
    problems: list[str] = []
    logfile = data.parent / f"{data.name}-server.log"
    with common.pretend_model(port + 1):
        proc = common.start_server(data, port, logfile)
        try:
            api = common.Api(port)
            api.sign_in()
            village(api)
            installed = install_apps(api)
            wait_for_programs(api, list(installed))
            problems = seed_apps(api, port)
            chat(api)
            api.close()
        finally:
            common.stop_server(proc)

    # Step 3, JimmyHQ stopped again.
    core = open_core(config.load())
    try:
        from jimmyhq.village import record

        for agent_id, name, _, _ in AGENTS:
            record.named(core, agent_id, name)
    finally:
        core.close()

    print("\nNot filled, and why:")
    for line in not_seeded():
        print(f"  - {line}")
    if problems:
        print("\nProblems while filling apps:")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("\nDone. The owner's password is DEMO_PASSWORD in scripts/_demo_common.py.")
    return serve(data, port) if args.serve else 0


if __name__ == "__main__":
    sys.exit(main())
