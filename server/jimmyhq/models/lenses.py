"""The two stored views of this module: its configuration, and usage totals."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jimmyhq.lens import Lens
from jimmyhq.tape.registry import Event

DAYS_KEPT = 90
HOURS_KEPT = 48
HOUR_MS = 3_600_000
# Distinct provider+model rows kept in one bucket. Model names are typed by the
# owner or passed by callers, so without a cap the view could grow without end.
MODELS_KEPT = 100
OTHER = "\n\n"  # the row every model past the cap is added to: no provider, no model


class ConfigLens(Lens):
    """Providers, roles and the owner's price table, as last saved."""

    name = "models.config"
    version = 1
    kinds = ("model.provider.saved", "model.provider.removed", "model.role.set",
             "model.prices.set")

    def initial(self) -> dict[str, Any]:
        return {"providers": {}, "roles": {}, "prices": None}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        payload = event.payload
        if event.kind == "model.provider.saved":
            state["providers"][payload["provider_id"]] = payload
        elif event.kind == "model.provider.removed":
            state["providers"].pop(payload["provider_id"], None)
        elif event.kind == "model.role.set":
            if payload["primary"] is None:
                state["roles"].pop(payload["role"], None)
            else:
                state["roles"][payload["role"]] = {
                    "primary": payload["primary"], "fallbacks": payload["fallbacks"],
                }
        elif event.kind == "model.prices.set":
            state["prices"] = payload["prices"]
        return state


def model_key(provider_id: str, provider_type: str, model: str) -> str:
    return "\n".join((provider_id, provider_type, model))


def _bucket() -> dict[str, Any]:
    # m: model key -> [calls, tokens in, tokens out, local calls, local in, local out]
    # e: model key -> [embedding calls, tokens, local calls, local tokens]. Kept apart
    # from m, so turning text into vectors is never counted as asking a model something.
    return {"m": {}, "e": {}, "fb": 0, "fail": 0}


class UsageLens(Lens):
    """Calls and tokens by model: for all time, per UTC day and per hour.

    Embedding calls are counted in their own rows, so they never inflate the
    count of chat calls or their tokens.

    Days are UTC because a fold must not read a setting; the hourly buckets
    exist so "the last 24 hours" can be answered without scanning the Tape.
    Both are capped, and so is the number of models in each bucket (the rest
    are added together), so the state stays small however long the Tape grows.
    """

    name = "models.usage"
    version = 3
    kinds = ("model.called", "model.failed")

    def initial(self) -> dict[str, Any]:
        return {"all": _bucket(), "days": {}, "hours": {}}

    def fold(self, state: dict[str, Any], event: Event) -> dict[str, Any]:
        hour = event.ts // HOUR_MS
        day = datetime.fromtimestamp(event.ts / 1000, tz=UTC).strftime("%Y-%m-%d")
        buckets = (
            state["all"],
            state["days"].setdefault(day, _bucket()),
            state["hours"].setdefault(str(hour), _bucket()),
        )
        payload = event.payload
        for bucket in buckets:
            if event.kind == "model.failed":
                bucket["fail"] += 1
                continue
            key = model_key(payload["provider_id"], payload["provider_type"], payload["model"])
            if payload.get("embedding"):
                if key not in bucket["e"] and len(bucket["e"]) >= MODELS_KEPT:
                    key = OTHER
                row = bucket["e"].setdefault(key, [0, 0, 0, 0])
                row[0] += 1
                row[1] += payload["input_tokens"]
                if payload["local"]:
                    row[2] += 1
                    row[3] += payload["input_tokens"]
                continue
            if key not in bucket["m"] and len(bucket["m"]) >= MODELS_KEPT:
                key = OTHER
            row = bucket["m"].setdefault(key, [0, 0, 0, 0, 0, 0])
            tokens_in, tokens_out = payload["input_tokens"], payload["output_tokens"]
            row[0] += 1
            row[1] += tokens_in
            row[2] += tokens_out
            if payload["local"]:
                row[3] += 1
                row[4] += tokens_in
                row[5] += tokens_out
            if payload.get("fallback_from"):
                bucket["fb"] += 1
        if len(state["days"]) > DAYS_KEPT:
            for old in sorted(state["days"])[:-DAYS_KEPT]:
                del state["days"][old]
        # By age, not by count: on a quiet install the hours are sparse.
        for old in [h for h in state["hours"] if int(h) <= hour - HOURS_KEPT]:
            del state["hours"][old]
        return state
