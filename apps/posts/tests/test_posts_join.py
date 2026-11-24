"""Where the three parts of Posts meet: the agents' flow, the scheduler and the page.
Each test names a seam that must hold whichever part changes. Sample text is invented."""

from __future__ import annotations

import ast
import re

import pytest
from jimmyhq_app import CallError
from posts import config, credits, engagement, pictures, states
from posts_rig import APP_ROOT, ship
from posts_service import (
    ACCOUNTS,
    DAY,
    HOUR,
    KEY,
    Service,
    booked,
    created,
    no_answer,
    queued,
    reply,
    upstream_post,
)
from test_posts_agents import WREN, agents, review, run, submit, to_checker, to_writer, tool
from test_posts_pictures import OTHER, SCENE
from test_posts_pictures import through as pictures_through

SCOUT = "agent-scout"
ROLES = {"researcher": "Scout"}  # writing and checking stay the plain way


def socialbu(make_rig, **more):
    rig = make_rig(settings={"scheduler": "socialbu"}, secrets={"socialbu_key": KEY}, **more)
    rig.service = Service(rig.core)
    return rig


def went_out(rig):
    """A post booked on two accounts that went out, and reconcile has run since."""
    post = booked(rig)
    for (pid, aid), likes in zip(((500, 1001), (501, 1002)), (30, 11), strict=True):
        rig.service.on("GET", f"/posts/{pid}", upstream_post(
            pid, aid, published=True, insights=[{"type": "likes", "value": likes},
                                                {"type": "impressions", "value": 900}]))
    rig.clock.advance(DAY + 3_600_000)
    rig.core.run_job("reconcile")
    return rig.store.post(post["id"])


def well(rig, page_id):
    return rig.core.tool("what_did_well", {"page": page_id}, agent=SCOUT,
                         role="researcher")["shown"]


# --- (a) the researcher's evidence and the scheduler's figures ------------------------------


def test_the_researchers_what_did_well_reads_the_figures_the_scheduler_reported(make_rig):
    rig = socialbu(make_rig, roles=ROLES)
    post = went_out(rig)
    assert post["state"] == "went_out"
    shown = well(rig, post["page_id"])
    # The same numbers, the same ranking, as engagement keeps them: one path, not two.
    [entry] = engagement.what_did_well(rig.ctx, post["page_id"])["posts"]
    assert entry["interactions"] == 41.0
    assert f"post {post['id']}" in shown and "41 interactions" in shown
    assert "likes 41" in shown and "impressions 1800" in shown


def test_what_did_well_says_there_are_none_when_the_scheduler_offers_none(make_rig):
    practice = make_rig(roles=ROLES)
    page = practice.page()
    assert "scheduler in use gives no figures" in well(practice, page["id"])
    # A scheduler that gives figures, before any post went out, says none yet.
    rig = socialbu(make_rig, roles=ROLES)
    page = rig.page()
    assert engagement.NONE_YET in well(rig, page["id"])


# --- (b) a post made from pictures goes out with its pictures --------------------------------


def picture_post(rig, page, shots=(SCENE, OTHER)):
    """Pictures to an approved, queued post: looked at, captioned, checked, approved."""
    piece = pictures_through(rig, page, shots)
    assert piece["state"] == "ready", rig.store.turns(piece["id"])[-1]
    post = rig.store.post_of(piece["id"])
    [card] = [c for c in rig.cards_of("draft") if c["subject"].endswith(f"post:{post['id']}")]
    rig.core.decide(card["card_id"], "approve")
    return rig.store.post(post["id"])


