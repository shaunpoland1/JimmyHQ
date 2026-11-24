"""A role's capability offers only that role's tools, and a ``record`` tool runs without
the ladder and leaves a receipt. From the attacker's side: a checker calling a writer's
tool, an agent given both roles of a pair by hand, a record tool reaching outside."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from apps_plugs_helpers import answer, client_for, handed, offer, running, speak
from apps_roles_helpers import agent, install_roles_app, kinds_printed
from jimmyhq.apps import inbox, roles
from jimmyhq.apps.plugs import tools
from jimmyhq.village import record as vrecord

ALL = ["brief", "publish", "review_packet", "submit_draft", "submit_review"]


@pytest.fixture
def web(core):
    with client_for(core) as c:
        yield c


@pytest.fixture
def posts(core, tmp_path, web):
    install_roles_app(core, tmp_path, permissions=(
        "agents.tools", "tasks.write", "model.ask", "bench.file", "net:api.example.com"))
    app = speak(web, core, "posts")
    offer(app, tools=ALL)
    running(core, "posts")
    agent(core, "agent-a")
    agent(core, "agent-b")
    return app


def village(core):
    return SimpleNamespace(core=core, tools=lambda: [])


def offered(core, held):
    return {t.name: t for t in asyncio.run(tools.offered(village(core), "agent-a", held))}


def tool(core, name):
    return {t.name: t for t in tools.tools_of(core, "posts")}[name]


def first_delivery(app, timeout_s=30.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = handed(app)
        if found:
            return found[0]
        time.sleep(0.05)
    raise AssertionError("no delivery came")


def in_thread(fn):
    out: dict = {}

    def go():
        try:
            out["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - the test looks at it
            out["error"] = exc

    thread = threading.Thread(target=go)
    thread.start()
    return thread, out


def test_a_roles_capability_offers_only_that_roles_tools(core, posts):
    found = offered(core, ["app.posts.checker"])
    assert sorted(found) == ["app_posts_review_packet", "app_posts_submit_review"]
    assert {t.capability for t in found.values()} == {"app.posts.checker"}
    assert sorted(offered(core, ["app.posts"])) == [f"app_posts_{n}" for n in ALL]


def test_a_checker_calling_a_writers_tool_is_refused_and_nothing_reaches_the_app(core, posts):
    roles.fill(core, "posts", "checker", "agent-a")
    with pytest.raises(tools.ToolRefused) as refused:
        asyncio.run(tools.run(core, tool(core, "submit_draft"), {}, "agent-a", "checker"))
    assert refused.value.rule == "role_tool_is_the_roles"
    assert "nothing was done" in refused.value.sentence
    assert inbox.pending(core, "posts") == 0


def test_a_role_tool_is_refused_once_the_role_is_taken_away(core, posts):
    roles.fill(core, "posts", "writer", "agent-a")
    [brief] = [t for t in offered(core, ["app.posts.writer"]).values()
               if t.name == "app_posts_brief"]
    roles.empty(core, "posts", "writer")
    ctx = SimpleNamespace(v=village(core), agent_id="agent-a", mode="work")
    from jimmyhq.village.rules import Refusal

    with pytest.raises(Refusal):
        asyncio.run(brief.run(ctx, {}))
    assert inbox.pending(core, "posts") == 0


def test_an_agent_given_both_roles_of_a_pair_by_hand_is_offered_neither(core, posts):
    assert offered(core, ["app.posts.writer", "app.posts.checker"]) == {}
    whole = offered(core, ["app.posts", "app.posts.writer", "app.posts.checker"])
    assert len(whole) == len(ALL)  # the whole-app capability keeps its meaning
    vrecord.configured(core, "agent-a", {"capabilities": ["app.posts.writer",
                                                         "app.posts.checker"]}, actor="owner")
    with pytest.raises(tools.ToolRefused):
        asyncio.run(tools.run(core, tool(core, "brief"), {}, "agent-a", "writer"))
    assert inbox.pending(core, "posts") == 0


def test_a_tool_delivery_says_which_agent_called_and_through_which_role(core, posts):
    roles.fill(core, "posts", "writer", "agent-a")
    thread, out = in_thread(lambda: asyncio.run(
        tools.run(core, tool(core, "brief"), {"piece": "12"}, "agent-a", "writer")))
    got = first_delivery(posts)
    assert got["data"] == {"name": "brief", "args": {"piece": "12"}, "agent": "agent-a",
                           "role": "writer", "effect": "read"}
    answer(posts, got["id"], {"result": "Here is the brief."})
    thread.join(30)
    assert out["value"].ok


def test_a_record_tool_runs_without_the_ladder_and_leaves_a_receipt(core, posts):
    roles.fill(core, "posts", "writer", "agent-a")
    thread, out = in_thread(lambda: asyncio.run(
        tools.run(core, tool(core, "submit_draft"), {"piece": "12"}, "agent-a", "writer")))
    got = first_delivery(posts)
    assert got["data"]["effect"] == "record"
    answer(posts, got["id"], {"result": "Draft kept."})
    thread.join(30)
    assert out["value"].ok
    [receipt] = kinds_printed(core, "apps.tool_recorded")
    assert receipt.payload == {"app_id": "posts", "tool": "submit_draft", "agent_id": "agent-a",
                               "role": "writer", "ok": True}
    assert receipt.actor == "agent:agent-a"
    assert kinds_printed(core, "ladder.set") == []


def test_a_tool_that_sends_still_starts_at_approve_through_a_role(core, posts):
    with pytest.raises(tools.ToolRefused) as refused:
        asyncio.run(tools.run(core, tool(core, "publish"), {}, "agent-a"))
    assert refused.value.rule == "app_tool_starts_at_approve"


def test_a_record_tool_cannot_reach_outside_the_app_while_it_runs(core, posts):
    roles.fill(core, "posts", "writer", "agent-a")
    thread, out = in_thread(lambda: asyncio.run(
        tools.run(core, tool(core, "submit_draft"), {}, "agent-a", "writer")))
    got = first_delivery(posts)
    for call, args in (("tasks.create", {"title": "Post it now"}),
                       ("work.give", {"role": "checker", "title": "Check"}),
                       ("net.fetch", {"url": "https://api.example.com/post"}),
                       ("model.ask", {"prompt": "hi"}),
                       ("bench.file", {"title": "Approve"})):
        error = posts.refused(call, args)
        assert error["code"] == "not_allowed", (call, error)
        assert "\"record\"" in error["message"] and "did nothing" in error["message"]
    posts.ok("storage.set", {"key": "draft", "value": "kept"})  # its own data: allowed
    answer(posts, got["id"], {"result": "Draft kept."})
    thread.join(30)
    assert posts.ok("tasks.create", {"title": "A task after the tool"})["title"]
    assert [t["title"] for t in _tasks(core)] == ["A task after the tool"]


# The calls from main that reach outside: the web, embeddings, a model that sees pictures,
# and every form of a request whose body JimmyHQ builds from the app's own files.
REACHING_OUT = (
    ("web.search", {"query": "today's news"}),
    ("web.read", {"url": "https://example.com/article"}),
    ("model.embed", {"texts": ["a draft"]}),
    ("model.ask", {"prompt": "What is in this picture?",
                   "images": ["uploads/0f1e2d3c4b5a69788796a5b4c3d2e1f0.jpg"]}),
    ("net.fetch", {"url": "https://api.example.com/up", "method": "PUT",
                   "file": "uploads/0f1e2d3c4b5a69788796a5b4c3d2e1f0.jpg"}),
    ("net.fetch", {"url": "https://api.example.com/up", "method": "POST",
                   "multipart": [{"name": "caption", "value": "hi"},
                                 {"name": "photo",
                                  "file": "uploads/0f1e2d3c4b5a69788796a5b4c3d2e1f0.jpg"}]}),
    ("net.fetch", {"url": "https://api.example.com/up", "method": "POST",
                   "json": {"image": {"$file": "uploads/0f1e2d3c4b5a69788796a5b4c3d2e1f0.jpg",
                                      "as": "data_url"}}}),
)


@pytest.fixture
def posts_on_the_web(core, tmp_path, web):
    install_roles_app(core, tmp_path, permissions=(
        "agents.tools", "model.ask", "web", "net:api.example.com"))
    app = speak(web, core, "posts")
    offer(app, tools=ALL)
    running(core, "posts")
    agent(core, "agent-a")
    return app


def test_a_record_tool_cannot_search_read_embed_see_or_send_files_while_it_runs(
        core, posts_on_the_web):
    from jimmyhq.apps import bridge, record_effect

    roles.fill(core, "posts", "writer", "agent-a")
    thread, out = in_thread(lambda: asyncio.run(
        tools.run(core, tool(core, "submit_draft"), {}, "agent-a", "writer")))
    got = first_delivery(posts_on_the_web)
    for call, args in REACHING_OUT:
        error = posts_on_the_web.refused(call, args)  # from the app's program
        assert error["code"] == "not_allowed", (call, error)
        assert "\"record\"" in error["message"] and "did nothing" in error["message"]
        with pytest.raises(bridge.BridgeError) as page:  # and from its page
            bridge.admit(core, "posts", call)
        assert page.value.code == "not_allowed" and "\"record\"" in page.value.message
    answer(posts_on_the_web, got["id"], {"result": "Draft kept."})
    thread.join(30)
    for call, _ in REACHING_OUT:  # once the tool is answered, the guard lets them through
        record_effect.check_outside_call(core, "posts", call)


def _tasks(core):
    from jimmyhq import tasks

    return tasks.service(core).list_open()


def test_a_reading_tool_in_hand_does_not_hold_back_the_apps_other_calls(core, posts):
    thread, out = in_thread(lambda: asyncio.run(
        tools.run(core, tool(core, "brief"), {}, "agent-a")))
    got = first_delivery(posts)
    assert posts.ok("tasks.create", {"title": "Still allowed"})["title"] == "Still allowed"
    answer(posts, got["id"], {"result": "ok"})
    thread.join(30)


def test_the_village_screen_lists_each_role_as_its_own_capability(core, posts):
    found = {c["id"]: c for c in tools.capabilities(core)}
    writer = found["app.posts.writer"]
    assert writer["label"] == "Posts: Writer"
    assert writer["tools"] == ["app_posts_brief", "app_posts_submit_draft"]
    assert "Checker" in writer["description"] and "neither" in writer["description"]
    assert "never changes a level" in writer["description"]
    assert "app.posts" in found
