"""The publisher interface: what ``publish`` calls, and the three publishers.

A publisher is handed a ``Publication`` and answers an ``Outcome``. It never decides
whether to publish: ``publish.publish`` does that, records the attempt first, and reads
the outcome.

    pub.needs(site) -> str | None        what the site lacks for it, as a sentence
    pub.publish(publication) -> Outcome  sent (with what the site said), refused or unsure
    pub.update(publication, external)    optional: put an edit into what was sent before;
                                         without it, an edit after sending is not sent

An outcome is ``sent`` only on a definite yes and ``refused`` only on a definite no;
anything else (no answer, a timeout, a yes without an id) is ``unsure``. ``sends_out``
says whether it sends anything off this computer: Practice and By hand do not. To add a
publisher, give it these and list it in ``made``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Publication:
    piece_id: int
    title: str
    html: str  # the clean article with its sources list, as ``render.final_html`` made it
    markdown: str
    site: dict[str, Any]
    header: str | None = None  # a header image in the app's data folder (``header``), if any
    header_type: str = "image/png"


@dataclass(frozen=True)
class Outcome:
    kind: str  # "sent", "refused" or "unsure"
    detail: str = ""
    external: dict[str, Any] = field(default_factory=dict)  # {id, link} from the site


class Publisher(Protocol):
    id: str
    label: str
    sends_out: bool

    def needs(self, site: dict[str, Any]) -> str | None: ...
    def publish(self, publication: Publication) -> Outcome: ...


def made(ctx: Any) -> dict[str, Publisher]:
    from articles.publishers.by_hand import ByHand  # noqa: PLC0415
    from articles.publishers.practice import Practice  # noqa: PLC0415
    from articles.publishers.wordpress import WordPress  # noqa: PLC0415

    return {"practice": Practice(ctx), "by_hand": ByHand(ctx), "wordpress": WordPress(ctx)}
