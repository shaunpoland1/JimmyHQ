"""The Python SDK for JimmyHQ full apps. Needs only ``httpx`` and ``pydantic``.

A full app's program talks to JimmyHQ over local HTTP with its own token, never by
importing JimmyHQ. JimmyHQ starts it as ``python -m jimmyhq_app <module:attribute>``. See
``App`` for everything it can do, and ``jimmyhq_app.testing.FakeCore`` for tests.

Helpers apps share, so each need not keep its own copy: ``jimmyhq_app.text`` (cleaned
article text and its numbered sentences), ``similar``/``word_runs``/``word_share``
(comparing texts, with or without an embeddings model) and ``jimmyhq_app.settings``
(an app's settings read in one place, with pairs that must stay the right way round),
``jimmyhq_app.sheets`` (a CSV cell a spreadsheet will not run as a formula, and the way
back) and ``jimmyhq_app.grounding`` (whether words a model suggested rest on the words it
quoted) and ``jimmyhq_app.keybind`` (a key that goes only to the address it was typed for).
"""

from jimmyhq_app._types import (
    Calendar,
    CallError,
    Decision,
    Embedding,
    Event,
    FeedItem,
    FeedPage,
    Inbound,
    Item,
    Limits,
    Payload,
    Recall,
    Reply,
    RoleState,
    TokenRevoked,
    WebPage,
    WebResult,
    Work,
)
from jimmyhq_app.app import App, PrivateDB, use
from jimmyhq_app.similarity import cosine, similar, word_overlap, word_runs, word_share

__version__ = "0.1.0"
API = "1.0"  # the app API this SDK speaks

__all__ = ["API", "App", "Calendar", "CallError", "Decision", "Embedding", "Event", "FeedItem",
           "FeedPage", "Inbound", "Item", "Limits",
           "Payload", "PrivateDB", "Recall", "Reply", "RoleState", "TokenRevoked", "WebPage",
           "WebResult", "Work", "__version__", "cosine", "similar", "use", "word_overlap",
           "word_runs", "word_share"]
