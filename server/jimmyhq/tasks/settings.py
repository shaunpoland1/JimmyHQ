"""Settings for the task list. Importing this module declares them."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

section("tasks", "Tasks", group="work", order=20,
        description="Your areas, and when a task counts as stalled or finished.")

declare(Setting(
    key="tasks.areas", section="tasks", type="list", default=["home", "work"],
    label="Areas",
    help="The short tags you sort tasks under, such as home or work. A task filed under a "
         "new area is still accepted, and the area is offered again next time.",
))
declare(Setting(
    key="tasks.default_area", section="tasks", type="str", default="",
    label="Default area",
    help="The area a new task gets when none is given. Leave empty for no area.",
))
declare(Setting(
    key="tasks.stalled_after", section="tasks", type="int", default=3, min=1, max=50,
    label="Mark a task as stalled after this many pushes",
    help="A task you have pushed to a later date this many times, without doing it in "
         "between, is flagged as stalled so you can decide whether to do it or drop it.",
))
declare(Setting(
    key="tasks.done_days", section="tasks", type="int", default=7, min=1, max=90,
    label="Show finished tasks for (days)",
    help="How many days a finished or dropped task stays visible on the task screen. "
         "Older ones stay on the record.",
))
