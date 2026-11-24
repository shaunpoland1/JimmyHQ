"""Watching agents' inboxes for mail that pretends to be from the owner.

The worker polls every account whose provider can receive, by a cursor kept in
``hands.db`` (no webhooks: a local-only install has no public address). For each new
message it looks at two things only: the From address the provider reports and the
provider's own sender check. A message from one of the owner's addresses whose check
failed (or was not checked, while ``hands.mail_unknown_is_failed`` is on) files a small
card on the Bench, at most ``hands.mail_failed_cards_per_day`` a day. The card names the
inbox and the check; never the sender, the subject or the text.

Nothing here reads what a message says, and nothing here changes a rule or a level.
"""

from __future__ import annotations

import logging
from typing import Any

from jimmyhq import bench
from jimmyhq.core import Core
from jimmyhq.hands import mail
from jimmyhq.hands.provider import Inbound, can_receive
from jimmyhq.hands.service import Hands, NotReady

log = logging.getLogger("jimmyhq.hands")

CARD = "hand.mail_failed_check"
SOURCE = "Integrations"
DAY_MS = 86_400_000


def claims_owner_and_failed(core: Any, item: Inbound) -> bool:
    """Rule ``hand_mail_forged_owner_card``: whether a message says it is from one of
    the owner's addresses and its sender check failed (or, with
    ``hands.mail_unknown_is_failed`` on, was not checked)."""
    get = core.settings.get
    owner = {mail.address_of(a) for a in get("hands.owner_addresses") or ()}
    sender = mail.address_of(item.sender)
    if not sender or sender not in owner:
        return False
    check = mail.counted_check(item.sender_check, bool(get("hands.mail_unknown_is_failed")))
    return check == mail.FAILED


def file_card(core: Core, h: Hands, account: str, item: Inbound) -> str | None:
    """File the card about one such message, unless today's cards are used up."""
    most = int(core.settings.get("hands.mail_failed_cards_per_day"))
    if h.store.mail_cards_since(h.now() - DAY_MS) >= most:
        return None
    checked = "failed" if item.sender_check == mail.FAILED else "not checked"
    try:
        card_id = bench.file_card(
            core, filed_by="hands", kind=CARD, source=SOURCE, lane="done",
            title="A message claiming to be from you failed its sender check",
            summary=f"It arrived in the inbox \"{account}\". Agents see it marked as "
                    "untrusted, and it never counts as your instruction.",
            body="The mail provider's own check could not confirm that this mail came "
                 "from your address. Someone may be pretending to be you. The message is "
                 "not shown here. If you did send it, your mail domain's set-up (DKIM or "
                 "DMARC) may need attention.",
            details=[{"label": "Inbox", "text": account},
                     {"label": "Sender check", "text": checked}])
    except bench.BenchError:
        log.exception("a card about failed mail could not be filed")
        return None
    h.store.note_mail_card(card_id, account, h.now())
    return card_id


async def poll(core: Core, h: Hands) -> list[str]:
    """One look at every inbox. Returns the ids of the cards filed."""
    if not core.settings.get("hands.mail_failed_card"):
        return []
    filed: list[str] = []
    for held in h.accounts():
        slug = held["account"]
        try:
            provider = h.adapter(held["provider_id"])
            if not can_receive(provider):
                continue
            items, cursor = await provider.inbound(h.provider_account(slug),
                                                   h.store.mail_cursor(slug))
        except NotReady:
            continue
        except Exception as exc:  # noqa: BLE001 - one inbox failing stops no other
            log.warning("looking for new mail failed: %s", type(exc).__name__)
            continue
        for item in items:
            if claims_owner_and_failed(core, item):
                card_id = file_card(core, h, slug, item)
                if card_id:
                    filed.append(card_id)
        h.store.set_mail_cursor(slug, cursor)
    return filed
