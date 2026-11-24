"""The Bench over HTTP: who may do what, and what odd input gets back."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jimmyhq.ids import now_ms
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
CARD = {"kind": "village.finding", "source": "Village bench", "title": "A finding",
        "summary": "One line", "body": "The whole story."}
MISSING = "01JABCDEFGHJKMNPQRSTVWXYZ0"
HOUR = 60 * 60 * 1000


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    r = client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD})
    assert r.status_code == 200
    return client


def click(action, **more):
    """What the owner's click sends for a card nobody has updated since it was read."""
    return {"action": action, "seen_updates": 0, **more}


def token(core, name, *scopes):
    return {"authorization": f"Bearer {core.auth.create_token(name, list(scopes))[1]}"}


@pytest.fixture
def agent(owner, core):
    """A client acting as a write token, with no owner session."""
    headers = token(core, "scout", "read", "write")
    with TestClient(owner.app, base_url="http://testserver", headers=headers) as c:
        yield c


def test_the_bench_needs_someone_signed_in(client):
    for path in ("/api/bench", f"/api/bench/{MISSING}", "/api/bench/ladder"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/bench", json=CARD).status_code == 401


def test_an_empty_bench_says_so_plainly(owner):
    body = owner.get("/api/bench").json()
    assert body["lanes"] == {"needs": [], "done": [], "waiting": []}
    assert body["counts"] == {"needs": 0, "done": 0, "waiting": 0}
    assert body["needs_by_kind"] == {} and body["needs_by_source"] == {}
    assert body["oldest_needs_ms"] is None and body["recent"] == []


def test_a_card_can_be_filed_listed_read_and_approved(owner):
    filed = owner.post("/api/bench", json=CARD)
    assert filed.status_code == 201
    card = filed.json()
    assert card["filed_by"] == "owner" and card["body"] == "The whole story."
    listed = owner.get("/api/bench").json()
    assert [c["id"] for c in listed["lanes"]["needs"]] == [card["id"]]
    assert "body" not in listed["lanes"]["needs"][0]
    assert listed["needs_by_kind"] == {"village.finding": 1}
    assert listed["needs_by_source"] == {"Village bench": 1}
    decided = owner.post(f"/api/bench/{card['id']}/decide", json=click("approve"))
    assert decided.status_code == 200 and decided.json()["status"] == "decided"
    assert owner.get("/api/bench").json()["counts"]["needs"] == 0
    assert owner.get(f"/api/bench/{card['id']}").json()["decision"]["action"] == "approve"


def test_a_token_files_as_itself_and_cannot_claim_to_be_someone_else(agent, core):
    card = agent.post("/api/bench", json=CARD).json()
    assert card["filed_by"] == "token:scout"
    assert agent.post("/api/bench", json={**CARD, "filed_by": "token:scout"}).status_code == 201
    for claimed in ("owner", "token:other", "agent:scout"):
        r = agent.post("/api/bench", json={**CARD, "filed_by": claimed})
        assert r.status_code == 403 and "as itself" in r.json()["detail"]
    row = {"actor": "token:other", "tool": "posts", "action": "publish", "category": "publish"}
    r = agent.post("/api/bench", json={**CARD, "ladder": row})
    assert r.status_code == 403 and "its own actions" in r.json()["detail"]
    assert len(core.tape.after(0, kind="bench.filed")) == 2


def test_a_read_token_can_look_but_not_file(owner, core):
    card = owner.post("/api/bench", json=CARD).json()
    headers = token(core, "wall", "read")
    owner.cookies.clear()
    assert owner.get("/api/bench", headers=headers).json()["counts"]["needs"] == 1
    assert owner.get(f"/api/bench/{card['id']}", headers=headers).status_code == 200
    assert owner.post("/api/bench", json=CARD, headers=headers).status_code == 403
    r = owner.post(f"/api/bench/{card['id']}/update", json={"note": "Hi"}, headers=headers)
    assert r.status_code == 403


def test_a_token_can_never_decide_snooze_or_unsnooze(agent, core):
    card = agent.post("/api/bench", json=CARD).json()
    until = now_ms() + HOUR
    for path, body in (("decide", click("approve")), ("snooze", {"until": until}),
                       ("unsnooze", None)):
        r = agent.post(f"/api/bench/{card['id']}/{path}", json=body)
        assert r.status_code == 403, path
        assert r.json()["detail"] == "Only the signed-in owner can do this."
    assert [e.kind for e in core.tape.after(0) if e.kind.startswith("bench.")] == ["bench.filed"]


def test_deciding_twice_is_a_409(owner):
    card = owner.post("/api/bench", json=CARD).json()
    first = owner.post(f"/api/bench/{card['id']}/decide", json=click("approve"))
    assert first.status_code == 200
    again = owner.post(f"/api/bench/{card['id']}/decide", json=click("approve"))
    assert again.status_code == 409 and "already closed" in again.json()["detail"]


def test_a_no_without_a_reason_is_a_422_that_says_a_reason_is_needed(owner):
    card = owner.post("/api/bench", json=CARD).json()
    r = owner.post(f"/api/bench/{card['id']}/decide", json=click("no", reason=" "))
    assert r.status_code == 422 and "reason is needed" in r.json()["detail"]
    r = owner.post(f"/api/bench/{card['id']}/decide",
                   json=click("no", reason="Wrong tone."))
    assert r.status_code == 200 and r.json()["decision"]["reason"] == "Wrong tone."


def test_an_action_the_card_does_not_have_is_a_422(owner):
    card = owner.post("/api/bench", json=CARD).json()
    r = owner.post(f"/api/bench/{card['id']}/decide", json=click("launch"))
    assert r.status_code == 422 and "approve, not_yet, no" in r.json()["detail"]


def test_snooze_and_unsnooze_move_a_card_out_of_needs_and_back(owner):
    card = owner.post("/api/bench", json=CARD).json()
    until = now_ms() + HOUR
    snoozed = owner.post(f"/api/bench/{card['id']}/snooze", json={"until": until})
    assert snoozed.status_code == 200 and snoozed.json()["snoozed_until"] == until
    body = owner.get("/api/bench").json()
    assert body["counts"] == {"needs": 0, "done": 0, "waiting": 1}
    assert body["needs_by_kind"] == {} and body["oldest_needs_ms"] is None
    assert owner.post(f"/api/bench/{card['id']}/unsnooze").json()["snoozed"] is False
    assert owner.get("/api/bench").json()["counts"]["needs"] == 1
    assert owner.post(f"/api/bench/{card['id']}/snooze").json()["snoozed"] is True  # default length


def test_a_snooze_in_the_past_is_a_422(owner):
    card = owner.post("/api/bench", json=CARD).json()
    for until in (now_ms() - 1000, 0, -5, "tomorrow", 1.5, 10**30):
        r = owner.post(f"/api/bench/{card['id']}/snooze", json={"until": until})
        assert r.status_code == 422, until
        assert isinstance(r.json()["detail"], str)


def test_the_filer_or_the_owner_can_update_and_withdraw_but_another_token_cannot(agent, owner,
                                                                                 core):
    card = agent.post("/api/bench", json=CARD).json()
    other = token(core, "other", "read", "write")
    owner.cookies.clear()
    for path in ("update", "withdraw"):
        r = owner.post(f"/api/bench/{card['id']}/{path}", json={"note": "Mine."}, headers=other)
        assert r.status_code == 403 and "whoever filed" in r.json()["detail"]
    updated = agent.post(f"/api/bench/{card['id']}/update", json={"note": "More evidence."})
    assert updated.status_code == 200 and updated.json()["history"][-1]["note"] == "More evidence."
    assert agent.post(f"/api/bench/{card['id']}/update", json={}).status_code == 422
    assert agent.post(f"/api/bench/{card['id']}/withdraw").json()["status"] == "withdrawn"
    assert agent.post(f"/api/bench/{card['id']}/withdraw").status_code == 409


def test_a_200_kb_body_survives_the_round_trip(owner, core):
    body = "A long finding with an accent: é.\n" * 6200
    assert len(body.encode()) > 200_000
    card = owner.post("/api/bench", json={**CARD, "body": body}).json()
    assert owner.get(f"/api/bench/{card['id']}").json()["body"] == body
    assert len(owner.get("/api/bench").content) < 3000
    assert core.tape.after(0, kind="bench.filed")[0].payload["body_ref"].startswith("sha256:")


def test_a_cards_file_is_served_as_a_download_with_a_safe_name(owner, core):
    ref = core.vault.put(b"col_a,col_b\n1,2\n")
    page = core.vault.put(b"<script>1</script>")
    card = owner.post("/api/bench", json={**CARD, "files": [
        {"name": "../résumé \"final\".csv", "ref": ref},
        {"name": "page.html", "ref": page},
    ]}).json()
    assert [f["name"] for f in card["files"]] == ["_résumé _final_.csv", "page.html"]
    r = owner.get(f"/api/bench/files/{card['id']}/0")
    assert r.status_code == 200 and r.content == b"col_a,col_b\n1,2\n"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="_r_sum_ _final_.csv"')
    assert "filename*=UTF-8''_r%C3%A9sum%C3%A9%20_final_.csv" in disposition
    assert r.headers["content-type"].startswith("text/csv")
    html = owner.get(f"/api/bench/files/{card['id']}/1")
    assert html.headers["content-type"] == "application/octet-stream"
    assert html.headers["content-disposition"].startswith("attachment")


def test_only_files_attached_to_the_card_can_be_fetched(owner, core):
    ref = core.vault.put(b"attached")
    core.vault.put(b"a secret that is not attached to any card")
    card = owner.post("/api/bench", json={**CARD, "files": [{"name": "a.txt", "ref": ref}]}).json()
    plain = owner.post("/api/bench", json=CARD).json()
    for path in (f"{card['id']}/1", f"{card['id']}/-1", f"{card['id']}/abc",
                 f"{card['id']}/99999999999999999999", f"{plain['id']}/0", f"{MISSING}/0",
                 "not-a-card/0"):
        r = owner.get(f"/api/bench/files/{path}")
        assert r.status_code == 404, path
        assert isinstance(r.json()["detail"], str)


def test_a_file_reference_that_is_not_in_the_vault_is_refused(owner):
    for ref in ("../../auth.db", "sha256:" + "f" * 64, "https://files.example.com/a.txt", ""):
        r = owner.post("/api/bench", json={**CARD, "files": [{"name": "a.txt", "ref": ref}]})
        assert r.status_code == 422, ref
    assert owner.get("/api/bench").json()["counts"]["needs"] == 0


def test_an_unknown_card_is_a_404_everywhere(owner):
    for bad in (MISSING, "nope", "0" * 27):
        assert owner.get(f"/api/bench/{bad}").status_code == 404
        r = owner.post(f"/api/bench/{bad}/decide", json=click("approve"))
        assert r.status_code == 404 and "no card" in r.json()["detail"]
        assert owner.post(f"/api/bench/{bad}/snooze").status_code == 404
        assert owner.post(f"/api/bench/{bad}/withdraw").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {}, [], "text", None,
        {**CARD, "kind": "NoDots"},
        {**CARD, "title": ""},
        {**CARD, "title": "x" * 500},
        {**CARD, "lane": "waiting"},
        {**CARD, "actions": "approve"},
        {**CARD, "actions": [{"id": "go", "label": "Go"}]},
        {**CARD, "actions": [{"id": "go", "label": "Go", "closes": True}] * 9},
        {**CARD, "details": [{"label": "x"}]},
        {**CARD, "unknown": 1},
        {**CARD, "body": 12},
        {**CARD, "summary": "two\nlines"},
    ],
)
def test_odd_input_is_a_422_with_one_readable_sentence_never_a_500(owner, body):
    r = owner.post("/api/bench", json=body)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str) and r.json()["detail"].endswith(".")


