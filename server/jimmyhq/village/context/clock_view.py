"""The time, said so it cannot be misread."""

from __future__ import annotations

from typing import Any

from jimmyhq.village.rules import rule


def part_of_day(hour: int) -> str:
    """Words beside the 24-hour clock: "11:07" alone gets read as late evening."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def clock_text(h: float) -> str:
    m = round(h * 60) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def bedtime_text(start_hour: float, early_hour: float, offset_min: float,
                 local_hour: float) -> str:
    """When this agent usually goes to sleep, so the evening is not mistaken for the
    end of the day and spent on closing notes hours before bed."""
    bed = start_hour + offset_min / 60
    early = early_hour + offset_min / 60
    h = local_hour if local_hour >= 5 else local_hour + 24
    if h >= bed:
        return ""
    return (f" You usually go to sleep around {clock_text(bed)}"
            f" (from about {clock_text(early)} if you're tired with nothing on), so the "
            "evening is still part of your day.")


def day_number(v: Any, agent_id: str) -> int:
    """Counted in calendar days, the way a person counts: the day you were born is day
    1 and the morning after is day 2."""
    born = v.when(v.born_at(agent_id)).date()
    return (v.when(v.clock.now()).date() - born).days + 1


@rule("time_view")
def time_text(v: Any, agent_id: str) -> str:
    a = v.world.agents[agent_id]
    local = v.when(v.clock.now())
    bed = "" if a.asleep else bedtime_text(
        v.S("sleep.start_hour"), v.S("sleep.early_night_hour"),
        a.temperament.get("sleep_offset_minutes", 0), local.hour + local.minute / 60)
    return (f"{local:%A %B} {local.day}, {local.year}, {local:%H:%M} "
            f"({part_of_day(local.hour)}). Day {day_number(v, agent_id)} of your life.{bed}")
