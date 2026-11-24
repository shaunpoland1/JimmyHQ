"""Settings for apps, all on the page ``apps``. Importing this module declares the ones
about installing and removing here, and the limits on a running app in ``quotas``."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

section("apps", "Apps", group="apps", order=10,
        description="Installing and removing apps, and the limits every app is held to.")
# The page is long, so it is in headed parts. A setting's key never says which part it is
# in: moving one between parts changes nothing stored or exported.
section("apps_install", "Installing, updating and removing", parent="apps", order=10,
        description="Where an app may come from, how it is updated, how large it may be, "
                    "and what Remove keeps.")
section("apps_storage", "What an app may store", parent="apps", order=20,
        description="How much each app may keep in its own storage.")
section("apps_limits", "What an app may use", parent="apps", order=30,
        description="How often an app may ask JimmyHQ, your models and the network for something.")

declare(Setting(
    key="apps.unreviewed_full_from_url", section="apps_install", type="bool", default=True,
    label="Allow full apps that have not been reviewed, from a URL",
    help="A full app runs as you on this computer. When on, you can install one from a git "
         "address after reading a warning and typing its name. When off, a full app can "
         "only be installed from a folder already on this computer.",
))
declare(Setting(
    key="apps.remove_keeps_data", section="apps_install", type="bool", default=True,
    label="Keep an app's data when it is removed",
    help="What Remove does unless you choose otherwise at the time. Kept data is there "
         "again if you install the app later.",
))
declare(Setting(
    key="apps.auto_update", section="apps_install", type="choice", default="wait",
    choices=(("wait", "Wait for my click"),
             ("auto", "Update by itself when it asks for nothing new")),
    label="When a new version of an app is found",
    help="Waiting, the Apps screen says \"Update available\" and nothing changes until you "
         "press Update. By itself, an update that asks for no new permission and keeps its "
         "tier is put in place for you and you are told on the Bench; one that asks for "
         "more always waits for you. Each app's own settings can choose differently.",
))
declare(Setting(
    key="apps.update_check_hours", section="apps_install", type="int", default=24, min=0,
    max=168,
    label="Look for new versions every (hours)",
    help="For an app installed from a git address with a branch or tag name, JimmyHQ asks "
         "that address which commit the name points at now; for an app that comes with "
         "JimmyHQ, it compares the copy that ships. Nothing is fetched or installed by "
         "looking. 0 never looks.",
))
declare(Setting(
    key="apps.keep_versions", section="apps_install", type="int", default=2, min=1, max=10,
    advanced=True,
    label="Versions of each app to keep",
    help="The version in use and the ones before it, with the copy of the app's data taken "
         "when it was replaced, so Roll back can return to them. Older ones are deleted.",
))
declare(Setting(
    key="apps.max_tree_mb", section="apps_install", type="int", default=50, min=1, max=2000,
    advanced=True,
    label="Largest app (megabytes)",
    help="An app whose files add up to more than this is refused before it is installed.",
))
declare(Setting(
    key="apps.max_file_mb", section="apps_install", type="int", default=10, min=1, max=500,
    advanced=True,
    label="Largest file in an app (megabytes)",
    help="An app with any one file larger than this is refused.",
))
declare(Setting(
    key="apps.max_files", section="apps_install", type="int", default=2000, min=1, max=50_000,
    advanced=True,
    label="Most files in an app",
    help="An app with more files than this is refused.",
))
declare(Setting(
    key="apps.fetch_timeout_s", section="apps_install", type="int", default=120, min=5, max=3600,
    advanced=True,
    label="Give up fetching an app after (seconds)",
    help="How long git may take to fetch an app from its address before the install is "
         "stopped.",
))
declare(Setting(
    key="apps.plan_minutes", section="apps_install", type="int", default=30, min=1, max=1440,
    advanced=True,
    label="Keep a fetched app waiting for your answer (minutes)",
    help="After you are shown what an app asks for, it waits this long for Install. After "
         "that it is thrown away and you start again.",
))
declare(Setting(
    key="apps.git_path", section="apps_install", type="str", default="", portable=False,
    label="Where git is",
    help="The full path of the git program used to fetch apps. Leave empty to find it the "
         "usual way.",
))
declare(Setting(
    key="apps.uv_path", section="apps_install", type="str", default="", portable=False,
    label="Where uv is",
    help="The full path of the uv program, which installs the exact libraries a full app "
         "locked. Leave empty to find it the usual way. Without uv, a full app runs with "
         "JimmyHQ's own Python.",
))
declare(Setting(
    key="apps.uv_timeout_s", section="apps_install", type="int", default=600, min=30,
    max=7200, advanced=True,
    label="Give up installing an app's libraries after (seconds)",
    help="How long uv may take to install what a full app locked before the install or "
         "update is stopped.",
))

section("apps_full", "Full apps", parent="apps", order=40,
        description="How JimmyHQ runs a full app's program and talks with it.")
declare(Setting(
    key="apps.crash_limit", section="apps_full", type="int", default=5, min=1, max=50,
    advanced=True,
    label="Stop restarting an app after this many quick exits",
    help="A full app's program that stops this many times in a row, each time within a "
         "minute of starting, is shown as \"Keeps crashing\" and not started again until "
         "you restart it.",
))
declare(Setting(
    key="apps.crash_loop", section="apps_full", type="choice", default="hold_and_tell",
    choices=(("hold_and_tell", "Hold it back and tell me"),
             ("hold", "Hold it back, and only show it on the Apps screen"),
             ("keep_trying", "Keep restarting it, waiting longer each time"),
             ("switch_off", "Switch it off and tell me"),
             ("roll_back", "Go back to the version before, if it began right after an "
                           "update, and tell me")),
    label="When a full app keeps crashing",
    help="A full app's program that stops again and again soon after starting. Held back, "
         "it stays stopped until you press Restart on the Apps screen, and \"tell me\" also "
         "puts a card on your Bench. Switched off, you switch it on again yourself. Going "
         "back happens only when the crashing began with a new version and the version "
         "before is still kept. Otherwise it is held back and you are told.",
))
declare(Setting(
    key="apps.crash_rollback_restores_data", section="apps_full", type="bool", default=True,
    label="Going back after crashing also puts the old data back",
    help="When an app goes back to the version before because the new one kept crashing, "
         "its data goes back too, to the copy taken just before the update. What the new "
         "version wrote is kept as a backup, not thrown away. Off, only the program goes "
         "back and the data stays as the new version left it.",
))
declare(Setting(
    key="apps.stop_wait_s", section="apps_full", type="int", default=30, min=5, max=600,
    advanced=True,
    label="Wait for a full app's program to stop before changing its version (seconds)",
    help="Updating or rolling back stops the program first. If it has not stopped in this "
         "time, nothing is changed and you are asked to try again.",
))
declare(Setting(
    key="apps.job_timeout_s", section="apps_full", type="int", default=600, min=10,
    max=86_400, advanced=True,
    label="Give up on a full app's background job after (seconds)",
    help="A run of a job that the app has not finished in this time is recorded as not "
         "answering. The next run comes at its usual time.",
))
declare(Setting(
    key="apps.collect_timeout_s", section="apps_full", type="int", default=60, min=5,
    max=600, advanced=True,
    label="Wait for a full app to give Memory what it collected for (seconds)",
    help="When Memory reads an app's source and the app has not answered in this time, "
         "Memory tries again at its next pass.",
))
declare(Setting(
    key="apps.tool_timeout_s", section="apps_full", type="int", default=60, min=5,
    max=600, advanced=True,
    label="Wait for a full app to run an agent's tool for (seconds)",
    help="An agent that used a tool a full app offers is told it did not work after this "
         "long. It is not tried again.",
))
declare(Setting(
    key="apps.not_responding_s", section="apps_full", type="int", default=90, min=10,
    max=3600, advanced=True,
    label="Show an app as not responding after (seconds)",
    help="A running full app asks JimmyHQ for work all the time. When it has not asked for "
         "this long, the Apps screen says \"Not responding\".",
))
declare(Setting(
    key="apps.inbox_wait_s", section="apps_full", type="int", default=25, min=1, max=60,
    advanced=True,
    label="Hold an app's request for work open for up to (seconds)",
    help="How long JimmyHQ keeps a full app's request for work open when there is none, "
         "before answering that there is nothing.",
))
declare(Setting(
    key="apps.redeliver_s", section="apps_full", type="int", default=300, min=5,
    max=86_400, advanced=True,
    label="Offer unanswered work again after (seconds)",
    help="Work handed to a full app that it has not answered in this time is handed over "
         "again, in case the app stopped part-way. Apps are written to cope with that.",
))
declare(Setting(
    key="apps.count_refresh_s", section="apps_full", type="int", default=60, min=10,
    max=3600, advanced=True,
    label="Look again at the counts beside apps every (seconds)",
    help="A full app tells JimmyHQ how many things wait on you, and the number beside it in "
         "the sidebar is read again this often while a page is open, so it is right without "
         "opening the app.",
))
declare(Setting(
    key="apps.call_timeout_s", section="apps_full", type="int", default=30, min=1, max=300,
    advanced=True,
    label="Wait for an app's own program to answer its page for (seconds)",
    help="When a full app's screen asks its program for something, the screen is told it "
         "did not work after this long, and the call is not carried out later. Work that "
         "takes longer is meant to answer at once and let the screen ask again for the "
         "result.",
))
declare(Setting(
    key="apps.call_max_kb", section="apps_full", type="int", default=1024, min=1,
    max=16_384, advanced=True,
    label="Largest answer from an app's program (KB)",
    help="An answer a full app's program gives larger than this is refused.",
))
declare(Setting(
    key="apps.max_kinds", section="apps_full", type="int", default=100, min=1, max=2000,
    advanced=True,
    label="Most kinds of event one app may declare",
    help="Each version of each kind of event a full app writes on your record counts once.",
))

# Files and secrets an app is handed or sends (secrets_entry, uploads, file_body).
declare(Setting(
    key="apps.upload_max_mb", section="apps_storage", type="int", default=25, min=1,
    max=2000,
    label="Largest picture you can give an app (MB)",
    help="A picture you choose on an app's screen is kept in that app's own storage. One "
         "larger than this is refused and nothing of it is kept. It also counts towards "
         "the app's storage.",
))
declare(Setting(
    key="apps.thumbnail_px", section="apps_storage", type="int", default=320, min=64,
    max=2048, advanced=True,
    label="Longest side of a picture's small version (pixels)",
    help="When an app's screen asks for one of its pictures to show, JimmyHQ makes it no "
         "larger than this on its longest side. The picture kept in the app's storage is "
         "not changed.",
))
# The full view (``files.picture``): the same shell-side path as the small version, larger.
declare(Setting(
    key="apps.picture_px", section="apps_storage", type="int", default=2048, min=64,
    max=8192, advanced=True,
    label="Longest side of a picture shown in full (pixels)",
    help="When an app's screen shows one of its own pictures large, JimmyHQ draws it no "
         "larger than this. The kept picture is not changed.",
))
declare(Setting(
    key="apps.picture_max_mb", section="apps_storage", type="int", default=25, min=1,
    max=200, advanced=True,
    label="Largest picture an app's screen may show in full (MB)",
    help="A kept picture larger than this is not read to be shown in full, and the app is "
         "told so.",
))
declare(Setting(
    key="apps.pictures_per_minute", section="apps_storage", type="int", default=60, min=1,
    max=600, advanced=True,
    label="Pictures an app's screen may show in full a minute",
    help="More than this in a minute from one open screen is refused until the minute is "
         "over.",
))
declare(Setting(
    key="apps.net_fetch_file_max_mb", section="apps_limits", type="int", default=100, min=1,
    max=10_000, advanced=True,
    label="Largest file an app may send (MB)",
    help="The most one file from an app's own storage may weigh when JimmyHQ sends it to a "
         "host you allowed that app. A larger one is refused and nothing is sent.",
))
declare(Setting(
    key="apps.net_fetch_body_max_mb", section="apps_limits", type="int", default=150, min=1,
    max=20_000, advanced=True,
    label="Largest request with files an app may send (MB)",
    help="The most one request JimmyHQ builds from an app's files may weigh in all, as "
         "sent: every file and field together, and a file sent as text takes a third more.",
))
declare(Setting(
    key="apps.net_fetch_file_timeout_s", section="apps_limits", type="int", default=300,
    min=5, max=3600, advanced=True,
    label="Longest wait for an app's file to be sent (seconds)",
    help="How long JimmyHQ keeps sending a file for an app, and waits for the host's "
         "answer, before it gives up.",
))
declare(Setting(
    key="apps.net_fetch_at_once", section="apps_limits", type="int", default=3, min=1,
    max=50, advanced=True,
    label="Network requests one app may have open at once",
    help="One more is refused until one of them finishes, so a single app cannot tie up "
         "JimmyHQ, or a service, with many at a time.",
))
declare(Setting(
    key="apps.local_addresses_max", section="apps_limits", type="int", default=3, min=1,
    max=10, advanced=True,
    label="Addresses on your own network one app may be given",
    help="You add each one yourself on the app's page under Apps. The app reaches only "
         "those, exactly.",
))
declare(Setting(
    key="apps.dictate_max_s", section="apps_limits", type="int", default=300, min=5,
    max=3600,
    label="Longest dictation for an app (seconds)",
    help="When an app asks you to dictate, its recording stops by itself after this long "
         "and what was said is turned into text. The limit under Settings, Voice also "
         "applies, whichever is shorter.",
))
declare(Setting(
    key="apps.save_max_mb", section="apps_limits", type="int", default=8, min=1, max=100,
    advanced=True,
    label="Largest file an app may offer you to keep (MB)",
    help="When an app's screen hands you a file to save (an export, a picture), one larger "
         "than this is refused and nothing is saved. Your browser then asks where to keep it.",
))
declare(Setting(
    key="apps.save_parts", section="apps_limits", type="int", default=5, min=1, max=10,
    advanced=True,
    label="Most files an app may offer from one click",
    help="An app that splits an export into parts may hand you this many files after a "
         "single click, one straight after the other. You see each one named as it is "
         "saved. More than this needs another click. Your browser may ask whether to allow "
         "several downloads.",
))
declare(Setting(
    key="apps.save_per_minute", section="apps_limits", type="int", default=12, min=1,
    max=60,
    label="Most files an app may offer in one minute",
    help="However many times you click, one app hands you no more files than this in any "
         "minute. More waits for the next minute.",
))
declare(Setting(
    key="apps.save_html", section="apps_limits", type="bool", default=True,
    label="Let an app save a web page (.html) for you",
    help="A saved page is only ever handed to your browser to download, and JimmyHQ puts a "
         "rule at the top of it that stops scripts, forms and outside loads. Opened later, "
         "its links still work if you click them. Off, an app that asks for a page gets a "
         "plain text file instead.",
))
declare(Setting(
    key="apps.app_key_max_chars", section="apps_limits", type="int", default=4000, min=16,
    max=65_536, advanced=True,
    label="Longest secret you can type in for an app (characters)",
    help="A key or token for an outside service, typed in on an app's own settings page.",
))

# --- messages from the owner's devices (inbound.py): POST /api/in/<app>/<name> -------------
section("apps_inbound", "Messages from your devices", parent="apps", order=50,
        description="A device on your network, such as your home's hub, can send a short "
                    "message to an app that asks for it, with a key you make.")
declare(Setting(
    key="apps.inbound_enabled", section="apps_inbound", type="bool", default=True,
    label="Let your devices send messages to apps",
    help="Off, the address devices send to answers every message with a refusal, whatever "
         "key it carries. Each app also has its own switch on its page, off until you make "
         "it a key.",
))
declare(Setting(
    key="apps.inbound_max_kb", section="apps_inbound", type="int", default=1024, min=1,
    max=1024, advanced=True,
    label="Most any device may send in one message (KB)",
    help="The ceiling for every app. An app that says how large its messages get is allowed "
         "that much, up to this; one that does not is held to the next setting. A larger "
         "message is refused before it is read.",
))
declare(Setting(
    key="apps.inbound_default_kb", section="apps_inbound", type="int", default=16, min=1,
    max=1024, advanced=True,
    label="Message size for an app that does not say what it expects (KB)",
    help="Used for a message name whose app does not say how large its messages get. "
         "Never more than the ceiling above.",
))
declare(Setting(
    key="apps.inbound_query_max", section="apps_inbound", type="int", default=20, min=0,
    max=200, advanced=True,
    label="Most parameters a sender may add to its address",
    help="Some senders add their own, such as ?uid=..., which the app is given. A message "
         "with more is refused.",
))
declare(Setting(
    key="apps.inbound_query_chars", section="apps_inbound", type="int", default=500, min=16,
    max=8000, advanced=True,
    label="Longest parameter a sender may add to its address (characters)",
    help="A message whose address carries a longer name or value is refused.",
))
declare(Setting(
    key="apps.inbound_keys_max", section="apps_inbound", type="int", default=5, min=1,
    max=50, advanced=True,
    label="Most keys for one app",
    help="Make one key for each device or service, so you can take one away by itself.",
))
declare(Setting(
    key="apps.inbound_per_minute", section="apps_inbound", type="int", default=120, min=1,
    max=6000,
    label="Messages one key may send each minute",
    help="Past this a device is told to wait and send again.",
))
declare(Setting(
    key="apps.inbound_bad_per_minute", section="apps_inbound", type="int", default=20, min=1,
    max=1000,
    label="Refused messages allowed from one address each minute",
    help="Past this, a message from that address without a right key is turned away for a "
         "while. A message with a right key always gets through, so a stranger cannot lock "
         "your devices out. Devices behind one proxy share its address.",
))
declare(Setting(
    key="apps.inbound_bad_all_per_minute", section="apps_inbound", type="int", default=200,
    min=1, max=10_000,
    label="Refused messages allowed from all addresses together each minute",
    help="Past this, every message without a right key is turned away for a while. A "
         "message with a right key always gets through.",
))
declare(Setting(
    key="apps.inbound_block_s", section="apps_inbound", type="int", default=300, min=10,
    max=86_400, advanced=True,
    label="How long an address is turned away (seconds)",
    help="After too many refused messages from one address, messages from it without a "
         "right key are turned away for this long.",
))
declare(Setting(
    key="apps.inbound_receipts_max", section="apps_inbound", type="int", default=200,
    min=10, max=5000, advanced=True,
    label="Receipts kept for each app",
    help="Each message leaves a line on the app's page: its name, the key's name, the time, "
         "its size and how it went, never what it said. Older lines go.",
))
declare(Setting(
    key="apps.inbound_queue_max", section="apps_inbound", type="int", default=500, min=1,
    max=10_000, advanced=True,
    label="Messages waiting for one app, at most",
    help="While an app's program is stopped or slow, messages wait for it. Past this a "
         "device is told to wait and send again, and nothing waiting is dropped.",
))

# The limits on what a running app may use live in the same section. Imported last: the
# section above must exist first, and every process that loads settings then has them.
from jimmyhq.apps import quotas  # noqa: E402, F401

# --- roles (apps/roles_settings.py): how far an app may lean on the owner's agents ----------
from jimmyhq.apps import roles_settings as _roles_settings  # noqa: E402,F401
