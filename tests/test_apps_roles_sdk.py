"""The SDK for an app that works with agents: roles, record tools, giving work and
hearing back, against the fake core an author tests with and against the real app API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from jimmyhq_app import App, CallError, RoleState, Work  # noqa: E402
from jimmyhq_app.testing import FakeCore  # noqa: E402

from apps_full_helpers import client_for  # noqa: E402
from apps_plugs_helpers import offer, running, speak  # noqa: E402
from apps_roles_helpers import agent, install_roles_app  # noqa: E402
from jimmyhq.apps import roles  # noqa: E402


def make(core: FakeCore) -> tuple[App, list]:
    heard: list = []
    app = App(transport=core)

    @app.record_tool("submit_draft")
    def submit(args, agent, role):
        app.tasks.create("Post it now")  # reaches outside: refused while recording
        return "kept"

    @app.tool("brief")
    def brief(args, agent):
        return f"brief for {agent}"

    @app.on_work
    def back(work: Work):
        heard.append(work)

    return app, heard


def test_an_app_gives_work_to_a_filled_role_and_hears_it_come_back():
    core = FakeCore("posts", roles={"writer": "Wren", "checker": None})
    app, heard = make(core)
    assert app.roles.get() == {"writer": RoleState(True, "Wren"),
                               "checker": RoleState(False, None)}
    work_id = app.give_work("writer", "Draft piece 12", subject="piece:12")
    core.end_work(work_id, "set_aside", note="The source will not load.")
    assert heard == [Work(id=work_id, role="writer", state="set_aside", subject="piece:12",
                          note="The source will not load.", agent_label="Wren")]


def test_work_for_an_empty_role_is_refused_so_the_app_falls_back():
    core = FakeCore("posts", roles={"writer": None})
    app, _ = make(core)
    with pytest.raises(CallError) as refused:
        app.work.give("writer", "Draft")
    assert refused.value.code == "not_found"


def test_a_record_tool_cannot_reach_outside_in_the_fake_core_either():
    core = FakeCore("posts", roles={"writer": "Wren"})
    make(core)
    with pytest.raises(CallError) as refused:
        core.tool("submit_draft", {}, role="writer")
    assert "record" in str(refused.value)
    assert core.tasks == []


def test_a_record_tool_cannot_search_read_embed_see_or_send_files_in_the_fake_core(tmp_path):
    core = FakeCore("posts", data_dir=tmp_path, roles={"writer": "Wren"})
    pic = "uploads/0f1e2d3c4b5a69788796a5b4c3d2e1f0.jpg"
    (tmp_path / "uploads").mkdir()
    (tmp_path / pic).write_bytes(b"\xff\xd8\xff")
    core.search_results["*"] = [{"title": "A", "url": "https://example.com/a"}]
    core.pages["https://example.com/a"] = "The page."
    app = App(transport=core)
    tries = {
        "web.search": lambda: app.web.search("news"),
        "web.read": lambda: app.web.read("https://example.com/a"),
        "model.embed": lambda: app.model.embed(["a draft"]),
        "model.ask": lambda: app.model.ask("What is this?", images=[pic]),
        "net.fetch file": lambda: app.net.fetch("https://api.example.com/up", method="PUT",
                                                file=pic),
        "net.fetch multipart": lambda: app.net.fetch(
            "https://api.example.com/up", method="POST",
            multipart=[{"name": "photo", "file": pic}]),
        "net.fetch json": lambda: app.net.fetch(
            "https://api.example.com/up", method="POST",
            json={"image": {"$file": pic, "as": "data_url"}}),
    }
    codes: dict[str, str] = {}

    @app.record_tool("submit_draft")
    def submit(args, agent, role):
        for name, attempt in tries.items():
            try:
                attempt()
                codes[name] = "done"
            except CallError as refused:
                codes[name] = refused.code
        return "kept"

    core.tool("submit_draft", {}, role="writer")
    assert codes == dict.fromkeys(tries, "not_allowed")
    assert core.searched == [] and core.read == [] and core.embedded == []
    assert core.asked == [] and core.fetched == []


def test_a_tool_hears_its_role_only_if_it_asks_for_it():
    core = FakeCore("posts")
    make(core)
    assert core.tool("brief", {}, agent="agent-a", role="writer")["result"] == \
        "brief for agent-a"


def test_the_sdk_gives_work_through_the_real_app_api(core, tmp_path):
    install_roles_app(core, tmp_path)
    agent(core, "agent-a", name="Wren")
    roles.fill(core, "posts", "writer", "agent-a")
    with client_for(core) as web:
        client = speak(web, core, "posts")
        offer(client, tools=["brief"])
        running(core, "posts")

        class Wire:
            def request(self, method, path, *, body=None, params=None, timeout=None):
                answer = (client.post(path, body) if method == "POST"
                          else client.get(path)).json()
                if not answer["ok"]:
                    raise CallError(answer["error"]["code"], answer["error"]["message"])
                return answer["result"]

        app = App(transport=Wire(), app_id="posts")
        assert app.roles.get()["writer"] == RoleState(True, "Wren")
        work_id = app.work.give("writer", "Draft piece 12")
        assert roles.read(core)["work"][work_id]["agent_id"] == "agent-a"
        with pytest.raises(CallError):
            app.work.give("checker", "Check it")
