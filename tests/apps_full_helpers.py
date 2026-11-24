"""Shared by the full-app tests (API, inbox, runtime, SDK): hand-made full apps installed
for real from a folder, and a client that speaks for one of them. No network."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps_install_helpers import install_folder
from jimmyhq.web.app import create_app

PASSWORD = "correct horse battery"
LOCAL = ("127.0.0.1", 5000)


def full_app(tmp_path: Path, app_id: str = "drafty", *, permissions=(), module: str = "",
             **extra) -> Path:
    """A full app's folder: a manifest and one Python module, ``<id>_main``."""
    folder = tmp_path / "src" / app_id
    (folder / f"{app_id}_main").mkdir(parents=True)
    (folder / f"{app_id}_main" / "__init__.py").write_text(module, encoding="utf-8")
    manifest = {
        "manifest": 1, "id": app_id, "name": app_id.title(), "summary": "A test app.",
        "licence": "MIT", "version": "1.0.0", "tier": "full", "api": "1.0",
        "permissions": list(permissions),
        "server": {"entry": f"{app_id}_main:app"},
    } | extra
    if "bench.file" in permissions:
        manifest.setdefault("cards", [{"kind": "draft", "label": "Draft"}])
    (folder / "jimmyhq.app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def install_full(core, tmp_path: Path, app_id: str = "drafty", **kw) -> dict:
    return install_folder(core, full_app(tmp_path, app_id, **kw))


def client_for(core, *, address=LOCAL) -> TestClient:
    # From this computer, a program addresses it by a local name; any other name would
    # mean something relayed the request, and an app's token is not taken then.
    base = "http://127.0.0.1" if address == LOCAL else "http://testserver"
    return TestClient(create_app(core), base_url=base, client=address)


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


class AppClient:
    """Speaks for one app with its token, as its program would."""

    def __init__(self, client: TestClient, token: str):
        self.client, self.token = client, token

    def get(self, path: str, **kw):
        return self.client.get(f"/api/app/v1{path}", headers=bearer(self.token), **kw)

    def post(self, path: str, body=None, **kw):
        return self.client.post(f"/api/app/v1{path}", json=body, headers=bearer(self.token),
                                **kw)

    def put(self, path: str, body=None, **kw):
        return self.client.put(f"/api/app/v1{path}", json=body, headers=bearer(self.token),
                               **kw)

    def call(self, name: str, args=None) -> dict:
        answer = self.post(f"/call/{name}", {"args": args or {}})
        assert answer.status_code == 200, answer.text
        return answer.json()

    def ok(self, name: str, args=None):
        body = self.call(name, args)
        assert body["ok"], body
        return body["result"]

    def refused(self, name: str, args=None) -> dict:
        body = self.call(name, args)
        assert not body["ok"], body
        return body["error"]


DRAFTED = {"type": "object", "properties": {"post": {"type": "integer"}},
           "required": ["post"], "additionalProperties": False}
