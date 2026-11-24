"""Event kinds for backups.

Small on purpose. A destination is named by which of the two it is (``first`` or
``second``) and by ``place``, a short fingerprint of the folder, so the record can tell
one folder from another without saying where on the computer it is. A full path is never
printed. A reason or a problem is a sentence for the owner; a file deeper than the top of
the data folder is named only by the folder it is in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from jimmyhq.tape.registry import Payload, kind

Which = Literal["first", "second"]
_PLACE = r"^[0-9a-f]{12}$"
_SNAPSHOT = r"^jimmyhq-backup-\d{8}T\d{9}Z$"


@kind("backup.made", example={
    "destination": "first", "place": "0123456789ab",
    "name": "jimmyhq-backup-20261122T164800123Z", "files": 412, "bytes": 52_428_800,
    "stores": 9, "left_out": 14, "pruned": 1, "duration_ms": 2300})
class BackupMade(Payload):
    """A snapshot was made, passed its check straight away, and was put in place. Older
    ones past the number to keep were removed after that."""

    destination: Which
    place: str = Field(pattern=_PLACE)
    name: str = Field(pattern=_SNAPSHOT)
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)
    stores: int = Field(ge=0)  # databases copied with SQLite's backup API
    left_out: int = Field(ge=0)  # things left out on purpose (logs, locks, links...)
    pruned: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


@kind("backup.failed", example={
    "destination": "second", "place": "ba9876543210",
    "reason": "The backup folder is not there. If it is on a drive that is not plugged in, "
              "plug it in.", "duration_ms": 12})
class BackupFailed(Payload):
    """A backup could not be made in this folder. Nothing older was removed."""

    destination: Which
    place: str = Field(pattern=_PLACE)
    reason: str = Field(max_length=1000)
    duration_ms: int = Field(ge=0)


@kind("backup.test_passed", example={
    "destination": "first", "place": "0123456789ab",
    "name": "jimmyhq-backup-20261122T164800123Z", "files": 412, "bytes": 52_428_800,
    "duration_ms": 900})
class BackupTestPassed(Payload):
    """The restore test: every file matched its sha256 and every database opened whole,
    at a version this JimmyHQ knows."""

    destination: Which
    place: str = Field(pattern=_PLACE)
    name: str = Field(pattern=_SNAPSHOT)
    files: int = Field(ge=0)
    bytes: int = Field(ge=0)
    duration_ms: int = Field(ge=0)


@kind("backup.test_failed", example={
    "destination": "first", "place": "0123456789ab",
    "name": "jimmyhq-backup-20261122T164800123Z",
    "problems": ["tape.db: Its contents have changed since the backup was made."],
    "duration_ms": 700})
class BackupTestFailed(Payload):
    """The restore test found a problem, or there was no backup to test (``name`` null)."""

    destination: Which
    place: str = Field(pattern=_PLACE)
    name: str | None = Field(default=None, pattern=_SNAPSHOT)
    problems: list[str] = Field(min_length=1, max_length=10)
    duration_ms: int = Field(ge=0)


@kind("backup.restored", example={
    "name": "jimmyhq-backup-20261122T164800123Z", "made_ms": 1_795_366_080_123,
    "version": "0.1.0"})
class BackupRestored(Payload):
    """The owner put a backup in place from the terminal. Printed on the restored Tape, so
    it says itself where it came from. The data that was there before was kept aside."""

    name: str = Field(pattern=_SNAPSHOT)
    made_ms: int = Field(ge=0)
    version: str = Field(max_length=40)  # the JimmyHQ that made the backup
