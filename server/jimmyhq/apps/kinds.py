"""Event kinds for the life of an app: the record of what was installed and agreed to.

Every one is printed with the Tape subject ``app:<id>`` (``subject(app_id)``), so one app's
whole story is a single indexed read, and the ``apps`` lens folds them into what is
installed now. These are the core's kinds about apps. An app's own kinds are named
``app.<id>.*`` and are registered by the app itself.

Permissions are recorded as the manifest wrote them (``"tasks.write"``,
``"net:api.example.com"``), with the fingerprint of what the owner agreed to
(``permissions.fingerprint``), so a later version can be compared with it.
"""

from __future__ import annotations

from typing import Literal

from jimmyhq.tape.registry import Payload, kind

Tier = Literal["sandboxed", "full"]
Source = Literal["folder", "git", "store", "development"]

_COMMIT = "3f2a9c1e5b7d4a6c8e0f1a2b3c4d5e6f70819203"
_TREE = "5d0c7a1e9b3f4d6c8a2e0f1b3d5c7e9a1b2c4d6e8f0a1c3e5b7d9f0a2c4e6b81"
_MARK = "9f2c1e5a7b3d4c6e8f0a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e4f506"
_WHAT = {
    "app_id": "water", "name": "Water", "version": "1.0.0", "tier": "sandboxed",
    "source": "git", "repo": "https://git.example.com/apps/water.git", "commit": _COMMIT,
    "tree": _TREE, "permissions": ["tasks.write"], "fingerprint": _MARK, "reviewed": False,
}


def subject(app_id: str) -> str:
    """The Tape subject every event about one app is printed with."""
    return f"app:{app_id}"


class _Version(Payload):
    """Exactly which code, from where, with what the owner agreed to."""

    app_id: str
    name: str
    version: str
    tier: Tier
    source: Source
    repo: str | None = None  # the git address, for "git" and "store"; never a local path
    commit: str | None = None  # the full commit hash it is pinned to
    tree: str  # sha256 over the sorted list of each path with its file's sha256
    permissions: list[str]
    fingerprint: str  # of what the owner agreed to
    reviewed: bool = False  # listed in the store as reviewed
    ref: str | None = None  # the branch or tag the commit was looked up from, if one was
    example: str | None = None  # the example that ships with JimmyHQ it was copied from


@kind("apps.installed", example=_WHAT)
class AppInstalled(_Version):
    """The owner agreed and the app was installed. It starts enabled."""


@kind("apps.updated", example={**_WHAT, "version": "1.1.0", "from_version": "1.0.0"})
class AppUpdated(_Version):
    """The app was switched to another version. New consent, if it was needed, is its own
    ``apps.permissions_granted`` event printed just before."""

    from_version: str


@kind("apps.permissions_granted", example={
    "app_id": "water", "version": "1.1.0", "tier": "sandboxed",
    "permissions": ["tasks.write", "net:api.example.com"], "added": ["net:api.example.com"],
    "removed": [], "fingerprint": _MARK,
})
class AppPermissionsGranted(Payload):
    """The owner agreed to a changed set of permissions, or to a change of tier."""

    app_id: str
    version: str  # the version the consent was given for
    tier: Tier
    permissions: list[str]  # everything granted from now on
    added: list[str] = []  # what this consent added, as the dialog listed it
    removed: list[str] = []
    fingerprint: str


@kind("apps.enabled", example={"app_id": "water"})
class AppEnabled(Payload):
    app_id: str


@kind("apps.disabled", example={"app_id": "water", "reason": "owner"})
class AppDisabled(Payload):
    """The app was switched off. Its code and data stay."""

    app_id: str
    reason: Literal["owner", "crashing"] = "owner"


@kind("apps.rolled_back", example={
    "app_id": "water", "from_version": "1.1.0", "version": "1.0.0", "reason": "owner",
    "data_restored": False,
})
class AppRolledBack(Payload):
    """The app was switched back to a version that was kept. Its permissions are again
    the ones the owner agreed to for that version."""

    app_id: str
    from_version: str
    version: str
    reason: Literal["owner", "crashing"] = "owner"
    data_restored: bool = False  # that version's backup of the data was put back too
    tree: str | None = None  # which kept copy, when two had the same version


