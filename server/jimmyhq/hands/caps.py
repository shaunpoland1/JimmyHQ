"""Limits on what leaves through a hand, counted from the run ledger.

Every run that left is one row in ``hands.db``, whether it worked or not, so a restart
loses no count and a failed call still counts. A call refused here never left: nothing
happened, and the caller did nothing wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jimmyhq.hands.rules import HandRefused
from jimmyhq.hands.store import HandsStore


def local_time(core: Any, now_ms: int) -> datetime:
    """A stored time in the owner's time zone: used only to find where a day starts."""
    name = str(core.settings.get("owner.timezone") or "")
    moment = datetime.fromtimestamp(now_ms / 1000).astimezone()
    if name:
        try:
            return moment.astimezone(ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return moment


def day_start(core: Any, now_ms: int) -> int:
    d = local_time(core, now_ms).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp() * 1000)


def month_start(core: Any, now_ms: int) -> int:
    d = local_time(core, now_ms).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp() * 1000)


def next_month(core: Any, now_ms: int) -> str:
    d = (local_time(core, now_ms).replace(day=1) + timedelta(days=32)).replace(day=1)
    return f"{d.day} {d:%B}"


def owner_name(core: Any) -> str:
    return str(core.settings.get("owner.name") or "").strip() or "the owner"


def calls_per_day(core: Any, store: HandsStore, agent: str, account: str, now_ms: int) -> None:
    """Rule ``hand_calls_per_day``: one agent's calls on one account in a day."""
    cap = int(core.settings.get("hands.calls_per_day"))
    used = store.count_runs(since_ms=day_start(core, now_ms), agent=agent, account=account)
    if used >= cap:
        raise HandRefused(
            "hand_calls_per_day",
            f"Nothing was done: today's limit of {cap} calls on {account} is used up. It "
            "opens again tomorrow. Work from what you already have.", frustrate=False)


def calls_per_month(core: Any, store: HandsStore, provider_id: str, label: str,
                    now_ms: int) -> None:
    """Rule ``hand_calls_per_month``: every agent's calls through one provider."""
    cap = int(core.settings.get("hands.calls_per_month"))
    used = store.count_runs(since_ms=month_start(core, now_ms), provider_id=provider_id)
    if used >= cap:
        raise HandRefused(
            "hand_calls_per_month",
            f"Nothing was done: this month's limit of {cap} calls through {label} is used "
            f"up. It opens again on {next_month(core, now_ms)}, unless {owner_name(core)} "
            "raises the limit. Carry on without it.", frustrate=False)


def requests_per_day(core: Any, store: HandsStore, agent: str, now_ms: int) -> None:
    """Rule ``hand_requests_per_day``: how many approvals one agent may ask for."""
    cap = int(core.settings.get("hands.requests_per_day"))
    if store.count_pending(agent, day_start(core, now_ms)) >= cap:
        raise HandRefused(
            "hand_requests_per_day",
            f"Nothing was asked: today's limit of {cap} requests to {owner_name(core)} is "
            "used up. It opens again tomorrow. Note what you wanted to do and ask then.",
            frustrate=False)


def action_today(core: Any, store: HandsStore, agent: str, account: str, action: str,
                 now_ms: int) -> int:
    return store.count_runs(since_ms=day_start(core, now_ms), agent=agent, account=account,
                            action=action)


def reads_per_day(core: Any, store: HandsStore, rule: dict[str, Any], now_ms: int) -> None:
    """Rule ``hand_rule_per_day``: the owner's daily number on one reading action. (On a
    changing action the same number turns the call into a request instead.)"""
    cap = (rule.get("limits") or {}).get("per_day")
    if cap is not None and action_today(core, store, rule["agent"], rule["account"],
                                        rule["action"], now_ms) >= cap:
        raise HandRefused(
            "hand_rule_per_day",
            f"Nothing was done: today's limit of {cap} uses of {rule['tool']} is used up. "
            "It opens again tomorrow. Work from what you already have.", frustrate=False)