def test_a_post_made_from_pictures_is_booked_with_each_of_its_pictures(make_rig):
    rig = socialbu(make_rig)
    post = picture_post(rig, rig.page(accounts=ACCOUNTS))
    refs = [p["ref"] for p in pictures.of_piece(rig.ctx, post["piece_id"])]
    assert post["state"] == "queued" and len(refs) == 2
    rig.service.on("POST", "/upload_media_by_url",
                   reply(200, {"success": True, "upload_token": "tok-1"}),
                   reply(200, {"success": True, "upload_token": "tok-2"}))
    rig.service.on("POST", "/posts", created((500, 1001), (501, 1002)))
    assert rig.core.call("post.book", {"id": post["id"]})["state"] == "booked"
    uploads = rig.service.sent("POST", "/upload_media_by_url")
    assert [u["url"]["$file"] for u in uploads] == refs  # each one, in the owner's order
    [sent] = rig.service.sent("POST", "/posts")
    assert sent["existing_attachments"] == [{"upload_token": "tok-1"},
                                            {"upload_token": "tok-2"}]


def test_a_picture_that_fails_to_upload_books_nothing(make_rig):
    rig = socialbu(make_rig)
    post = picture_post(rig, rig.page(accounts=ACCOUNTS))
    rig.service.on("POST", "/upload_media_by_url",
                   reply(200, {"success": True, "upload_token": "tok-1"}),
                   reply(422, {"message": "The file could not be read."}))
    after = rig.core.call("post.book", {"id": post["id"]})
    assert after["state"] == "queued" and "picture-2" in after["note"]
    assert "Nothing was booked" in after["note"]
    assert ("POST", "/posts") not in rig.service.asked()
    assert rig.store.upstream(post["id"]) == []


def test_practice_records_which_pictures_it_would_have_sent(make_rig):
    rig = make_rig()
    post = picture_post(rig, rig.page())
    assert post["state"] == "booked"  # Practice books as soon as it is approved
    [logged] = rig.store.all("SELECT * FROM practice_log WHERE post_id=?", (post["id"],))
    refs = [p["ref"] for p in pictures.of_piece(rig.ctx, post["piece_id"])]
    assert logged["would_send"]["pictures"] == [
        {"name": "picture-1.jpg", "ref": refs[0]}, {"name": "picture-2.jpg", "ref": refs[1]}]


# --- (c) the page shows what the agents and the scheduler added, each with its way out -------


def overview(rig):
    return rig.core.call("overview", {})


def piece_row(rig, piece_id):
    return next(p for p in overview(rig)["pieces"] if p["id"] == piece_id)


def post_row(rig, post_id):
    return next(p for p in overview(rig)["posts"] if p["id"] == post_id)


def test_the_overview_says_who_worked_on_a_piece_by_role(make_rig):
    rig, page = agents(make_rig)
    piece = to_checker(rig, page)
    assert review(rig, piece)["ok"]
    run(rig)
    assert piece_row(rig, piece["id"])["worked"] == (
        "Found by the researcher (Scout). Written by the writer (Wren). Checked by the "
        "checker (Kit).")


def test_a_stalled_agent_step_is_on_the_page_with_the_cards_choices_and_they_work(make_rig):
    rig, page = agents(make_rig)
    piece = to_writer(rig, page)
    for _ in range(2):  # late and reminded, then late again: the owner is told
        rig.clock.advance(25 * HOUR)
        rig.core.run_job("watch")
    notice = piece_row(rig, piece["id"])["notice"]
    assert notice["role"] == "writer" and "Stuck at writing" in notice["why"]
    assert [a["id"] for a in notice["actions"]] == ["wait", "finish_plain", "drop"]
    [card] = [c for c in rig.cards_of("notice") if c["subject"].endswith(
        f"work:{notice['work_id']}")]
    rig.core.call("work.choose", {"id": notice["work_id"], "action": "wait"})
    assert rig.store.piece(piece["id"])["state"] == "writing"
    assert piece_row(rig, piece["id"])["notice"] is None
    assert rig.core.cards[card["card_id"]].get("withdrawn")  # the Bench card went too


