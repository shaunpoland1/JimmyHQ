"""The forms an agent asked its owner to let it send from its browser.

Sending a form that changes something waits for the owner. The agent is told at once
that nothing was sent; this is where it later reads the answer: still waiting, sent as
shown, declined, or approved too late for a page that had changed. Only an agent that
holds the ``browser`` capability is shown any of it, and every word here is the
village's own: nothing a page said is repeated.
"""

from __future__ import annotations

from typing import Any

from jimmyhq.village.rules import rule

CAPABILITY = "browser"


@rule("browser_answers_in_view")
def browser_text(v: Any, agent_id: str) -> str:
    """The waiting requests and the recent answers, or "" when there are none."""
    if CAPABILITY not in ((v.cfg(agent_id) or {}).get("capabilities") or ()):
        return ""
    from jimmyhq.village.tools import browser

    return browser.view_text(v, agent_id)