@kind("apps.removed", example={
    "app_id": "water", "version": "1.0.0", "data": "kept", "tape_erased": False,
})
class AppRemoved(Payload):
    """The app was uninstalled: its code, secrets and plug points are gone."""

    app_id: str
    version: str
    data: Literal["kept", "deleted"] = "kept"
    tape_erased: bool = False  # the owner also had what it wrote on the Tape erased


@kind("apps.fetched", example={"app_id": "water", "host": "api.example.com", "size": 2048,
                               "address": "192.0.2.10"})
class AppFetched(Payload):
    """A receipt for one ``net.fetch`` the core made for an app: where and how much,
    never what. ``address`` is the one address it connected to, as checked."""

    app_id: str
    host: str
    size: int  # bytes received
    address: str | None = None  # absent from receipts written before it was kept


@kind("apps.host_added", example={"app_id": "water", "host": "blog.example.com"})
class AppHostAdded(Payload):
    """The owner gave an app that declares ``owner_hosts`` one host of his own choosing
    (``owner_hosts.py``). From now on ``net.fetch`` may reach it exactly as it reaches a
    host its manifest names, and nothing more."""

    app_id: str
    host: str  # one exact public host name, as checked


@kind("apps.host_removed", example={"app_id": "water", "host": "blog.example.com",
                                    "reason": "owner"})
class AppHostRemoved(Payload):
    """A host the owner gave an app was taken away, with the secrets kept for it: by the
    owner, or because the version now in use no longer declares ``owner_hosts`` or allows
    as many. Removing the app takes them all away without this event."""

    app_id: str
    host: str
    reason: Literal["owner", "no_longer_declared"] = "owner"


@kind("apps.host_secret_changed", example={"app_id": "water", "host": "blog.example.com",
                                           "name": "site_password", "action": "set"})
class AppHostSecretChanged(Payload):
    """The owner typed in, or cleared, the secret an app keeps for one host he granted it.
    Which one, for which host, and what was done: never the value, its length or a hash."""

    app_id: str
    host: str
    name: str  # as the manifest declares it
    action: Literal["set", "cleared"]


@kind("apps.local_address_added", example={"app_id": "water",
                                           "address": "http://192.0.2.10:8000",
                                           "resolved": ["192.0.2.10"]})
class AppLocalAddressAdded(Payload):
    """The owner let one app reach one address on his own network (``local_reach.py``):
    exactly that scheme, host and port, and nothing else there. ``net.fetch`` may go to it
    from now on, and only while its name leads to the addresses in ``resolved``, which the
    owner was shown; the app cannot add or change one itself."""

    app_id: str
    address: str  # "<scheme>://<host>:<port>", as checked, the port always written
    # Where its host led when it was added, as the owner was shown. Absent from an event
    # written before these were kept: the owner then confirms it once.
    resolved: list[str] = []


@kind("apps.local_address_confirmed", example={"app_id": "water",
                                               "address": "http://example-server.example:8000",
                                               "resolved": ["192.0.2.11"]})
class AppLocalAddressConfirmed(Payload):
    """The owner agreed to where the name of an address he let one app reach leads now,
    after it led somewhere else: ``resolved`` takes the place of what was stored."""

    app_id: str
    address: str
    resolved: list[str]


@kind("apps.local_address_removed", example={"app_id": "water",
                                             "address": "http://192.0.2.10:8000",
                                             "reason": "owner"})
class AppLocalAddressRemoved(Payload):
    """The owner took back an address on his own network he had let one app reach.
    Removing the app takes them all away without this event, and so does an update or a
    rollback that changes its tier (``apps.updated``, ``apps.rolled_back``)."""

    app_id: str
    address: str
    reason: Literal["owner"] = "owner"


LIFECYCLE = ("apps.installed", "apps.updated", "apps.permissions_granted", "apps.enabled",
             "apps.disabled", "apps.rolled_back", "apps.removed", "apps.host_added",
             "apps.host_removed", "apps.local_address_added", "apps.local_address_confirmed",
             "apps.local_address_removed")


