"""By hand: the article is ready to copy as clean HTML or Markdown, and the owner presses
Published once it is up."""

from __future__ import annotations

from typing import Any

from articles.publishers import Outcome, Publication


class ByHand:
    id = "by_hand"
    label = "By hand"
    sends_out = False

    def __init__(self, ctx: Any):
        self.ctx = ctx

    def needs(self, site: dict[str, Any]) -> str | None:
        return None

    def publish(self, publication: Publication) -> Outcome:
        return Outcome("sent", "Ready for you to publish by hand: copy it as HTML or "
                       "Markdown, put it on your site, then press Published.")
