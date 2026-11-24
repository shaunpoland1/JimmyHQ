"""Settings for chat. Importing this module declares them."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

section("chat", "Chat", group="work", order=10,
        description="How conversations start, what a model remembers, and when old ones go.")

declare(Setting(
    key="chat.system_prompt", section="chat", type="str",
    default="You are a helpful assistant inside a personal productivity tool. Answer clearly "
            "and briefly, and say so when you are not sure.",
    label="Instructions for the model",
    help="Sent to the model at the start of every conversation. Leave empty to send none.",
))
declare(Setting(
    key="chat.context_messages", section="chat", type="int", default=40, min=1, max=2000,
    advanced=True,
    label="Messages to remember",
    help="How many of a conversation's latest messages are sent to the model with each new "
         "one. Older ones are left out, and the screen says how many.",
))
declare(Setting(
    key="chat.context_chars", section="chat", type="int", default=60000, min=1000,
    max=4000000,
    advanced=True,
    label="Characters to remember",
    help="The most text from a conversation that is sent to the model at once. Lower it for "
         "a model with a small memory. Your newest message is always sent whole.",
))
declare(Setting(
    key="chat.local_only", section="chat", type="bool", default=False,
    label="Keep new conversations on my own machines",
    help="New conversations start with \"local only\" switched on: only providers marked "
         "local may answer, and nothing is sent to a hosted model.",
))
declare(Setting(
    key="chat.on_record", section="chat", type="bool", default=True,
    label="Start new conversations on the record",
    help="On: what you and the model say is kept on the Tape, where it can be found again "
         "and stays until you delete the conversation. Off: new conversations start off the "
         "record, so their words are kept apart from the Tape. This only sets how "
         "a new conversation starts. Every conversation has its own switch, which you can "
         "flip at any time, and conversations you already have are not changed.",
))
declare(Setting(
    key="chat.model_titles", section="chat", type="bool", default=False,
    label="Let a model name conversations",
    help="After the first reply, ask the model in the \"cheap\" role for a short title. "
         "A hosted model is only asked when a hosted model wrote the reply, and never "
         "in a \"local only\" conversation. When off, or if that fails, the title is the "
         "start of your first message.",
))
declare(Setting(
    key="chat.max_message_chars", section="chat", type="int", default=200000, min=100,
    max=2000000,
    advanced=True,
    label="Longest message (characters)",
    help="A message longer than this is refused before anything is sent.",
))
declare(Setting(
    key="chat.use_memory", section="chat", type="bool", default=True,
    label="Use memory in new conversations",
    help="On: before each reply, chat looks in your memory for notes that bear on what "
         "you asked and shows them to the model. Every conversation has its own \"Use "
         "memory\" switch, which you can flip at any time; this only sets how a new one "
         "starts.",
))
declare(Setting(
    key="chat.memory_to_hosted", section="chat", type="bool", default=False,
    label="Let hosted models see memory",
    help="Off: what chat recalls from memory is only ever sent to a provider marked local. "
         "When a hosted model would answer, the reply is written without memory and says "
         "so, and a reply that carries memory never falls back to a hosted model. On: "
         "recalled notes may be sent to whichever model answers, hosted ones included.",
))
declare(Setting(
    key="chat.memory_passages", section="chat", type="int", default=0, min=0, max=50,
    advanced=True,
    label="Memory passages per reply",
    help="The most passages chat asks memory for before a reply. 0 uses memory's own "
         "setting.",
))
declare(Setting(
    key="chat.memory_chars", section="chat", type="int", default=0, min=0, max=200000,
    advanced=True,
    label="Memory characters per reply",
    help="The most text from memory that is put in front of the model for one reply. "
         "Passages are never cut: one that does not fit is left out. 0 uses memory's own "
         "setting.",
))
declare(Setting(
    key="chat.memory_timeout_s", section="chat", type="float", default=3.0, min=0.2, max=60,
    advanced=True,
    label="Longest wait for memory (seconds)",
    help="If searching memory takes longer than this, the reply is written without it and "
         "says so.",
))
declare(Setting(
    key="chat.silent_reply_limit_s", section="chat", type="int", default=240, min=0, max=3600,
    advanced=True,
    label="Longest silent wait for a reply (seconds)",
    help="A reply that has written no text and looked nothing up on the web for this long is "
         "stopped, and the conversation says what happened. Models that reason before they "
         "answer can be quiet for a while; raise this for a slow one, or lower the effort for "
         "that conversation instead. 0 never stops a quiet reply. A reply that is writing, or "
         "looking things up, is never stopped by this.",
))
declare(Setting(
    key="chat.delete_archived_after_days", section="chat", type="int", default=0, min=0,
    max=3650,
    label="Delete archived conversations after (days)",
    help="0 keeps archived conversations until you delete them yourself. Any other number: "
         "a conversation that has stayed archived for that many days is marked for deletion, "
         "and once the grace period below has passed it is deleted for good, on the record "
         "and off it, and cannot be brought back. The archived list shows the day. Restoring "
         "a conversation stops the clock, and archiving it again starts a new one. Changing "
         "this number, or setting it back to 0, takes every mark away, so nothing is ever "
         "deleted sooner than the grace period after you last changed it. Facts that memory "
         "already saved from a conversation are kept.",
))
declare(Setting(
    key="chat.delete_grace_hours", section="chat", type="int", default=24, min=1, max=8760,
    label="Grace period before an archived conversation is deleted (hours)",
    help="How long a conversation stays marked for deletion before it is really deleted. "
         "During that time restoring it, or changing either of these two settings, saves "
         "it. It is the safety margin against a mistyped number of days or a wrong clock.",
))
