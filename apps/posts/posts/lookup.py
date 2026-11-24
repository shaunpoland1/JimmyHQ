"""Asking a tool about the owner's own accounts, for the page's "Add an account".

``accounts_list`` only reads: it asks the tool (SocialBu, Buffer) which accounts the owner
has there, or says in a sentence why it cannot, so the page can offer typing one in
instead. It never raises for a tool that cannot tell: the sentence is the answer.
"""

from __future__ import annotations

from typing import Any

from posts import booking
from posts.context import Ctx
from posts.schedulers import Unavailable, lists_accounts


def accounts_list(ctx: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    """The accounts the owner has on one tool: ``{scheduler, label, can_list, accounts,
    said}``. ``scheduler`` is the tool's id, else the one Posts books through; ``accounts``
    is ``[{account_id, label, network}]``. ``can_list`` False (or an empty list) comes with
    ``said``, the sentence that says why and what to do instead."""
    chosen = str(args.get("scheduler") or "").strip() or booking.chosen_id(ctx, None)
    sched = ctx.schedulers.get(chosen)
    if sched is None:
        return {"scheduler": chosen, "label": chosen, "can_list": False, "accounts": [],
                "said": f"Posts does not offer a tool called \"{chosen}\". Choose one from the "
                        "list, or type the account in instead."}
    answer: dict[str, Any] = {"scheduler": sched.id, "label": sched.label, "can_list": False,
                              "accounts": [], "said": ""}
    if not lists_accounts(sched):
        answer["said"] = (f"{sched.label} books nothing out, so it has no accounts to list. "
                          "Posts books the brand itself there." if not sched.sends_out else
                          f"Posts cannot ask {sched.label} for its accounts. Type each one "
                          "in instead.")
        return answer
    try:
        found = sched.accounts()
    except Unavailable as error:
        answer["said"] = str(error)
        return answer
    answer["can_list"] = True
    answer["accounts"] = found
    if not found:
        answer["said"] = (f"{sched.label} lists no account for this key. Connect one there, "
                          "or type its id in instead.")
    return answer
