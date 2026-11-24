"""Settings for backups. Importing this module declares them.

Backups are off until a folder is chosen. Whether a folder is inside the data folder, is
there or can be written to is checked when a backup is made, and the answer is reported
(the Settings screen, the record, a card on the Bench): a drive that is unplugged today
may be plugged in tomorrow, so a folder is not refused for being missing.
"""

from __future__ import annotations

import os

from jimmyhq.settings import Setting, SettingError, declare, section

MAX_PATH = 1000

section("backup", "Backups", group="system", order=80,
        description="Copies of everything JimmyHQ keeps, made on a schedule into folders you "
                    "choose, and a regular test that each copy opens.")


def _folder(value: str) -> str:
    value = value.strip()
    # A path copied from a file manager often arrives wrapped in quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value:
        return ""
    if len(value) > MAX_PATH or any(ord(ch) < 32 for ch in value):
        raise SettingError("A backup folder is one line: the full path of a folder, or empty "
                           "to make no backups there.")
    if not os.path.isabs(os.path.expanduser(value)):
        raise SettingError("A backup folder must be a full path, starting from the top of a "
                           "drive (or from ~ for your home folder), so it means the same "
                           "folder whichever process makes the backup.")
    return value


declare(Setting(
    key="backup.destination", section="backup", type="str", default="",
    portable=False,
    label="Backup folder",
    help="Where backups are made: a folder outside JimmyHQ's data folder, ideally on another "
         "drive. Each backup holds your sign-in and your keys for outside services, so choose "
         "a place only you can read. Leave it empty and no backups are made.",
    validate=_folder,
))
declare(Setting(
    key="backup.second_destination", section="backup", type="str", default="",
    portable=False,
    label="Second backup folder",
    help="Optional: a second place that gets its own copy each time, such as a different "
         "drive or a synced folder. It does not depend on the first: either one can fail "
         "without stopping the other.",
    validate=_folder,
))
declare(Setting(
    key="backup.every_hours", section="backup", type="int", default=24, min=1, max=24 * 30,
    label="Make a backup every (hours)",
    help="How often a backup is made in each folder. Measured from the last one, so it "
         "keeps its rhythm across restarts.",
))
declare(Setting(
    key="backup.retry_hours", section="backup", type="int", default=2, min=1, max=24 * 7,
    label="After a backup fails, try again after (hours)",
    help="Used when a folder could not take a backup, for example while its drive is not "
         "plugged in.",
))
declare(Setting(
    key="backup.keep", section="backup", type="int", default=7, min=1, max=1000,
    label="Backups to keep in each folder",
    help="Older ones are removed only after a new one was made and passed its check, and "
         "the newest good one is never removed. Removing old backups is also how something "
         "you erased eventually leaves them.",
))
declare(Setting(
    key="backup.include_rebuildable", section="backup", type="bool", default=False,
    label="Include the stores JimmyHQ can rebuild",
    help="The stored views, the memory index and the vital signs are made again from the "
         "rest after a restore, so they are left out to keep backups small. Switch this on "
         "to include them, which makes a restore start faster.",
))
declare(Setting(
    key="backup.test_every_days", section="backup", type="int", default=7, min=1, max=90,
    label="Test the newest backup every (days)",
    help="The restore test reads every file of the newest backup in each folder, checks it "
         "against what was written, and opens every database from a temporary copy. It "
         "never touches your data. Each backup also gets a quick check when it is made.",
))
declare(Setting(
    key="backup.warn_after_days", section="backup", type="int", default=3, min=1, max=365,
    label="Tell me when the newest backup is older than (days)",
    help="While backups are on, a card on the Bench says so when a folder's newest good "
         "backup is older than this, or when a test failed. It goes away by itself once a "
         "backup works again.",
))
declare(Setting(
    key="backup.stuck_after_hours", section="backup", type="int", default=12, min=1,
    max=24 * 7,
    label="Tell me when a backup or test has run for longer than (hours)",
    help="A backup that never finishes, for example on a drive that stopped answering, "
         "holds up every one after it. Past this, a card on the Bench says so. Restarting "
         "JimmyHQ stops it, and nothing is lost.",
))