def test_the_ladder_can_be_read_by_a_token_but_set_only_by_the_owner(owner, agent):
    row = {"actor": "token:scout", "tool": "posts", "action": "publish", "level": "alone"}
    r = agent.put("/api/bench/ladder", json=row)
    assert r.status_code == 403
    assert agent.get("/api/bench/ladder").json()["rows"] == []
    set_ = owner.put("/api/bench/ladder", json=row)
    assert set_.status_code == 200 and set_.json()["level"] == "alone"
    table = agent.get("/api/bench/ladder").json()
    assert [(r["actor"], r["level"]) for r in table["rows"]] == [("token:scout", "alone")]
    assert table["caution_categories"] == ["send", "delete", "spend", "access"]
    assert table["levels"] == ["read", "approve", "alone"]


def test_the_owner_sets_a_cautious_row_to_alone_over_the_api_with_no_extra_step(owner, agent):
    row = {"actor": "agent:scout", "tool": "bank", "action": "pay", "level": "alone",
           "category": "spend"}
    assert agent.put("/api/bench/ladder", json=row).status_code == 403  # a token cannot
    r = owner.put("/api/bench/ladder", json=row)
    assert r.status_code == 200
    assert (r.json()["level"], r.json()["cautious"]) == ("alone", True)
    assert owner.put("/api/bench/ladder", json={**row, "level": "read"}).json()["level"] == "read"
    for bad in ({**row, "level": "root"}, {**row, "tool": "Bank Tool"}, {"actor": "x"}, {}):
        assert owner.put("/api/bench/ladder", json=bad).status_code == 422


