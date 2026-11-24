"""Settings for an app's roles: how much work an app may hand the owner's agents, and how
an agent made for a role starts. On the Apps page, in their own part. Imported by
``apps.settings``."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

WORK_OPEN = "apps.roles.work_open"
WORK_PER_DAY = "apps.roles.work_per_day"
NEW_AGENT_LOCAL_ONLY = "apps.roles.new_agent_local_only"

section("apps_roles", "Roles and agents", parent="apps", order=50,
        description="When an app asks for roles to be filled by your agents: how much "
                    "work it may hand them, and how an agent made for a role starts.")
declare(Setting(
    key=WORK_OPEN, section="apps_roles", type="int", default=10, min=0, max=500,
    label="Open pieces of work each app may give out",
    help="The most tasks one app may have waiting on your agents' lists at once. Past it, "
         "the app is told no and does the work without agents. 0 lets no app give out "
         "work.",
))
declare(Setting(
    key=WORK_PER_DAY, section="apps_roles", type="int", default=20, min=0, max=1000,
    label="Pieces of work each app may give out a day",
    help="How many tasks one app may give your agents in 24 hours.",
))
declare(Setting(
    key=NEW_AGENT_LOCAL_ONLY, section="apps_roles", type="bool", default=False,
    label="An agent made for a role starts with \"Local only\" on",
    help="When on, an agent JimmyHQ makes for an app's role uses only the models on this "
         "computer until you change it on the Village screen. Making an agent for a role "
         "never lets it do anything alone on the ladder: every level starts where it "
         "always does.",
))
