"""Shared by the plug point tests: a full app installed for real, speaking with its own
token, that has told JimmyHQ what it offers and is asking for work. No network, no
child process."""

from __future__ import annotations

from apps_full_helpers import AppClient, client_for, install_full
from jimmyhq.apps import inbox
from jimmyhq.ids import now_ms


def speak(web, core, app_id: str) -> AppClient:
    return AppClient(web, core.auth.mint_app_token(app_id))


def offer(app: AppClient, **plugs) -> dict:
    body = app.put("/plugs", plugs).json()
    assert body["ok"], body
    return body["result"]


def handed(app: AppClient) -> list[dict]:
    body = app.get("/inbox?wait=0").json()
    assert body["ok"], body
    return body["result"]["deliveries"]


def answer(app: AppClient, delivery_id: str, result=None, *, error: str | None = None):
    body = {"ok": False, "error": error} if error else {"ok": True, "result": result}
    reply = app.post(f"/inbox/{delivery_id}", body).json()
    assert reply["ok"], reply
    return reply["result"]


def running(core, app_id: str) -> None:
    """The app asked for work just now, as a running program does all the time."""
    inbox.inbox(core).beat(app_id, now_ms())


__all__ = ["AppClient", "answer", "client_for", "handed", "install_full", "offer",
           "running", "speak"]
