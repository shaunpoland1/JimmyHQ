"""A hub and a node end to end: join, connect, offer a model server, call it
through the Brain (in one piece and streamed), and leave. Both sides are real,
on loopback ports the system chose; the model server on the node is a fake."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from jimmyhq import __version__
from jimmyhq.models import ModelError, Target, brain
from jimmyhq.nodes import agent, hub, protocol
from jimmyhq.nodes import service as hub_module
from jimmyhq.nodes.service import provider_id
from nodes_helpers import TEXT, FakeModels, HubUnderTest, NodeUnderTest, Served, until

PID = provider_id("example-box", "big")


@pytest.fixture
def the_hub(tmp_path):
    made = HubUnderTest(tmp_path / "hub")
    yield made
    made.stop()


@pytest.fixture
def models():
    fake = FakeModels()
    served = Served(fake.app).start()
    fake.url = f"{served.url}/v1"
    yield fake
    fake.release.set()
    served.stop()


@pytest.fixture
def node(tmp_path, the_hub, models):
    """A node that has joined, offers the fake model server as "big", and is connected."""
    made = NodeUnderTest(tmp_path / "node")
    made.join(the_hub.url, the_hub.code())
    agent.offer_model(made.store, "big", models.url)
    made.start().connected()
    until(lambda: brain(the_hub.core).provider(PID), what="the node's model to be offered")
    brain(the_hub.core).set_role("default", Target(PID, "model-a"), actor="owner")
    yield made
    made.stop()


def chat(core, **options):
    return asyncio.run(brain(core).chat(
        [{"role": "user", "content": "Hi"}], actor="owner", caller="test.nodes", **options))


def stream(core, seen: list, **options):
    async def run():
        async for item in brain(core).stream(
                [{"role": "user", "content": "Hi"}], actor="owner", caller="test.nodes",
                **options):
            seen.append(item)
    asyncio.run(run())


def kinds(core, kind):
    return [e for e in core.tape.latest(limit=200) if e.kind == kind]


def test_a_machine_joins_with_a_code_and_the_hub_records_it_without_an_address(
        tmp_path, the_hub):
    node = NodeUnderTest(tmp_path / "node")
    said = node.join(the_hub.url, the_hub.code())
    assert "joined" in said and "example-box" in said
    assert node.store.token.startswith("jhqn_")
    assert not (tmp_path / "node" / "tape.db").exists()  # no copy of the hub's data
    [joined] = kinds(the_hub.core, "node.joined")
    assert joined.payload == {"node_id": node.store.read()["node_id"], "name": "example-box",
                              "version": __version__}
    assert "127.0.0.1" not in str(joined.payload) and node.store.token not in str(joined)
    [listed] = the_hub.nodes()
    assert listed["name"] == "example-box" and listed["connected"] is False


def test_a_nodes_model_server_becomes_a_local_provider_named_for_the_node(the_hub, node):
    provider = brain(the_hub.core).provider(PID)
    assert provider.label == "big on example-box" and provider.is_local
    assert provider.type == "openai_compatible"
    [offered] = kinds(the_hub.core, "node.offered")
    assert offered.payload["models"] == ["big"] and offered.payload["offers"] == ["models"]
    assert "http" not in str(offered.payload)  # the node's own addresses never reach the Tape
    [listed] = the_hub.nodes()
    assert listed["connected"] and listed["offers"] == ["models"]
    assert listed["models"] == [{"name": "big", "provider_id": PID, "roles": ["default"]}]


def test_a_model_call_reaches_the_nodes_model_server_and_comes_back_whole(
        the_hub, node, models):
    reply = chat(the_hub.core)
    assert reply.text == TEXT and reply.provider_id == PID and reply.is_local
    assert (reply.input_tokens, reply.output_tokens) == (7, 4)
    method, path, body = models.asked[-1]
    assert (method, path) == ("POST", "/chat/completions")
    assert body["model"] == "model-a" and body["messages"][-1]["content"] == "Hi"


def test_a_streamed_reply_arrives_piece_by_piece_while_the_model_is_still_writing(
        the_hub, node, models):
    models.hold = True
    seen: list = []
    worker = threading.Thread(target=stream, args=(the_hub.core, seen), daemon=True)
    worker.start()
    # The first piece is in the caller's hands while the model server is held.
    until(lambda: seen, what="the first piece")
    assert seen == ["Hello"] and not models.release.is_set()
    models.release.set()
    worker.join(10)
    assert "".join(s for s in seen if isinstance(s, str)) == TEXT
    assert seen[-1].text == TEXT and seen[-1].is_local


def test_a_request_that_must_stay_local_may_use_a_model_on_a_node(the_hub, node):
    assert chat(the_hub.core, local_only=True).text == TEXT


def test_embeddings_and_the_model_list_are_relayed_too(the_hub, node, models):
    listed = asyncio.run(brain(the_hub.core).list_models(PID, refresh=True))
    assert listed["models"] == ["model-a", "model-b"]
    brain(the_hub.core).set_role("embeddings", Target(PID, "model-a"), actor="owner")
    made = asyncio.run(brain(the_hub.core).embed(["a", "b"], actor="owner", caller="test.nodes"))
    assert made.vectors == [[0.5, 0.0], [0.5, 1.0]] and made.is_local


def test_a_nodes_health_follows_the_node(the_hub, node):
    health = {p["id"]: p for p in asyncio.run(brain(the_hub.core).health())["providers"]}
    assert health[PID]["ok"] is True
    node.stop()
    the_hub.core.metrics.forget("node:example-box")  # as if it had not reported in for a while
    brain(the_hub.core)._state.forget(PID)
    health = {p["id"]: p for p in asyncio.run(brain(the_hub.core).health())["providers"]}
    assert health[PID]["ok"] is False
    assert "The node example-box is not connected" in health[PID]["error"]


def test_a_node_that_drops_mid_reply_fails_the_call_with_a_sentence_saying_it_went_away(
        the_hub, node, models):
    models.hold = True
    seen: list = []
    failed: list = []

    def run():
        try:
            stream(the_hub.core, seen)
        except ModelError as exc:
            failed.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    until(lambda: seen, what="the first piece")
    node.stop()  # the node process goes away with the answer half written
    worker.join(10)
    [error] = failed
    assert "The node example-box went away before it finished answering" in error.message
    assert seen == ["Hello"]
    [receipt] = kinds(the_hub.core, "model.failed")
    assert receipt.payload["provider_id"] == PID and receipt.payload["reason"] == "unavailable"


def test_a_removed_node_is_cut_off_mid_stream_and_its_token_stops_working(
        the_hub, node, models):
    brain(the_hub.core).set_role("cheap", Target(PID, "model-b"), actor="owner")
    models.hold = True
    seen: list = []
    failed: list = []

    def run():
        try:
            stream(the_hub.core, seen)
        except ModelError as exc:
            failed.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    until(lambda: seen, what="the first piece")
    node_id = the_hub.nodes()[0]["id"]
    token = node.store.token
    answer = the_hub.owner.delete(f"/api/nodes/{node_id}").json()
    assert answer["roles_changed"] == ["cheap", "default"]
    worker.join(10)
    [error] = failed
    assert "The node example-box was removed from this machine" in error.message
    # The token is dead at once, the node says so and stops, and nothing is left behind.
    until(lambda: node.exit == 3, what="the node to stop")
    assert "was removed from the hub" in node.agent.said[-1] and node.store.token is None
    refused = httpx.post(f"{the_hub.url}{protocol.LINK}/heartbeat", json={},
                         headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code in (401, 404)
    assert the_hub.nodes() == [] and brain(the_hub.core).provider(PID) is None
    assert brain(the_hub.core).config().roles == {}
    [left] = kinds(the_hub.core, "node.left")
    assert left.payload["reason"] == "removed" and left.actor == "owner"
    assert all(p["name"] != "node:example-box"
               for p in the_hub.owner.get("/api/status").json()["processes"])


def test_a_node_leaves_by_itself_and_the_hub_forgets_it(the_hub, node):
    node.stop()
    said = asyncio.run(agent.leave(node.store))
    assert "the hub has been told" in said and node.store.token is None
    assert the_hub.nodes() == [] and brain(the_hub.core).provider(PID) is None
    [left] = kinds(the_hub.core, "node.left")
    assert left.payload["reason"] == "left" and left.actor == "node:example-box"
    with pytest.raises(ModelError):
        chat(the_hub.core)


def test_a_model_server_that_is_down_on_the_node_says_so_and_the_next_model_is_tried(
        the_hub, node, models):
    agent.offer_model(node.store, "spare", "http://127.0.0.1:9/v1")  # nothing listens there
    spare = provider_id("example-box", "spare")
    until(lambda: brain(the_hub.core).provider(spare), what="the second offer")
    brain(the_hub.core).set_role("default", Target(spare, "model-a"), [Target(PID, "model-a")],
                                 actor="owner")
    reply = chat(the_hub.core)
    assert reply.text == TEXT and reply.fallback_from == f"{spare}/model-a"
    [failed] = kinds(the_hub.core, "model.failed")
    assert failed.payload["provider_id"] == spare
    with pytest.raises(ModelError) as caught:
        chat(the_hub.core, no_fallback=True)
    assert "could not reach its model server for spare" in caught.value.message


def test_an_offer_taken_back_on_the_node_removes_the_provider_and_moves_the_role_on(
        the_hub, node, models):
    agent.offer_model(node.store, "spare", models.url)
    spare = provider_id("example-box", "spare")
    until(lambda: brain(the_hub.core).provider(spare), what="the second offer")
    brain(the_hub.core).set_role("default", Target(PID, "model-a"), [Target(spare, "model-b")],
                                 actor="owner")
    agent.remove_model(node.store, "big")
    until(lambda: brain(the_hub.core).provider(PID) is None, what="the offer to go")
    [target] = brain(the_hub.core).config().roles["default"]
    assert target == Target(spare, "model-b")


def test_the_connected_node_shows_in_the_hubs_process_status(the_hub, node):
    processes = {p["name"]: p for p in the_hub.owner.get("/api/status").json()["processes"]}
    assert processes["node:example-box"]["ok"] is True


def test_a_node_reconnects_with_back_off_after_the_hub_restarts(tmp_path, models):
    first = HubUnderTest(tmp_path / "hub")
    node = NodeUnderTest(tmp_path / "node", delay=lambda failures: min(0.05 * failures, 0.3))
    try:
        node.join(first.url, first.code())
        agent.offer_model(node.store, "big", models.url)
        node.start().connected()
        until(lambda: brain(first.core).provider(PID), what="the offer")
        brain(first.core).set_role("default", Target(PID, "model-a"), actor="owner")
        port = first.port
        first.stop()
        until(lambda: len(node.agent.waits) >= 3, what="the node to retry")
        assert node.agent.state == "waiting"
        assert "cannot be reached" in node.agent.said[-1]
        waits = node.agent.waits[:3]
        assert waits == sorted(waits) and waits[0] < waits[2]  # each wait longer than the last
        second = HubUnderTest(tmp_path / "hub", port=port)
        try:
            until(lambda: node.agent.state == "connected" and node.agent.failures == 0,
                  what="the node to reconnect")
            assert node.agent.connects == 2
            until(lambda: second.nodes()[0]["connected"], what="the hub to see the node")
            assert chat(second.core).text == TEXT
        finally:
            second.stop()
    finally:
        node.stop()


def test_a_different_version_with_the_same_protocol_is_said_on_both_sides_and_still_works(
        tmp_path, the_hub, models):
    node = NodeUnderTest(tmp_path / "node", version="9.9.9")
    node.join(the_hub.url, the_hub.code())
    agent.offer_model(node.store, "big", models.url)
    node.start().connected()
    try:
        until(lambda: brain(the_hub.core).provider(PID), what="the offer")
        brain(the_hub.core).set_role("default", Target(PID, "model-a"), actor="owner")
        [listed] = the_hub.nodes()
        assert listed["blocked"] is False and listed["version"] == "9.9.9"
        assert f"This machine runs JimmyHQ {__version__} and example-box runs 9.9.9" \
            in listed["skew"]
        assert any(f"The hub runs JimmyHQ {__version__} and this machine runs 9.9.9" in s
                   and "still work together" in s for s in node.agent.said)
        assert chat(the_hub.core).text == TEXT
    finally:
        node.stop()


def test_a_different_protocol_stops_the_work_and_is_said_on_both_sides(
        tmp_path, the_hub, models):
    node = NodeUnderTest(tmp_path / "node", version="9.9.9",
                         protocol_number=protocol.PROTOCOL + 1)
    node.join(the_hub.url, the_hub.code())
    agent.offer_model(node.store, "big", models.url)
    node.start().connected()
    try:
        until(lambda: brain(the_hub.core).provider(PID), what="the offer")
        brain(the_hub.core).set_role("default", Target(PID, "model-a"), actor="owner")
        [listed] = the_hub.nodes()
        assert listed["blocked"] is True and "cannot work together" in listed["skew"]
        assert "update this machine" in listed["skew"]  # the hub is the older one
        assert node.agent.blocked
        assert any("cannot work together" in s and "update the hub" in s
                   for s in node.agent.said)
        with pytest.raises(ModelError) as caught:
            chat(the_hub.core)
        assert "cannot work together" in caught.value.message
        assert models.asked == []  # nothing was sent to the node's model server
    finally:
        node.stop()


def test_a_job_kind_the_node_has_no_handler_for_is_refused_with_a_sentence(the_hub, node):
    """The seam for a browser on a node: a job of another kind travels the same way."""
    node_id = the_hub.nodes()[0]["id"]
    h = hub(the_hub.core)

    async def ask():
        job = hub_module.Job(node_id, "example-box", "browser", "main", {"call": {"op": "open"}})
        h._jobs[job.id] = job
        h._queues.setdefault(node_id, asyncio.Queue()).put_nowait(job)
        return await h.head(job)

    loop = the_hub.served.server.servers[0].get_loop()
    future = asyncio.run_coroutine_threadsafe(ask(), loop)
    with pytest.raises(hub_module.NodeError) as caught:
        future.result(10)
    assert "cannot do that kind of work yet" in caught.value.detail
