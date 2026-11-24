from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def client(core):
    with TestClient(create_app(core), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def owner(client):
    assert client.post("/api/auth/setup", json={"name": "Sam", "password": PASSWORD}).is_success
    return client


def _token(core, scopes):
    secret = core.auth.create_token("test", scopes)[1]
    return {"authorization": f"Bearer {secret}"}


def test_nobody_reads_or_changes_tasks_without_signing_in(client):
    assert client.get("/api/tasks").status_code == 401
    assert client.post("/api/tasks", json={"title": "x"}).status_code == 401


def test_the_whole_life_of_a_task_over_the_api(owner):
    made = owner.post("/api/tasks", json={"title": "Call the plumber", "area": "home"})
    assert made.status_code == 201
    task = made.json()
    url = f"/api/tasks/{task['id']}"
    assert task["filed_by"] == "owner" and owner.get(url).json() == task

    today = owner.get("/api/tasks").json()["date"]
    assert owner.patch(url, json={"due": today, "notes": "before noon"}).json()["due"] == today
    board = owner.get("/api/tasks").json()
    assert [t["id"] for t in board["today"]] == [task["id"]]
    assert set(board) == {"date", "timezone", "stalled_after", "done_days", "open", "today",
                          "kept_today", "done", "dropped", "areas", "default_area", "counts"}
    assert board["counts"]["due_today"] == 1

    pushed = owner.post(f"{url}/push", json={"to": "2999-01-01"}).json()
    assert (pushed["due"], pushed["pushes"]) == ("2999-01-01", 1)
    assert owner.post(f"{url}/done").json()["status"] == "done"
    assert owner.get("/api/tasks").json()["counts"]["done_today"] == 1
    assert owner.post(f"{url}/done").status_code == 409
    assert owner.post(f"{url}/reopen").json()["status"] == "open"
    assert owner.post(f"{url}/drop").json()["status"] == "dropped"
    assert [t["id"] for t in owner.get("/api/tasks").json()["dropped"]] == [task["id"]]
    assert owner.post(f"{url}/restore").json()["status"] == "open"


def test_a_push_is_taken_back_over_the_api_and_a_finished_task_says_which_day_it_was(owner):
    board = owner.get("/api/tasks").json()
    task = owner.post("/api/tasks", json={"title": "Renew the pass", "due": board["date"]}).json()
    url = f"/api/tasks/{task['id']}"
    assert owner.post(f"{url}/unpush").status_code == 409
    pushed = owner.post(f"{url}/push", json={"to": "2999-01-01"}).json()
    assert (pushed["pushes"], pushed["can_unpush"]) == (1, True)
    back = owner.post(f"{url}/unpush")
    assert back.status_code == 200
    assert (back.json()["due"], back.json()["pushes"], back.json()["can_unpush"]) == (
        board["date"], 0, False)
    refused = owner.post(f"{url}/unpush")
    assert refused.status_code == 409
    assert refused.json()["detail"] == ("This task has not been pushed since it was last done, "
                                        "so there is nothing to take back.")
    assert owner.post(f"{url}/done").json()["done_on"] == board["date"]
    after = owner.get("/api/tasks").json()
    assert after["done_days"] == 7 and after["done"][0]["done_on"] == after["date"]
    owner.cookies.clear()
    assert owner.post(f"{url}/unpush").status_code == 401


def test_a_repeating_task_is_kept_and_unkept_over_the_api(owner):
    today = owner.get("/api/tasks").json()["date"]
    task = owner.post("/api/tasks", json={"title": "Stretch", "repeat": "FREQ=DAILY"}).json()
    assert (task["due"], task["repeat_text"]) == (today, "every day")
    url = f"/api/tasks/{task['id']}"
    kept = owner.post(f"{url}/done").json()
    assert kept["status"] == "open" and kept["due"] > today and kept["last_kept"] == today
    board = owner.get("/api/tasks").json()
    assert board["today"] == [] and [t["id"] for t in board["kept_today"]] == [task["id"]]
    undone = owner.post(f"{url}/undo-round").json()
    assert (undone["due"], undone["last_kept"]) == (today, None)
    assert owner.post(f"{url}/undo-round").status_code == 409


def test_rank_and_filters_over_the_api(owner):
    a, b = (owner.post("/api/tasks", json={"title": t, "area": t.lower()}).json() for t in "AB")
    moved = owner.post(f"/api/tasks/{b['id']}/rank", json={"before": a["id"]})
    assert moved.status_code == 200
    assert [t["title"] for t in owner.get("/api/tasks").json()["open"]] == ["B", "A"]
    assert [t["title"] for t in owner.get("/api/tasks?area=a").json()["open"]] == ["A"]
    assert owner.get("/api/tasks?assignee=agent:scout").json()["open"] == []
    owner.post(f"/api/tasks/{a['id']}/drop")
    gone = owner.post(f"/api/tasks/{b['id']}/rank", json={"after": a["id"]})
    assert gone.status_code == 409 and "no longer on the open list" in gone.json()["detail"]


@pytest.mark.parametrize(("body", "status"), [
    ({}, 422),
    ({"title": ""}, 422),
    ({"title": 5}, 422),
    ({"title": "t", "colour": "red"}, 422),
    ({"title": "t", "due": "2026-13-01"}, 422),
    ({"title": "t", "due": 20260101}, 422),
    ({"title": "t", "repeat": "FREQ=MINUTELY"}, 422),
    ({"title": "t", "repeat": "FREQ=DAILY\nDTSTART:20200101"}, 422),
    ({"title": "t", "assignee": "agent:"}, 422),
    ({"title": "x" * 30_000}, 422),
    ([1, 2], 422),
])
def test_odd_input_is_refused_with_one_readable_sentence_and_never_a_500(owner, body, status):
    r = owner.post("/api/tasks", json=body)
    assert r.status_code == status
    detail = r.json()["detail"]
    assert isinstance(detail, str) and detail.endswith(".") and "Traceback" not in detail
    assert owner.get("/api/tasks").json()["open"] == []


def test_odd_requests_about_one_task_are_refused_cleanly(owner):
    task = owner.post("/api/tasks", json={"title": "t"}).json()
    url = f"/api/tasks/{task['id']}"
    for method, path, body, status in [
        ("get", "/api/tasks/nope", None, 404),
        ("get", "/api/tasks/01JABCDEFGHJKMNPQRSTVWXYZ0", None, 404),
        ("post", "/api/tasks/01JABCDEFGHJKMNPQRSTVWXYZ0/done", None, 404),
        ("patch", url, {"title": None}, 422),
        ("patch", url, {"status": "done"}, 422),
        ("patch", url, {"due_time": "09:00"}, 422),
        ("post", f"{url}/push", {}, 422),
        ("post", f"{url}/push", {"to": "yesterday"}, 422),
        ("post", f"{url}/push", {"to": "1999-01-01"}, 422),
        ("post", f"{url}/rank", {}, 422),
        ("post", f"{url}/rank", {"before": task["id"]}, 422),
        ("post", f"{url}/rank", {"before": "x", "after": "y"}, 422),
        ("post", f"{url}/reopen", None, 409),
        ("post", f"{url}/restore", None, 409),
        ("post", f"{url}/undo-round", None, 409),
        ("post", f"{url}/unpush", None, 409),
        ("post", f"{url}/unpush", {"to": "2999-01-01"}, 409),
        ("get", "/api/tasks?area=" + "a" * 500, None, 422),
    ]:
        r = owner.request(method, path, json=body)
        assert r.status_code == status, (method, path, body, r.text)
        assert isinstance(r.json()["detail"], str)
    assert owner.get(url).json() == task


def test_a_read_token_can_read_but_not_change_and_a_write_token_files_under_its_own_name(
        owner, core):
    task = owner.post("/api/tasks", json={"title": "t"}).json()
    owner.cookies.clear()
    read, write = _token(core, ["read"]), _token(core, ["read", "write"])
    assert owner.get("/api/tasks", headers=read).status_code == 200
    assert owner.post("/api/tasks", json={"title": "x"}, headers=read).status_code == 403
    assert owner.post(f"/api/tasks/{task['id']}/done", headers=read).status_code == 403
    made = owner.post("/api/tasks", json={"title": "x"}, headers=write)
    assert made.status_code == 201 and made.json()["filed_by"] == "token:test"
