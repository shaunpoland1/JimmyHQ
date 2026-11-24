"""The small rules of the node link, each a named function."""

from __future__ import annotations

import asyncio

import pytest

from jimmyhq.models import Target
from jimmyhq.nodes import protocol
from jimmyhq.nodes.protocol import DATA, END, ERROR, HEAD, FrameReader, frame
from jimmyhq.nodes.service import _QUEUE, Job, provider_id, roles_without

PRIVATE = ".".join(("10", "0", "0", "5"))
MESH = ".".join(("100", "64", "0", "7"))


def test_a_node_waits_longer_after_each_failure_and_never_more_than_the_longest():
    assert [protocol.reconnect_delay(n) for n in range(8)] == [0, 1, 2, 4, 8, 16, 30, 30]
    assert protocol.reconnect_delay(500) == 30
    assert protocol.reconnect_delay(4, longest=5) == 5


def test_a_private_address_is_loopback_a_private_range_or_a_mesh_range_and_nothing_public():
    for address in ("127.0.0.1", "::1", PRIVATE, MESH, "fd00::1", "169.254.1.1"):
        assert protocol.is_private_address(address), address
    for address in ("192.0.2.10", "2001:db8::1", "not-an-address", ""):
        assert not protocol.is_private_address(address), address


def test_plain_http_is_refused_to_a_hub_that_is_not_private_unless_the_owner_accepts_it():
    assert protocol.plain_http_refusal("http://127.0.0.1:8440", False) is None
    assert protocol.plain_http_refusal(f"http://{PRIVATE}:8440", False) is None
    assert protocol.plain_http_refusal("https://hub.example.com", False) is None
    refusal = protocol.plain_http_refusal("http://192.0.2.10:8440", False)
    assert refusal.startswith("Nothing was sent: 192.0.2.10 is not on a private network")
    assert "--allow-plain-http" in refusal and "https" in refusal
    assert protocol.plain_http_refusal("http://192.0.2.10:8440", True) is None


def test_a_hub_named_rather_than_numbered_is_judged_by_every_address_the_name_has():
    def mesh(_):
        return [MESH]

    def mixed(_):
        return [MESH, "192.0.2.10"]

    def unknown(_):
        raise OSError("no such name")

    assert protocol.plain_http_refusal("http://example-hub:8440", False, mesh) is None
    assert "not on a private network" in protocol.plain_http_refusal(
        "http://example-hub:8440", False, mixed)
    assert "could not be looked up" in protocol.plain_http_refusal(
        "http://example-hub:8440", False, unknown)


def test_a_hubs_address_is_kept_as_scheme_host_and_port_and_nothing_else():
    assert protocol.hub_address("192.0.2.10:8440") == "http://192.0.2.10:8440"
    assert protocol.hub_address(" https://hub.example.com/ ") == "https://hub.example.com"
    assert protocol.hub_address("http://[2001:db8::1]:8440") == "http://[2001:db8::1]:8440"
    for bad in ("ftp://hub.example.com", "http://user:pw@" + "hub.example.com",
                "http://hub.example.com/path", "http://hub.example.com?x=1", "http://",
                "not an address"):
        with pytest.raises(ValueError, match="The hub's address"):
            protocol.hub_address(bad)


def test_different_versions_earn_a_sentence_and_different_protocols_stop_the_work():
    assert protocol.skew("1.0", 1, "1.0", 1, "box") == (None, False)
    assert protocol.skew("1.0", 1, None, None, "box") == (None, False)  # not heard from yet
    sentence, blocked = protocol.skew("1.0", 1, "1.1", 1, "box")
    assert not blocked and "still work together" in sentence and "1.1" in sentence
    sentence, blocked = protocol.skew("1.0", 1, "2.0", 2, "box")
    assert blocked and sentence.endswith("update this machine.")
    sentence, blocked = protocol.skew("2.0", 2, "1.0", 1, "box")
    assert blocked and sentence.endswith("update box.")
    assert protocol.skew_on_node("1.0", 1, "1.0", 1) == (None, False)
    sentence, blocked = protocol.skew_on_node("2.0", 2, "1.0", 1)
    assert blocked and sentence.endswith("update this machine.")
    sentence, blocked = protocol.skew_on_node("1.0", 1, "1.1", 1)
    assert not blocked and "The hub runs JimmyHQ 1.0 and this machine runs 1.1" in sentence


def test_frames_are_put_back_together_however_the_bytes_are_cut():
    sent = (frame(HEAD, {"status": 200, "content_type": "text/event-stream"})
            + frame(DATA, b"abc") + frame(DATA, b"") + frame(END))
    for size in (1, 2, 5, 7, len(sent)):
        reader, got = FrameReader(), []
        for at in range(0, len(sent), size):
            got += reader.feed(sent[at:at + size])
        assert [k for k, _ in got] == [HEAD, DATA, DATA, END]
        assert got[1][1] == b"abc"