def test_a_spent_budget_is_on_the_page_and_finish_without_agents_finishes_it(make_rig):
    rig, page = agents(make_rig, agent_steps_per_piece=2)
    piece = to_writer(rig, page)  # propose was step 1
    assert tool(rig, "brief", {"piece": piece["id"]}, WREN, "writer")["ok"]  # step 2
    assert submit(rig, piece)["ok"]  # what there is may still be handed back
    run(rig, 2)
    notice = piece_row(rig, piece["id"])["notice"]
    assert "budget of 2 agent steps is spent" in notice["why"]
    assert [a["id"] for a in notice["actions"]] == ["finish_plain", "drop"]
    with pytest.raises(CallError, match="cannot choose 'wait'"):
        rig.core.call("work.choose", {"id": notice["work_id"], "action": "wait"})
    rig.core.call("work.choose", {"id": notice["work_id"], "action": "finish_plain"})
    rig.core.replies.append(ship())
    run(rig)
    assert rig.store.piece(piece["id"])["state"] == "ready"
    assert piece_row(rig, piece["id"])["notice"] is None


def test_a_story_told_as_the_owners_is_an_open_question_on_the_page(rig):
    page = rig.page()
    piece = rig.through(page, "Last year I finally installed panels on the shed, like "
                              "1,240 homes in the valley.",
                        claims=[{"text": "1,240 homes added panels", "sentences": [2]}])
    post = post_row(rig, rig.store.post_of(piece["id"])["id"])
    assert [a["check"] for a in post["asks"]] == ["lived"]
    assert post["asks"][0]["detail"].startswith("Did this happen?")
    assert post["questions"] == 1


def test_a_failed_post_offers_back_in_the_queue_or_decline_and_both_work(make_rig):
    rig = socialbu(make_rig)
    post = booked(rig)
    rig.service.on("GET", "/posts/500", upstream_post(500, 1001, error="Token expired."))
    rig.service.on("GET", "/posts/501", upstream_post(501, 1002))
    rig.clock.advance(31 * 60_000)
    rig.core.run_job("reconcile")
    trouble = post_row(rig, post["id"])["trouble"]
    assert trouble["kind"] == "failed" and "Token expired." in trouble["said"]
    assert [a["call"] for a in trouble["actions"]] == ["post.book_again", "post.cancel"]
    again = rig.core.call("post.book_again", {"id": post["id"]})
    assert again["state"] == "queued" and again["slot_ms"] and rig.service.calls[-1][0] == "GET"
    assert post_row(rig, post["id"])["trouble"] is None
    assert rig.store.upstream(post["id"], live=True) == []


def test_a_post_that_went_missing_can_be_declined_from_the_page(make_rig):
    rig = socialbu(make_rig)
    post = booked(rig)
    rig.store.move_post(post["id"], "missing", note="It is no longer on SocialBu.")
    assert post_row(rig, post["id"])["trouble"]["kind"] == "missing"
    assert rig.core.call("post.cancel", {"id": post["id"]})["state"] == "declined"


def test_no_answer_offers_i_checked_and_a_named_copy_offers_nothing_that_books_again(make_rig):
    rig = socialbu(make_rig)
    silent = queued(rig)
    rig.service.on("POST", "/posts", no_answer())
    rig.core.call("post.book", {"id": silent["id"]})
    trouble = post_row(rig, silent["id"])["trouble"]
    assert trouble["kind"] == "no_answer" and [a["call"] for a in trouble["actions"]] == [
        "post.clear"]
    rig.core.call("post.clear", {"id": silent["id"]})
    assert rig.store.post(silent["id"])["state"] == "queued"
    rig.core.call("post.cancel", {"id": silent["id"]})
    half = queued(rig, page=rig.store.page(silent["page_id"]))
    rig.service.script.clear()
    rig.service.on("POST", "/posts", created((555, 1001)))
    rig.core.call("post.book", {"id": half["id"]})
    trouble = post_row(rig, half["id"])["trouble"]
    assert trouble["kind"] == "partial" and trouble["actions"] == []