def test_a_decline_over_the_api_drops_an_alone_row_and_counts_on_the_tally(owner, agent):
    row = {"actor": "token:scout", "tool": "posts", "action": "publish"}
    owner.put("/api/bench/ladder", json={**row, "level": "alone"})
    card = agent.post("/api/bench", json={**CARD, "ladder": {**row, "category": "publish"}}).json()
    owner.post(f"/api/bench/{card['id']}/decide", json=click("no", reason="Not this."))
    rows = owner.get("/api/bench/ladder").json()["rows"]
    assert [(r["level"], r["declines"], r["category"]) for r in rows] == [
        ("approve", 1, "publish")]


def test_the_bench_settings_are_declared_for_the_settings_screen(owner):
    described = owner.get("/api/settings").json()
    assert {"id": "bench", "label": "Bench"} in described["sections"]
    keys = {s["key"] for s in described["settings"] if s["section"] in ("bench", "bench_freedom")}
    assert keys == {"bench.snooze_hours", "bench.snooze_later_hours",
                    "bench.snooze_morning_hour", "bench.recent_decisions",
                    "bench.done_expire_days", "bench.single_keys", "bench.promotion_streak",
                    "bench.caution_categories", "bench.undo_drops_alone"}
    freedom = {s["key"] for s in described["settings"] if s["section"] == "bench_freedom"}
    assert freedom == {"bench.promotion_streak", "bench.caution_categories",
                       "bench.undo_drops_alone"}


