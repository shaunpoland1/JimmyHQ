"""Settings for nodes. Importing this module declares them."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

section("nodes", "Nodes", group="system", order=10,
        description="Your other machines that have joined this one and lend it their models.")

declare(Setting(
    key="nodes.join_code_minutes", section="nodes", type="int", default=10, min=1, max=120,
    label="A join code works for (minutes)",
    help="How long you have to type a new join code on the other machine. It works once.",
))
declare(Setting(
    key="nodes.heartbeat_s", section="nodes", type="float", default=10.0, min=0.5, max=300,
    advanced=True,
    label="A node reports in every (seconds)",
    help="How often a joined machine tells this one it is still there.",
))
declare(Setting(
    key="nodes.gone_after_s", section="nodes", type="int", default=30, min=5, max=3600,
    advanced=True,
    label="Treat a node as gone after (seconds)",
    help="A node that has not reported in for this long is shown as out of reach, and a "
         "model on it is passed over. Keep it at least twice the reporting interval.",
))
declare(Setting(
    key="nodes.pickup_timeout_s", section="nodes", type="float", default=8.0, min=1, max=120,
    advanced=True,
    label="Give up handing a request to a node after (seconds)",
    help="How long a model request waits for a connected node to take it before the next "
         "model in the role is tried.",
))