def test_a_post_out_of_step_offers_move_to_one_time(make_rig):
    rig = socialbu(make_rig)
    post = booked(rig)
    rig.service.on("GET", "/posts/500", upstream_post(500, 1001))
    rig.service.on("GET", "/posts/501", upstream_post(501, 1002,
                                                      publish_at="2027-01-16 09:00:00"))
    rig.clock.advance(31 * 60_000)
    rig.core.run_job("reconcile")
    trouble = post_row(rig, post["id"])["trouble"]
    assert trouble["kind"] == "out_of_step"
    assert [a["call"] for a in trouble["actions"]] == ["post.move", "post.cancel"]


# --- (d) the one table: every state reached, every state left or final ----------------------

START_OF = {"piece": "proposed", "post": "draft"}
TABLES = {"piece": states.PIECE, "post": states.POST}
FINAL = {"piece": {"closed", "dropped"}, "post": {"went_out", "declined"}}


def reachable(table, start):
    seen, todo = {start}, [start]
    while todo:
        for nxt in table[todo.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


@pytest.mark.parametrize("what", ["piece", "post"])
def test_walking_the_table_every_state_is_reached_and_every_one_can_end(what):
    table, start = TABLES[what], START_OF[what]
    assert reachable(table, start) == set(table), "a state nothing leads to"
    finals = {s for s, out in table.items() if not out - {s}}
    assert finals == FINAL[what], "a state with no way out that is not an end"
    for state in table:
        assert reachable(table, state) & FINAL[what], f"{state} can never end"


def _targets(expr: ast.expr) -> set[str]:
    """The states an argument can be: a literal, either side of ``a if x else b``, or any
    part of ``a or b``. A name or a lookup gives none (see ``BY_NAME``)."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.IfExp):
        return _targets(expr.body) | _targets(expr.orelse)
    if isinstance(expr, ast.BoolOp):
        return set().union(*(_targets(v) for v in expr.values))
    return set()


def _moves() -> dict[str, set[str]]:
    """Every state the code moves a piece or a post to, read from its source: the second
    argument of each ``move_piece`` and ``move_post`` call."""
    found: dict[str, set[str]] = {"piece": set(), "post": set()}
    for path in (APP_ROOT / "posts").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("move_piece", "move_post") and len(node.args) > 1):
                what = "piece" if node.func.attr == "move_piece" else "post"
                found[what] |= _targets(node.args[1])
    return found


# States the code moves to through a name, not a literal, and where.
BY_NAME = {
    "piece": {"reading", "analysing", "overlap_check",  # intake: the first step of a way in
              "writing", "captioning",  # actions.draft_anyway, by the way the piece came in
              "drafted", "in_check", "revising"},  # back from stalled: its came_from
    "post": {"draft"},  # plain._keep_post: a new revision of a draft is a draft again
}


@pytest.mark.parametrize("what", ["piece", "post"])
def test_every_change_of_state_the_code_makes_is_in_the_one_table(what):
    table = TABLES[what]
    moved = _moves()[what]
    assert moved <= set(table), f"moves to states the table does not know: {moved - set(table)}"
    # And every state the table can be moved to is one the code moves to.
    targets = {new for out in table.values() for new in out}
    assert targets - moved - BY_NAME[what] == set()


def test_no_state_is_written_except_through_the_table():
    pattern = re.compile(r"""["']state["']\s*:""")
    for path in (APP_ROOT / "posts").rglob("*.py"):
        if path.name == "store.py":
            continue  # move_piece and move_post themselves
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if pattern.search(line) and ('update("piece"' in line or 'update("post"' in line):
                raise AssertionError(f"{path.name}: {line.strip()}")


# --- the whole way, with agents and with pictures --------------------------------------------


def watched(rig) -> list[tuple[str, int, str]]:
    """Every change of state from here on, as (piece or post, id, new state)."""
    seen: list[tuple[str, int, str]] = []
    store = rig.store
    piece_was, post_was = store.move_piece, store.move_post

    def piece_moves(piece_id, new, **more):
        seen.append(("piece", piece_id, new))
        return piece_was(piece_id, new, **more)

    def post_moves(post_id, new, **more):
        seen.append(("post", post_id, new))
        return post_was(post_id, new, **more)
    store.move_piece, store.move_post = piece_moves, post_moves
    return seen


def approve_on_the_bench(rig, post_id):
    [card] = [c for c in rig.cards_of("draft") if c["subject"].endswith(f"post:{post_id}")]
    rig.core.decide(card["card_id"], "approve")


def reconcile_leaves_it(rig, post_id):
    before = (rig.store.post(post_id), rig.store.upstream(post_id),
              rig.store.attempts(post_id))
    rig.clock.advance(2 * HOUR)
    rig.core.run_job("reconcile")
    after = (rig.store.post(post_id), rig.store.upstream(post_id),
             rig.store.attempts(post_id))
    assert after == before


def test_a_pasted_link_goes_through_two_agents_and_the_bench_to_practice(make_rig):
    rig, page = agents(make_rig)
    seen = watched(rig)
    piece = rig.link(page)  # the owner pastes a link
    run(rig, 3)  # read, the overlap check, then the writing is given to an agent
    assert [s for k, i, s in seen if k == "piece"][:3] == ["reading", "overlap_check",
                                                           "writing"]
    turns = [t["step"] for t in rig.store.turns(piece["id"])]
    assert turns.index("overlap_check") < turns.index("writing")
    assert submit(rig, piece, agent="agent-b")["ok"]  # a draft with its claims
    post = rig.store.post_of(piece["id"])
    assert [c["sentences"] for c in post["claims"]] == [[2], [3]]
    assert {c["name"] for c in rig.store.checks(post["id"])} >= {"numbers", "lived", "links"}
    run(rig, 2)  # the full checks, then the review is given to the checker
    assert not review(rig, piece, agent="agent-b")["ok"]  # never by the one who wrote it
    assert review(rig, piece, agent="agent-c")["ok"]
    run(rig)
    assert rig.store.piece(piece["id"])["state"] == "ready"
    assert not rig.core.asked  # no plain model stood in for either agent
    approve_on_the_bench(rig, post["id"])
    post = rig.store.post(post["id"])
    assert [s for k, i, s in seen if k == "post"][-3:] == ["queued", "booking", "booked"]
    [logged] = rig.store.all("SELECT * FROM practice_log WHERE post_id=?", (post["id"],))
    assert logged["action"] == "book" and logged["would_send"]["text"].startswith("1,240")
    assert post["state"] == "booked" and post["slot_ms"] > rig.clock.now
    assert credits.line(rig.store, piece["id"]) == (
        "Found by you. Written by the writer (Wren). Checked by the checker (Kit).")
    reconcile_leaves_it(rig, post["id"])


def test_pictures_go_through_plain_steps_and_the_bench_to_practice(make_rig):
    rig = make_rig()
    seen = watched(rig)
    post = picture_post(rig, rig.page())
    piece_steps = [s for k, i, s in seen if k == "piece"]
    assert piece_steps[:3] == ["analysing", "overlap_check", "captioning"]
    assert [s for k, i, s in seen if k == "post"][-3:] == ["queued", "booking", "booked"]
    assert [a["role"] for a in rig.core.asked][-2:] == [
        config.defaults()["role_caption"], config.defaults()["role_check"]]
    [logged] = rig.store.all("SELECT * FROM practice_log WHERE post_id=?", (post["id"],))
    assert len(logged["would_send"]["pictures"]) == 2
    assert "Written the plain way" in credits.line(rig.store, post["piece_id"])
    reconcile_leaves_it(rig, post["id"])