def test_a_snooze_time_must_be_a_whole_number_not_text(owner):
    card = owner.post("/api/bench", json=CARD).json()
    for until in (str(now_ms() + HOUR), now_ms() + HOUR + 0.5, True, [now_ms() + HOUR]):
        r = owner.post(f"/api/bench/{card['id']}/snooze", json={"until": until})
        assert r.status_code == 422 and r.json()["detail"].startswith("until"), until
    assert owner.get(f"/api/bench/{card['id']}").json()["snoozed"] is False


def test_a_subject_with_a_hidden_character_is_a_422(owner):
    r = owner.post("/api/bench", json={**CARD, "subject": "task:a\x00b"})
    assert r.status_code == 422 and r.json()["detail"].startswith("subject")


def test_a_token_cannot_raise_its_own_row_by_filing_a_card(owner, agent):
    row = {"actor": "token:scout", "tool": "pay", "action": "send"}
    assert owner.put("/api/bench/ladder", json={**row, "level": "approve",
                                                "category": "spend"}).status_code == 200
    card = agent.post("/api/bench", json={**CARD, "ladder": {**row, "category": "publish"}})
    assert card.status_code == 201
    owner.post(f"/api/bench/{card.json()['id']}/decide", json=click("approve"))
    assert agent.put("/api/bench/ladder", json={**row, "level": "alone"}).status_code == 403
    rows = owner.get("/api/bench/ladder").json()["rows"]
    assert [(r["level"], r["approvals"]) for r in rows] == [("approve", 1)]


def test_a_token_cannot_swap_the_labels_on_approve_and_no(owner, agent, core):
    row = {"actor": "token:scout", "tool": "posts", "action": "posts.publish",
           "category": "publish"}
    swapped = [{"id": "no", "label": "Approve", "style": "primary", "closes": True},
               {"id": "approve", "label": "No", "style": "danger", "closes": True}]
    r = agent.post("/api/bench", json={**CARD, "actions": swapped, "ladder": row})
    assert r.status_code == 422 and "Bench's own buttons" in r.json()["detail"]
    r = agent.post("/api/bench", json={**CARD, "ladder": row, "actions": [
        {"id": "decline", "label": "No", "style": "danger", "closes": True}]})
    assert r.status_code == 422 and "Bench's own buttons" in r.json()["detail"]
    assert core.tape.after(0, kind="bench.filed") == []


def test_a_label_that_only_looks_like_one_of_the_benchs_own_is_refused_too(agent, core):
    """Punctuation, full-width letters and characters that do not show must not
    be a way to put a second "No" or "Approve" on a card."""
    for label in ("No.", "Approve!", "No\u200b", "\u200bNo", "N\u00a0o", "No\u00ad",
                  "\uff21\uff50\uff50\uff52\uff4f\uff56\uff45", "Not-yet", "GOT  IT", "(Undo)"):
        r = agent.post("/api/bench", json={**CARD, "actions": [
            {"id": "decline", "label": label, "closes": True}]})
        assert r.status_code == 422 and "Bench's own buttons" in r.json()["detail"], label
    assert core.tape.after(0, kind="bench.filed") == []
    r = agent.post("/api/bench", json={**CARD, "actions": [
        {"id": "decline", "label": "Not now", "closes": True},
        {"id": "nope", "label": "No thanks", "closes": True}]})
    assert r.status_code == 201


