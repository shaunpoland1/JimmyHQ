"""The owner's SocialBu key goes in one place, the Authorization header JimmyHQ sends, and
nowhere else: not in a log line, a Tape event, an error sentence, a card or the app's own
records, even when the service writes it back in an answer."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from jimmyhq_app import CallError
from posts_service import (
    BASE,
    DAY,
    KEY,
    SLOT,
    Service,
    booked,
    no_answer,
    queued,
    reply,
    upstream_post,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 200
ECHO = f"Invalid token {KEY} for this request."


def test_the_key_never_appears_in_a_log_line_a_tape_event_an_error_sentence_or_the_apps_own_records(  # noqa: E501
        make_rig, caplog):
    caplog.set_level(logging.DEBUG)
    rig = make_rig(settings={"scheduler": "socialbu"}, secrets={"socialbu_key": KEY})
    rig.service = Service(rig.core)
    errors: list[str] = []

    def attempt(name, args):
        try:
            return rig.core.call(name, args)
        except CallError as error:
            errors.append(error.message)
            return None

    # a booking the service refuses, quoting the key back
    first = queued(rig)
    rig.service.on("POST", "/posts", reply(401, {"message": ECHO}))
    attempt("post.book", {"id": first["id"]})
    # a picture upload the service refuses the same way
    picture = rig.core.upload(PNG, "roof.png")
    rig.ctx.pictures_for = lambda ctx, post_id: [picture]
    rig.service.on("POST", "/upload_media_by_url", reply(400, {"message": ECHO}))
    attempt("post.book", {"id": first["id"]})
    rig.ctx.pictures_for = lambda ctx, post_id: []
    # a booked post: an edit, a move and a cancel refused with the key in the answer
    rig.service.script.clear()
    post = booked(rig, page=rig.store.page(first["page_id"]), url="https://news.example.com/b")
    for pid, aid in ((500, 1001), (501, 1002)):
        rig.service.on("GET", f"/posts/{pid}", upstream_post(pid, aid))
        rig.service.on("PATCH", f"/posts/{pid}", reply(422, {"message": ECHO}))
        rig.service.on("DELETE", f"/posts/{pid}", reply(403, {"errors": {"id": [ECHO]}}))
    attempt("post.edit", {"id": post["id"], "body": "New words for the post."})
    attempt("post.move", {"id": post["id"], "slot_ms": SLOT + DAY})
    attempt("post.cancel", {"id": post["id"]})
    # no answer at all, with the key in what the platform says
    rig.service.on("GET", "/posts/500", CallError("unavailable", ECHO))
    # reconcile: a failure the service reports in words that hold the key
    rig.service.on("GET", "/posts/501", upstream_post(501, 1002, error=ECHO))
    rig.clock.advance(31 * 60_000)
    rig.core.run_job("reconcile")
    rig.service.script.pop(("GET", f"{BASE}/posts/500"))
    rig.service.on("GET", "/posts/500", upstream_post(500, 1001, error=ECHO))
    rig.clock.advance(31 * 60_000)
    rig.core.run_job("reconcile")
    assert rig.store.post(post["id"])["state"] == "failed"
    assert errors  # refusals did reach the owner, in sentences

    # the key was sent where it belongs
    assert all(args["headers"]["Authorization"] == f"Bearer {KEY}"
               for _, _, args in rig.service.calls)
    secret = KEY.encode()
    # ... and nowhere else
    for path in Path(rig.core.data_dir).rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes(), path.name
    assert secret not in json.dumps(rig.core.events, default=str).encode()
    assert secret not in json.dumps(rig.core.cards, default=str).encode()
    assert secret not in json.dumps(rig.core.emitted, default=str).encode()
    assert secret not in "\n".join(errors).encode()
    assert secret not in caplog.text.encode()
    for row in rig.store.all("SELECT * FROM post") + rig.store.all("SELECT * FROM attempt"):
        assert KEY not in json.dumps(row, default=str)


@pytest.mark.parametrize("given", ["", "   "])
def test_an_empty_key_counts_as_no_key(make_rig, given):
    rig = make_rig(settings={"scheduler": "socialbu"}, secrets={"socialbu_key": given})
    rig.service = Service(rig.core)
    post = queued(rig)
    with pytest.raises(CallError, match="Posts has no SocialBu key"):
        rig.core.call("post.book", {"id": post["id"]})
    rig.service.on("POST", "/posts", no_answer())
    assert rig.service.calls == []