@kind("apps.held_back", example={
    "app_id": "drafts", "version": "0.1.0", "exits": 5, "last_code": 1, "told": True,
    "then": "held",
})
class AppHeldBack(Payload):
    """A full app's program kept stopping soon after it started, so JimmyHQ stopped
    starting it. What happened then is the owner's choice (``apps.crash_loop``): by
    default it stays installed and switched on, held back until Restart; it may instead
    have been switched off, or rolled back to the version before an update (its own
    ``apps.disabled`` or ``apps.rolled_back`` is printed just before this)."""

    app_id: str
    version: str
    exits: int  # quick exits in a row
    last_code: int | None = None  # the last exit code, when there was one
    told: bool = False  # a card was put on the Bench to say so
    then: Literal["held", "switched_off", "rolled_back"] = "held"


@kind("apps.restarted", example={"app_id": "drafts", "was": "crashing"})
class AppRestarted(Payload):
    """The owner pressed Restart: the program was stopped, if it ran, and started again."""

    app_id: str
    was: str  # how it was before, as the Apps screen showed it: running, crashing, ...


@kind("apps.secret_changed", example={"app_id": "posts", "name": "booking_key",
                                      "action": "set"})
class AppSecretChanged(Payload):
    """The owner typed in, or cleared, one secret an app declared. Which one and what
    was done, never the value, its length or a hash of it."""

    app_id: str
    name: str  # as the manifest declares it, without the app.<id>. in front
    action: Literal["set", "cleared"]


class SentFile(Payload):
    ref: str  # the path inside the app's data folder it was read from
    size: int  # its bytes, before any encoding


@kind("apps.file_sent", example={
    "app_id": "posts", "host": "uploads.example.com", "method": "PUT", "form": "raw",
    "size": 204800, "type": "image/jpeg",
    "files": [{"ref": "uploads/0f1e2d3c4b5a69788796a5b4c3d2e1f0.jpg", "size": 204800}]})
class AppFileSent(Payload):
    """A receipt for a request whose body JimmyHQ built from files in an app's own data
    folder (``net.fetch`` with ``file``, ``multipart`` or file fields in ``json``): where,
    how, which files and how much. Never what was in them, or the other fields sent."""

    app_id: str
    host: str
    method: str
    form: Literal["raw", "multipart", "json"]
    size: int  # bytes of the whole body, as sent
    type: str | None = None  # the Content-Type it was sent with
    files: list[SentFile]


@kind("apps.file_received", example={
    "app_id": "posts", "ref": "uploads/0f1e2d3c4b5a69788796a5b4c3d2e1f0.jpg",
    "size": 204800, "type": "image/jpeg"})
class AppFileReceived(Payload):
    """The owner gave an app a picture, and it was kept in the app's own data folder.
    The picture's own name is not kept here: it can say more than the owner meant to."""

    app_id: str
    ref: str  # where it is kept, inside the app's data folder
    size: int
    type: str  # what its bytes are, as JimmyHQ read them


# --- messages from the owner's devices (apps/inbound.py) ------------------------------------


@kind("apps.inbound_key_made", example={"app_id": "home", "key_id": "01J9ZK3V7T8Q2W4E6R8T0Y2X4H",
                                        "label": "Home hub", "in_address": False})
class AppInboundKeyMade(Payload):
    """The owner made a key a device can send messages to an app with. Its label and id,
    never the key, its length or its digest. ``in_address``: the owner made it for a sender
    that can only be given an address, so it may travel in the address itself."""

    app_id: str
    key_id: str
    label: str
    in_address: bool = False


@kind("apps.inbound_key_removed", example={"app_id": "home",
                                           "key_id": "01J9ZK3V7T8Q2W4E6R8T0Y2X4H",
                                           "label": "Home hub"})
class AppInboundKeyRemoved(Payload):
    """The owner removed a key: messages sent with it are refused from now on."""

    app_id: str
    key_id: str
    label: str


@kind("apps.inbound_switched", example={"app_id": "home", "open": True})
class AppInboundSwitched(Payload):
    """The owner opened or closed an app to messages from devices (its own switch)."""

    app_id: str
    open: bool


# --- roles (apps/role_kinds.py): an app's roles filled and emptied, the work it gave out ----
from jimmyhq.apps import role_kinds as _role_kinds  # noqa: E402,F401 - registers the kinds