def test_a_card_from_before_the_buttons_were_checked_cannot_be_decided_by_a_swapped_button(
        owner, core):
    """Events are never rewritten, so a card filed with swapped labels can still
    be open. A click on it must not be recorded as the opposite of what it said."""
    row = {"actor": "token:scout", "tool": "posts", "action": "posts.publish",
           "category": "publish"}
    core.tape.print("bench.filed", {
        "card_id": MISSING, "kind": "posts.draft", "source": "Posts", "title": "Publish?",
        "lane": "needs", "ladder": row, "actions": [
            {"id": "no", "label": "Approve", "style": "primary", "closes": True},
            {"id": "approve", "label": "No", "style": "danger", "closes": True}],
    }, actor="token:scout", subject=f"bench:{MISSING}")
    assert owner.get(f"/api/bench/{MISSING}").json()["status"] == "open"
    for action in ("approve", "no"):
        r = owner.post(f"/api/bench/{MISSING}/decide", json=click(action, reason="Because."))
        assert r.status_code == 409 and "withdraw" in r.json()["detail"]
    assert core.tape.after(0, kind="bench.decided") == []
    assert owner.get("/api/bench/ladder").json()["rows"] == []
    assert owner.post(f"/api/bench/{MISSING}/withdraw").json()["status"] == "withdrawn"


def test_the_owners_click_is_refused_when_the_filer_rewrote_the_card_after_it_was_read(
        owner, agent, core):
    filed = agent.post("/api/bench", json={**CARD, "title": "Post the lunch menu",
                                           "body": "Soup."}).json()
    seen = owner.get(f"/api/bench/{filed['id']}").json()
    assert seen["title"] == "Post the lunch menu" and seen["updates"] == 0
    assert agent.post(f"/api/bench/{filed['id']}/update", json={
        "title": "Post the staff list", "body": "Everyone."}).status_code == 200
    click = {"action": "approve", "seen_updates": seen["updates"]}
    r = owner.post(f"/api/bench/{filed['id']}/decide", json=click)
    assert r.status_code == 409 and "changed since you read it" in r.json()["detail"]
    assert core.tape.after(0, kind="bench.decided") == []
    again = owner.get(f"/api/bench/{filed['id']}").json()
    assert again["status"] == "open" and again["title"] == "Post the staff list"
    r = owner.post(f"/api/bench/{filed['id']}/decide",
                   json={"action": "approve", "seen_updates": again["updates"]})
    assert r.status_code == 200
    assert core.tape.after(0, kind="bench.decided")[0].payload["updates"] == 1


def test_a_decision_over_the_api_must_say_which_revision_of_the_card_was_read(owner):
    card = owner.post("/api/bench", json=CARD).json()
    for body in ({"action": "approve"}, {"action": "approve", "seen_updates": "0"},
                 {"action": "approve", "seen_updates": -1}):
        r = owner.post(f"/api/bench/{card['id']}/decide", json=body)
        assert r.status_code == 422 and r.json()["detail"].startswith("seen_updates"), body
    assert owner.get(f"/api/bench/{card['id']}").json()["status"] == "open"


def test_a_token_cannot_attach_a_vault_blob_to_a_card_and_read_it_back(owner, agent, core):
    ref = core.vault.put_text("a prompt nobody showed this token")
    r = agent.post("/api/bench", json={**CARD, "files": [{"name": "a.txt", "ref": ref}]})
    assert r.status_code == 403 and "cannot attach files" in r.json()["detail"]
    assert core.tape.after(0, kind="bench.filed") == []
    # The owner, and trusted code calling file_card, still can.
    mine = owner.post("/api/bench", json={**CARD, "files": [{"name": "a.txt", "ref": ref}]})
    assert mine.status_code == 201
    assert owner.get(f"/api/bench/files/{mine.json()['id']}/0").status_code == 200


def test_the_bench_offers_the_quick_snooze_choices_the_server_worked_out(owner):
    owner.put("/api/settings/owner.timezone", json={"value": "Asia/Tokyo"})
    body = owner.get("/api/bench").json()
    assert [c["id"] for c in body["snooze_choices"]][-2:] == ["tomorrow", "week"]
    assert all(c["until"] > body["now"] and c["label"] for c in body["snooze_choices"])
    assert body["timezone"] == "Asia/Tokyo"
