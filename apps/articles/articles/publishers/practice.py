"""Practice: records what it would send, and publishes nothing. The default."""

from __future__ import annotations

from typing import Any

from articles.publishers import Outcome, Publication


class Practice:
    id = "practice"
    label = "Practice"
    sends_out = False

    def __init__(self, ctx: Any):
        self.ctx = ctx

    def needs(self, site: dict[str, Any]) -> str | None:
        return None

    def publish(self, publication: Publication) -> Outcome:
        self.ctx.store.insert("practice_log", {
            "piece_id": publication.piece_id, "created": self.ctx.now(),
            "would_send": {"site": publication.site["name"], "title": publication.title,
                           "html": publication.html, "status": "draft"}})
        return Outcome("sent", "Practice: nothing was sent. What would have been sent is "
                       "kept under the article.")