def test_a_frame_of_an_unknown_type_or_an_impossible_size_is_refused():
    with pytest.raises(ValueError):
        FrameReader().feed(b"X\x00\x00\x00\x00")
    with pytest.raises(ValueError):
        FrameReader().feed(b"D\xff\xff\xff\xff")
    with pytest.raises(ValueError):
        frame(DATA, b"x" * (protocol.MAX_FRAME + 1))


def test_only_whole_events_are_passed_on_from_a_stream():
    assert protocol.complete_events(b"data: 1\n\ndata: 2\n\ndata: {\"par") == (
        b"data: 1\n\ndata: 2\n\n", b"data: {\"par")
    assert protocol.complete_events(b"data: 1\r\n\r\ndata") == (b"data: 1\r\n\r\n", b"data")
    assert protocol.complete_events(b"data: unfinished") == (b"", b"data: unfinished")
    assert protocol.complete_events(b"") == (b"", b"")


def test_offered_names_are_short_plain_and_few():
    assert protocol.offer_names(["big", "small_2", "big"], "models") == ["big", "small_2"]
    for bad in (["Big"], ["a b"], [""], ["x" * 17], [3], "big", [f"m{i}" for i in range(17)]):
        with pytest.raises(ValueError):
            protocol.offer_names(bad, "models")


def test_the_only_places_a_model_job_may_ask_for_are_the_three_a_model_server_has():
    assert protocol.MODEL_PATHS == {"/models": "GET", "/chat/completions": "POST",
                                    "/embeddings": "POST"}


def test_a_provider_id_names_the_node_and_the_offer_and_cannot_be_read_two_ways():
    assert provider_id("gpu-box", "big") == "n-gpu-box_big"
    assert provider_id("a-b", "c") != provider_id("a", "b-c")
    assert len(provider_id("x" * 16, "y" * 16)) <= 40


def test_a_role_that_loses_a_nodes_model_keeps_its_other_choices_in_order():
    a, b, c = Target("n-box_big", "m"), Target("home", "m"), Target("cloud", "m")
    roles = {"default": [a, b, c], "cheap": [b, a], "smart": [a], "code": [c]}
    assert roles_without(roles, {"n-box_big"}) == {
        "default": [b, c], "cheap": [b], "smart": []}
    assert roles_without(roles, set()) == {}


def test_a_reply_is_taken_from_a_node_no_faster_than_its_reader_takes_it(core):
    """Back-pressure: with nobody reading, the hub stops taking the upload once
    the short queue is full, and takes the rest when the reader catches up."""
    from jimmyhq.nodes import hub

    async def run():
        h = hub(core)
        job = Job("node-1", "example-box", "model", "big", {})
        h._jobs[job.id] = job
        taken = 0

        async def upload():
            nonlocal taken
            yield frame(HEAD, {"status": 200, "content_type": "text/event-stream"})
            for index in range(200):
                yield frame(DATA, b"%d\n" % index)
                taken = index + 1
            yield frame(END)

        receiving = asyncio.create_task(h.receive(job, upload()))
        await asyncio.sleep(0.6)
        assert not receiving.done() and taken <= _QUEUE + 2  # held back, not buffered
        got = [chunk async for chunk in h.chunks(job)]
        assert await receiving is True and len(got) == 200 and taken == 200

    asyncio.run(run())


def test_a_reply_whose_reader_has_gone_tells_the_node_to_stop(core):
    from jimmyhq.nodes import hub

    async def run():
        h = hub(core)
        job = Job("node-1", "example-box", "model", "big", {})
        job.taken = True
        h._jobs[job.id] = job
        waiting = asyncio.Event()

        async def upload():
            yield frame(HEAD, {"status": 200, "content_type": "application/json"})
            waiting.set()
            await asyncio.sleep(30)  # a model still thinking
            yield frame(END)

        receiving = asyncio.create_task(h.receive(job, upload()))
        await waiting.wait()
        h.release(job)  # the caller gave up
        assert await asyncio.wait_for(receiving, 2) is False
        # The node hears about it at its next heartbeat and stops the model.
        assert h._dropped == {"node-1": {job.id}}

    asyncio.run(run())


def test_a_reply_that_ends_without_saying_it_was_done_means_the_node_went_away(core):
    from jimmyhq.nodes import hub
    from jimmyhq.nodes.service import NodeError

    async def run(frames, expect):
        h = hub(core)
        job = Job("node-1", "example-box", "model", "big", {})
        h._jobs[job.id] = job

        async def upload():
            for item in frames:
                yield item

        assert await h.receive(job, upload()) is True
        with pytest.raises(NodeError) as caught:
            await h.head(job)
            [chunk async for chunk in h.chunks(job)]
        assert expect in caught.value.detail

    head = frame(HEAD, {"status": 200, "content_type": "text/event-stream"})
    asyncio.run(run([head, frame(DATA, b"x")], "went away before it finished"))
    asyncio.run(run([], "went away before it finished"))
    asyncio.run(run([frame(ERROR, {"error": "unreachable"})], "could not reach its model server"))
    asyncio.run(run([frame(ERROR, {"error": "<script>"})], "could not be read"))
    asyncio.run(run([frame(DATA, b"x")], "could not be read"))  # a body before its head
    asyncio.run(run([b"garbage that is no frame"], "could not be read"))
