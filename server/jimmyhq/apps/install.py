"""Installing an app takes two calls, and the owner's answer sits between them.

1. ``plan_folder(core, folder)``, ``plan_example(core, id)`` (one of the examples that
   ship with JimmyHQ) or ``plan_git(core, url, commit=...)`` fetches the app
   into a staging folder, proves the files are safe to keep (``store``), reads the manifest
   (``manifest.load``), checks it against what is installed, and returns a **plan**: who and
   what it is, the permission sentences, the warnings, the tree hash and the fingerprint
   of what the owner is asked to agree to. Nothing is installed.
2. ``confirm(core, plan_id, fingerprint)`` installs exactly the staged files, if the
   fingerprint is the plan's and the staged files still hash to what was shown. It prints
   ``apps.installed``; the app starts enabled.

``remove``, ``enable`` and ``disable`` are the rest of an app's life that this package owns.

**Git, and why nothing from a fetched tree can run.** Git is run as an argument list with a
timeout, never through a shell, with ``--`` before the address. The fetch goes into a bare
repository made from an empty template, and the files are then read out of git's object
store one blob at a time (``ls-tree`` and ``cat-file --batch``) and written by this module.
There is never a checkout, so there is no working tree for a hook, a smudge or LFS filter,
a ``.gitattributes`` or a ``.gitmodules`` to act on. On top of that (``git_options``):

- ``protocol.allow=never`` with only https allowed, and ``GIT_ALLOW_PROTOCOL`` set the same
  way in a rebuilt environment: no ``ext::``, ``file://``, ``ssh`` or ``git://``.
- ``core.hooksPath`` points at an empty folder; ``init --template`` at the same one.
- ``fetch.recurseSubmodules=false``, ``submodule.recurse=false``, ``--no-recurse-submodules``;
  a submodule entry in the tree is refused.
- ``filter.lfs.*`` emptied and ``GIT_LFS_SKIP_SMUDGE=1``.
- ``fetch.fsckObjects=true``: malformed trees (``..``, ``.git``) are refused by git too.
- ``transfer.bundleURI=false``, ``gc.auto=0``, ``maintenance.auto=false``,
  ``core.fsmonitor=false``, ``GIT_TERMINAL_PROMPT=0``: nothing else is fetched or started.

A symbolic link in the tree (mode 120000) is refused before any file is written.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from jimmyhq import ids
from jimmyhq.apps import described, feeds, kinds, storage_ask, store
from jimmyhq.apps import manifest as mf
from jimmyhq.apps import permissions as perms
from jimmyhq.apps.store import AppError
from jimmyhq.tape.store import TapeConflict

if TYPE_CHECKING:
    from jimmyhq.core import Core

log = logging.getLogger("jimmyhq.apps.install")

NOT_REVIEWED = "Not reviewed. Install only if you trust who wrote it."
REVIEWED = "Reviewed and listed in the store."
BUNDLED_REVIEW = "Comes with JimmyHQ. Made by the JimmyHQ project."
# Where a planned app comes from, for the words the owner reads. "bundled" is only ever
# worked out here (``bundled_example``); nothing an app or a caller says can make it so.
BUNDLED, FOLDER, GIT = "bundled", "folder", "git"
_PLAN_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}\Z")
_COMMIT = re.compile(r"^[0-9a-f]{40}\Z")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?\Z")
_URL_PATH = re.compile(r"^(/[A-Za-z0-9._~-]+)+/?\Z")
_GIT_ENV_KEEP = ("PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
                 "LOCALAPPDATA", "PROGRAMDATA", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
                 "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
                 "XDG_CONFIG_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE",
                 "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY",
                 "all_proxy")
_FILE_MODES = ("100644", "100755")


# --- what may be asked for ------------------------------------------------------------------


def _url_refusal(why: str) -> AppError:
    return AppError(422, (
        f"You cannot install from that address: {why} Nothing was fetched or installed. "
        "Give an https address of a git repository, such as "
        "https://git.example.com/apps/water.git."))


def check_git_url(url: Any) -> str:
    """An https address of a git repository and nothing else, or ``AppError``.

    Refused: any other scheme or transport (``ext::``, ``ssh``, ``git``, ``file``, ``http``,
    a local path), anything git could read as an option, a name and password in the
    address (it is recorded), a query or fragment, spaces and control characters."""
    if not isinstance(url, str) or not url:
        raise _url_refusal("it is empty.")
    if len(url) > 500:
        raise _url_refusal("it is longer than 500 characters.")
    if not url.isascii() or any(c.isspace() or ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        raise _url_refusal("it has a space or a character that does not belong in an address.")
    if url.startswith("-"):
        raise _url_refusal("it starts with a hyphen, which git would read as an option.")
    if "::" in url:
        raise _url_refusal("it names a helper program for git to run, which is never allowed.")
    if not url.startswith("https://"):
        raise _url_refusal("only https addresses are fetched.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise _url_refusal("it is not a well-formed address.") from None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _url_refusal("it has a sign-in name or password in it, which would be kept in "
                           "your record.")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise _url_refusal("it has a query or a fragment after the path.")
    host = parts.hostname or ""
    if not _HOST.match(host) or ".." in host:
        raise _url_refusal("its host name is not valid.")
    if port is not None and not 1 <= port <= 65535:
        raise _url_refusal("its port is not valid.")
    if not _URL_PATH.match(parts.path) or "/../" in parts.path + "/" or "/./" in parts.path + "/":
        raise _url_refusal("its path must be plain folder and repository names.")
    return url


def check_commit(commit: Any) -> str:
    """A full commit hash: 40 hexadecimal characters, lower case."""
    if not isinstance(commit, str) or not _COMMIT.match(commit):
        raise AppError(422, (
            "You cannot install without an exact commit: give the full 40-character commit "
            "hash in lower case, or a branch or tag name to look it up. Nothing was fetched "
            "or installed."))
    return commit


def check_ref(ref: Any) -> str:
    """A branch or tag name that git cannot read as an option or a path trick."""
    if (not isinstance(ref, str) or not _REF.match(ref) or ".." in ref or "//" in ref
            or ref.endswith(("/", ".", ".lock"))):
        raise AppError(422, (
            "That is not a branch or tag name JimmyHQ can look up. Nothing was fetched or "
            "installed. Give a plain name such as main or v1.0.0, or the full commit hash."))
    return ref


def check_plan_id(plan_id: Any) -> str:
    """A plan's id is a ULID and nothing else, so it can never be a path."""
    if not isinstance(plan_id, str) or not _PLAN_ID.match(plan_id):
        raise AppError(404, (
            "That is not an install JimmyHQ is waiting on. Nothing was installed. Start "
            "the install again."))
    return plan_id


def check_id_is_free(core: Core, m: mf.Manifest) -> None:
    """Refuse an app that is already installed."""
    row = store.get(core, m.id)
    if row is not None:
        raise AppError(409, (
            f"You already have {row['name']} {row['version']} installed, with the same id "
            f"\"{m.id}\". Nothing was changed. Remove it first if you want to install this "
            "one in its place; its data is kept unless you say otherwise."))


def check_full_allowed(core: Core, tier: str, source: str) -> None:
    """A full app that nobody reviewed comes from a URL only if the owner allows that."""
    if (tier == perms.FULL and source == "git"
            and not core.settings.get("apps.unreviewed_full_from_url")):
        raise AppError(403, (
            "You cannot install this app from a URL: it is a full app that has not been "
            "reviewed, and Settings, Apps has that switched off. Nothing was installed. "
            "Switch it on there if you trust who wrote it, or install from a folder on "
            "this computer."))


def check_typed_name(m: mf.Manifest, typed: Any) -> None:
    """An unreviewed full app is installed only when its name was typed out."""
    if m.tier == perms.FULL and (not isinstance(typed, str) or typed.strip() != m.name):
        raise AppError(422, (
            f"Nothing was installed. \"{m.name}\" is a full app that has not been "
            f"reviewed: type its name, {m.name}, exactly, to show you mean it."))


# --- git ------------------------------------------------------------------------------------


def git_options(protocol: str, empty: Path) -> list[str]:
    """The settings every git command here runs with. See the module's notes for why
    each one is there. They come first on the command line and win over any config."""
    pairs = (
        ("protocol.allow", "never"), (f"protocol.{protocol}.allow", "always"),
        ("core.hooksPath", str(empty)), ("fetch.recurseSubmodules", "false"),
        ("submodule.recurse", "false"), ("fetch.fsckObjects", "true"),
        ("transfer.bundleURI", "false"), ("filter.lfs.smudge", ""),
        ("filter.lfs.process", ""), ("filter.lfs.required", "false"),
        ("core.fsmonitor", "false"), ("core.symlinks", "false"), ("gc.auto", "0"),
        ("maintenance.auto", "false"), ("credential.interactive", "false"),
        ("core.askPass", ""),
    )
    out: list[str] = []
    for key, value in pairs:
        out += ["-c", f"{key}={value}"]
    return out


def git_env(protocol: str) -> dict[str, str]:
    """A rebuilt environment: nothing named ``GIT_*`` is inherited, so no caller's
    variable can swap the transport, the config or the repository."""
    env = {k: os.environ[k] for k in _GIT_ENV_KEEP if k in os.environ}
    env |= {"GIT_ALLOW_PROTOCOL": protocol, "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1", "GIT_OPTIONAL_LOCKS": "0", "GCM_INTERACTIVE": "never",
            "LC_ALL": "C"}
    return env


def git_program(core: Core) -> str:
    chosen = str(core.settings.get("apps.git_path") or "").strip()
    found = chosen if chosen and Path(chosen).is_file() else shutil.which("git")
    if not found:
        raise AppError(503, (
            "JimmyHQ could not find git on this computer, so nothing was fetched or "
            "installed. Install git, or give its full path in Settings, Apps."))
    return found


def _git(core: Core, args: list[str], *, protocol: str, empty: Path,
         stdin: bytes | None = None, what: str) -> bytes:
    argv = [git_program(core), *git_options(protocol, empty), *args]
    timeout = float(core.settings.get("apps.fetch_timeout_s"))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(  # noqa: S603 - an argument list, never a shell
            argv, input=stdin, stdin=subprocess.DEVNULL if stdin is None else None,
            capture_output=True, timeout=timeout, env=git_env(protocol), cwd=str(empty),
            creationflags=flags, check=False)
    except subprocess.TimeoutExpired:
        raise AppError(504, (
            f"JimmyHQ gave up {what} after {int(timeout)} seconds. Nothing was installed. "
            "Check the address and your connection, or allow longer in Settings, "
            "Apps.")) from None
    except OSError:
        raise AppError(503, (
            "JimmyHQ could not start git, so nothing was fetched or installed. Check the "
            "path of git in Settings, Apps.")) from None
    if done.returncode != 0:
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        last = (said[-1] if said else "nothing")[:200]
        raise AppError(502, (
            f"JimmyHQ failed {what}, so nothing was installed. Check the address and the "
            f"commit, and that this computer can reach it. Git said: {last}"))
    return done.stdout


def _transport(url: str) -> tuple[str, str]:
    """The address handed to git and the one protocol it may use for it."""
    return url, "https"


def resolve_ref(core: Core, url: str, ref: str, *, empty: Path) -> str:
    """The commit a branch or tag names right now. The install is pinned to the commit;
    the name is only shown."""
    check_ref(ref)
    remote, protocol = _transport(url)
    out = _git(core, ["ls-remote", "--quiet", "--", remote, f"refs/heads/{ref}",
                      f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"],
               protocol=protocol, empty=empty, what="looking up that name").decode("ascii",
                                                                                   "replace")
    found = dict(reversed(line.split("\t", 1)) for line in out.splitlines() if "\t" in line)
    for name in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}"):
        if name in found and _COMMIT.match(found[name]):
            return found[name]
    raise AppError(404, (
        f"That repository has no branch or tag called \"{ref}\". Nothing was installed. "
        "Check the name, or give the full commit hash."))


def fetch_commit(core: Core, remote: str, commit: str, repo: Path, *, empty: Path,
                 protocol: str = "https") -> None:
    """Fetch one commit, and nothing older, into a new bare repository at ``repo``."""
    check_commit(commit)
    _git(core, ["init", "--quiet", "--bare", f"--template={empty}", str(repo)],
         protocol=protocol, empty=empty, what="preparing to fetch")
    _git(core, ["-C", str(repo), "fetch", "--quiet", "--depth=1", "--no-tags",
                "--no-recurse-submodules", "--", remote, commit],
         protocol=protocol, empty=empty, what="fetching the app")
    kind = _git(core, ["-C", str(repo), "cat-file", "-t", commit],
                protocol=protocol, empty=empty, what="reading what was fetched")
    if kind.strip() != b"commit":
        raise AppError(422, (
            "That hash does not name a commit in the repository. Nothing was installed. "
            "Give the full hash of the commit to install."))


def read_listing(listing: bytes, lim: store.Limits) -> tuple[list[tuple[str, str, int]], int]:
    """Check ``git ls-tree -r -l -z`` output before a single file is written.

    Returns (object, path, size) for each file to write, and how many were left out.
    Refuses links, submodules, unsafe paths, names differing by case, and anything over
    the size limits."""
    entries: list[tuple[str, str, int]] = []
    seen: dict[str, str] = {}
    total = skipped = 0
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        try:
            head, name = raw.split(b"\t", 1)
            mode, kind, oid, size = head.decode("ascii").split()
            path = name.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise AppError(422, (
                "You cannot install this app, and nothing was installed: it has a file "
                "whose name cannot be read. Its author needs to rename it.")) from None
        if store.path_left_out(path):
            skipped += 1
            continue
        if mode == "120000":
            raise store._link_refusal(path)  # noqa: SLF001
        if mode == "160000" or kind == "commit":
            raise AppError(422, (
                f"You cannot install this app, and nothing was installed: \"{path}\" is a "
                "submodule, another repository inside this one. JimmyHQ never fetches "
                "those. The app's author needs to put the files in the app itself."))
        if kind != "blob" or mode not in _FILE_MODES or not size.isdigit():
            raise store._special_refusal(path)  # noqa: SLF001
        store.check_tree_path(path)
        store.check_case(seen, path)
        store.check_file_size(path, int(size), lim)
        total += int(size)
        entries.append((oid, path, int(size)))
        store.check_tree_size(len(entries), total, lim)
    return entries, skipped


def extract_commit(core: Core, repo: Path, commit: str, dest: Path, lim: store.Limits, *,
                   empty: Path, protocol: str = "https") -> int:
    """Write the commit's files into ``dest`` straight from git's object store. No
    checkout happens. Returns how many paths were left out."""
    listing = _git(core, ["-C", str(repo), "ls-tree", "-r", "-l", "-z", "--full-tree", commit],
                   protocol=protocol, empty=empty, what="reading the app's files")
    entries, skipped = read_listing(listing, lim)
    dest.mkdir(parents=True)
    if not entries:
        return skipped
    wanted = "".join(f"{oid}\n" for oid, _, _ in entries).encode("ascii")
    blobs = _git(core, ["-C", str(repo), "cat-file", "--batch"], stdin=wanted,
                 protocol=protocol, empty=empty, what="reading the app's files")
    at = 0
    for oid, path, size in entries:
        end = blobs.find(b"\n", at)
        header = blobs[at:end].split() if end >= 0 else []
        if len(header) != 3 or header[0] != oid.encode() or header[1] != b"blob" \
                or int(header[2]) != size:
            raise AppError(502, (
                "Git did not hand over the app's files as listed, so nothing was "
                "installed. Try the install again."))
        store.write_file(dest, path, blobs[end + 1:end + 1 + size])
        at = end + 1 + size + 1
    return skipped


# --- plans ----------------------------------------------------------------------------------


def _plan_dir(core: Core, plan_id: str) -> Path:
    return store.staging_root(core) / check_plan_id(plan_id)


def sweep(core: Core) -> None:
    """Throw away fetched apps whose plan has run out, and anything half-made."""
    base = store.staging_root(core)
    if not base.is_dir():
        return
    now = ids.now_ms()
    for entry in base.iterdir():
        record = _read_record(entry)
        if record is None:
            # No plan yet: still being fetched, unless it has sat there for a day.
            try:
                old = now - entry.lstat().st_mtime * 1000 > 24 * 3600 * 1000
            except OSError:
                continue
            if old:
                store.delete_tree(entry)
        elif record.get("expires_at", 0) <= now:
            store.delete_tree(entry)


def _read_record(folder: Path) -> dict[str, Any] | None:
    try:
        record = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _manifest_refusal(error: mf.ManifestError) -> AppError:
    return AppError(422, str(error))


def tier_warning(tier: str) -> str | None:
    """The warning the dialog sets apart for a full app."""
    return perms.BY_ID[perms.RUNS_AS_YOU].sentence if tier == perms.FULL else None


EXAMPLE, FIRST_PARTY = "example", "first_party"


def bundled_bases() -> list[tuple[str, Path]]:
    """The folders inside this copy of JimmyHQ that ship apps, with their group: the
    examples, then the first-party apps."""
    out = []
    for group, base in ((EXAMPLE, store.examples_dir()), (FIRST_PARTY, store.first_party_dir())):
        if base is not None:
            out.append((group, base))
    return out


def _shipped_name(group: str, name: str) -> bool:
    """Whether a folder name in a shipping folder can be an app of that group. The
    examples folder inside the first-party one never is."""
    return bool(mf._ID.match(name)) and not (  # noqa: SLF001
        group == FIRST_PARTY and name == store.EXAMPLES_FOLDER)


def bundled_example(folder: Path | None) -> str | None:
    """The id of the shipped app whose folder this is, if ``folder`` is one of the
    example or first-party app folders inside this copy of JimmyHQ, or None. Decided by
    where the folder really is (links resolved), never by anything a manifest says."""
    if folder is None:
        return None
    try:
        here = Path(folder).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    for group, base in bundled_bases():
        try:
            root = base.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if here.parent == root and _shipped_name(group, here.name):
            return here.name
    return None


def bundled_folder(app_id: Any) -> Path | None:
    """The folder of the app ``app_id`` as it ships in this copy of JimmyHQ (an example
    first, then a first-party app), or None. The id is checked by the id rule before it
    is joined onto a path, and the folder's manifest must name that id."""
    if not isinstance(app_id, str) or not mf._ID.match(app_id):  # noqa: SLF001
        return None
    for group, base in bundled_bases():
        folder = base / app_id
        if not _shipped_name(group, app_id) or not (folder / mf.MANIFEST_FILE).is_file():
            continue
        try:
            if mf.load(folder).id == app_id:
                return folder
        except mf.ManifestError:
            continue
    return None


def bundled_tree(core: Core, example_id: str) -> str | None:
    """The tree hash of the shipped app ``example_id`` (an example or a first-party app)
    as it ships in this copy of JimmyHQ, or None when there is none or it cannot be read."""
    folder = bundled_folder(example_id)
    if folder is None:
        return None
    try:
        return store.hash_folder(folder, store.limits(core)).hash
    except (AppError, mf.ManifestError, OSError):
        return None


def provenance(core: Core, origin: Path | None, kind: str, tree: str, app_id: str) -> str:
    """Where a planned app comes from: ``bundled`` only when it was copied from an example
    folder inside JimmyHQ, under that folder's own id, and the copy is byte for byte what
    ships there. A folder or an address elsewhere is never bundled, whatever its manifest
    claims about its id, its name or its author."""
    if kind != FOLDER:
        return GIT if kind == "git" else kind
    example = bundled_example(origin)
    if example is None or example != app_id or bundled_tree(core, example) != tree:
        return FOLDER
    return BUNDLED


def origin_name(provenance_: str, source: dict[str, Any], folder: Path | None,
                app_id: str) -> str | None:
    """Rule ``an_app_is_the_same_by_where_it_came_from``: the place one install came from,
    by what JimmyHQ saw itself and never by what the manifest says: an app that ships with
    JimmyHQ by its id, once its files proved byte for byte that example (``provenance``);
    one from a git address by that address; one from a folder on this computer by the
    folder's full path. None when there is nothing to tell it by. Two installs are the same
    app when these are equal (``inbound.on_installed`` keeps a removed app's device keys
    only then). Its limits: anyone who can push to that address, or write that folder, can
    put another app there under the same id."""
    if provenance_ == BUNDLED:
        return f"example:{app_id}"
    if source.get("kind") == GIT and source.get("repo"):
        return f"git:{source['repo']}"
    if source.get("kind") == FOLDER and folder is not None:
        try:
            return f"folder:{Path(folder).resolve().as_posix()}"
        except (OSError, RuntimeError):
            return None
    return None


def origin_of_row(core: Core, row: dict[str, Any]) -> str | None:
    """``origin_name`` for an app installed before its origin was kept: from what the
    record says, which tells an example and a git address apart but not a folder."""
    if row.get("example") or (row.get("source") == FOLDER and row.get("tree")
                              and bundled_tree(core, row["app_id"]) == row["tree"]):
        return f"example:{row['app_id']}"
    if row.get("source") == GIT and row.get("repo"):
        return f"git:{row['repo']}"
    return None


def review_sentence(provenance_: str, reviewed: bool) -> str:
    if provenance_ == BUNDLED:
        return BUNDLED_REVIEW
    return REVIEWED if reviewed else NOT_REVIEWED


def _plan(core: Core, plan_id: str, folder: Path, source: dict[str, Any],
          skipped: int, origin: Path | None = None) -> dict[str, Any]:
    """Verify a staged tree and write down what the owner is about to be shown."""
    tree = store.hash_tree(folder / "tree", store.limits(core))
    try:
        m = mf.load(folder / "tree")
        mf.check_alongside(m, store.manifests(core))
    except mf.ManifestError as error:
        raise _manifest_refusal(error) from None
    check_records(m)
    check_id_is_free(core, m)
    check_full_allowed(core, m.tier, source["kind"])
    came_from = provenance(core, origin, source["kind"], tree.hash, m.id)
    now = ids.now_ms()
    record = {
        "plan": plan_id, "created_at": now,
        "expires_at": now + int(core.settings.get("apps.plan_minutes")) * 60_000,
        "app": {"id": m.id, "name": m.name, "version": m.version, "author": m.author,
                "summary": m.summary, "licence": m.licence, "tier": m.tier},
        "source": {**source, "tree": tree.hash, "files": tree.files, "bytes": tree.bytes,
                   "left_out": skipped},
        "reviewed": False, "provenance": came_from,
        "origin": origin_name(came_from, source, origin, m.id),
        "review": review_sentence(came_from, reviewed=False),
        "warning": tier_warning(m.tier),
        "needs_typed_name": m.tier == perms.FULL,
        "permissions": feeds.explain(core, [line.as_dict() for line in perms.describe(m)]),
        "adds": store.adds(m), "described": store.no_code(m),
        "fingerprint": perms.fingerprint(m),
        "feeds": feeds.plan_lines(core, m),  # who, already installed, will read its feeds
        "roles": roles_plan(m, folder / "tree"),  # roles: read before any agent is given one
        "storage": storage_ask.plan_line(core, m),
    }
    (folder / "plan.json").write_text(json.dumps(record, indent=1), encoding="utf-8",
                                      newline="\n")
    return record


def roles_plan(m: mf.Manifest, tree: Path) -> list[dict[str, Any]]:
    from jimmyhq.apps import roles  # noqa: PLC0415

    return roles.plan_roles(m, tree)


def planned_roles(core: Core, plan_id: Any) -> list[dict[str, Any]]:
    """The roles a waiting install would like filled, as its plan showed them."""
    record = _read_record(_plan_dir(core, check_plan_id(plan_id)))
    if record is None:
        raise AppError(404, "That install is no longer waiting. Start the install again.")
    return list(record.get("roles") or [])


def _stage(core: Core) -> tuple[str, Path, Path]:
    sweep(core)
    plan_id = ids.ulid()
    folder = _plan_dir(core, plan_id)
    empty = folder / "empty"
    empty.mkdir(parents=True)
    return plan_id, folder, empty


def plan_folder(core: Core, folder: str | Path) -> dict[str, Any]:
    """Copy an app from a folder on this computer into staging and return its plan. The
    copy is what would be installed: later changes to the folder change nothing."""
    if not isinstance(folder, str | Path) or not str(folder).strip():
        raise AppError(422, "Give the folder to install from. Nothing was installed.")
    source = Path(folder).expanduser()
    plan_id, staged, _ = _stage(core)
    try:
        tree = store.copy_folder(source, staged / "tree", store.limits(core))
        return _plan(core, plan_id, staged,
                     {"kind": "folder", "repo": None, "commit": None, "ref": None},
                     tree.left_out, origin=source)
    except BaseException:
        store.delete_tree(staged)
        raise


def check_records(m: mf.Manifest) -> None:
    """Refuse an app whose tables JimmyHQ could not keep records for (a computed field
    whose formula does not read), before the owner is asked anything."""
    problems = described.check(m.tables)
    if problems:
        raise AppError(422, (
            "You cannot install this app: " + " ".join(problems) + " Nothing was installed. "
            "The app's author needs to correct jimmyhq.app.json."))


def examples() -> list[dict[str, Any]]:
    """The apps that ship with JimmyHQ, by name: the examples and the first-party apps,
    each with its ``group``. One that does not read is left out: a shipped app is never a
    reason for the Apps screen to fail."""
    out = []
    seen: set[str] = set()
    for group, base in bundled_bases():
        found = []
        for folder in sorted(base.iterdir()):
            if (not _shipped_name(group, folder.name) or folder.name in seen
                    or not (folder / mf.MANIFEST_FILE).is_file()):
                continue
            try:
                m = mf.load(folder)
            except mf.ManifestError:
                continue
            if m.id == folder.name:
                seen.add(m.id)
                found.append({"id": m.id, "name": m.name, "summary": m.summary,
                              "tier": m.tier, "described": store.no_code(m), "group": group})
        out.extend(sorted(found, key=lambda e: e["name"].lower()))
    return out


def plan_example(core: Core, example_id: Any) -> dict[str, Any]:
    """Plan the install of one app that ships with JimmyHQ (an example or a first-party
    app), named by its id. The id is checked
    against the id rule and then looked up among the folders that are really there, so
    nothing a caller sends is ever joined onto a path."""
    known = {e["id"] for e in examples()}
    if not isinstance(example_id, str) or example_id not in known:
        listed = ", ".join(sorted(known)) or "none in this copy of JimmyHQ"
        raise AppError(404, (
            "JimmyHQ has no app of that name among the ones it ships. Nothing was "
            f"installed. They are: {listed}."))
    folder = bundled_folder(example_id)
    assert folder is not None  # `known` lists only folders that are really there
    plan = plan_folder(core, folder)
    return _mark_example(core, plan, example_id)


def _mark_example(core: Core, plan: dict[str, Any], example_id: str) -> dict[str, Any]:
    plan["source"]["example"] = example_id
    (_plan_dir(core, plan["plan"]) / "plan.json").write_text(
        json.dumps(plan, indent=1), encoding="utf-8", newline="\n")
    return plan


def plan_git(core: Core, url: str, *, commit: str | None = None,
             ref: str | None = None) -> dict[str, Any]:
    """Fetch one exact commit of a git repository into staging and return its plan. A
    branch or tag is looked up once and the commit it names is what gets pinned."""
    url = check_git_url(url)
    if commit:
        commit = check_commit(commit)
    elif ref:
        ref = check_ref(ref)
    else:
        raise check_commit_missing()
    plan_id, staged, empty = _stage(core)
    try:
        if not commit:
            commit = resolve_ref(core, url, ref or "", empty=empty)
        remote, protocol = _transport(url)
        repo = staged / "repo.git"
        fetch_commit(core, remote, commit, repo, empty=empty, protocol=protocol)
        skipped = extract_commit(core, repo, commit, staged / "tree", store.limits(core),
                                 empty=empty, protocol=protocol)
        store.delete_tree(repo)
        return _plan(core, plan_id, staged,
                     {"kind": "git", "repo": url, "commit": commit, "ref": ref}, skipped)
    except BaseException:
        store.delete_tree(staged)
        raise


def check_commit_missing() -> AppError:
    return AppError(422, (
        "You cannot install from a URL without saying which commit. Nothing was fetched or "
        "installed. Give the full 40-character commit hash, or a branch or tag name to "
        "look it up."))


def cancel(core: Core, plan_id: str) -> bool:
    """The owner said no: throw the fetched app away. True if there was one."""
    folder = _plan_dir(core, plan_id)
    there = folder.is_dir()
    store.delete_tree(folder)
    return there


def confirm(core: Core, plan_id: str, fingerprint: str, *, typed_name: str | None = None,
            actor: str = "owner") -> dict[str, Any]:
    """Install what a plan showed, if this is the consent for exactly that.

    ``fingerprint`` must be the plan's; the staged files must still hash to the tree the
    plan showed; the manifest read from them must still ask for what the fingerprint
    covers. Anything else is refused and nothing is installed."""
    folder = _plan_dir(core, plan_id)
    with store.service(core).lock:
        record = _read_record(folder)
        if record is None:
            raise AppError(404, (
                "That install is no longer waiting: it was cancelled, installed, or ran "
                "out of time. Nothing was installed. Start the install again."))
        if record.get("plan") != plan_id or record.get("expires_at", 0) <= ids.now_ms():
            store.delete_tree(folder)
            raise AppError(410, (
                "That install waited too long for an answer and was thrown away. Nothing "
                "was installed. Start the install again."))
        if not isinstance(fingerprint, str) or not hmac.compare_digest(
                fingerprint.encode("utf-8", "replace"), str(record["fingerprint"]).encode()):
            raise AppError(409, (
                "That is not the consent for this app. Nothing was installed. Start the "
                "install again and agree to what it shows."))
        changed = AppError(409, (
            "The app waiting to be installed is not what you were shown: its files "
            "changed after the plan was made. Nothing was installed, and it was thrown "
            "away. Start the install again."))
        try:
            tree = store.hash_tree(folder / "tree", store.limits(core))
            m = mf.load(folder / "tree")
        except (AppError, mf.ManifestError, OSError):
            store.delete_tree(folder)
            raise changed from None
        source = record["source"]
        if (tree.hash != source["tree"] or perms.fingerprint(m) != record["fingerprint"]
                or m.id != record["app"]["id"] or m.version != record["app"]["version"]):
            store.delete_tree(folder)
            raise changed
        check_typed_name(m, typed_name)
        check_full_allowed(core, m.tier, source["kind"])
        check_id_is_free(core, m)
        try:
            mf.check_alongside(m, store.manifests(core))
        except mf.ManifestError as error:
            raise _manifest_refusal(error) from None

        subject = kinds.subject(m.id)
        seq = core.tape.subject_last_seq(subject)
        dest = store.version_dir(core, m.id, m.version, tree.hash)
        store.move_tree(folder / "tree", dest)
        from jimmyhq.apps import environment  # noqa: PLC0415 - it imports runtime

        try:
            environment.build(core, m.id, m.name, store.version_name(m.version, tree.hash),
                              dest)
            core.tape.print("apps.installed", {
                "app_id": m.id, "name": m.name, "version": m.version, "tier": m.tier,
                "source": source["kind"], "repo": source["repo"], "commit": source["commit"],
                "tree": tree.hash, "permissions": list(m.permissions),
                "fingerprint": record["fingerprint"], "reviewed": False,
                "ref": source.get("ref"),
                "example": source.get("example") if record.get("provenance") == BUNDLED
                else None,
            }, actor=actor, subject=subject, expect_subject_seq=seq)
        except TapeConflict:
            store.delete_tree(dest)
            raise AppError(409, (
                "Something else changed this app at the same moment, so nothing was "
                "installed. Look at the Apps screen, then try again.")) from None
        except BaseException:
            store.delete_tree(dest)
            store.delete_tree(environment.venv_dir(
                core, m.id, store.version_name(m.version, tree.hash)))
            raise
        finally:
            store.delete_tree(folder)
        from jimmyhq.apps import inbound  # noqa: PLC0415 - it imports this package

        # Device keys kept when an app of this id was removed belong to that install: they
        # stay only if this is the same app again (``origin_name``), the door still shut.
        inbound.on_installed(core, m.id, record.get("origin"), actor=actor)
        storage_ask.grant(core, m, actor=actor)  # agreed to with the rest of the plan
        _publish(core)
        return detail(core, m.id)


# --- the rest of an app's life --------------------------------------------------------------


def enable(core: Core, app_id: str, *, actor: str = "owner") -> dict[str, Any]:
    """Switch an installed app on. Asking twice changes nothing."""
    with store.service(core).lock:
        if not store.need(core, app_id)["enabled"]:
            core.tape.print("apps.enabled", {"app_id": app_id}, actor=actor,
                            subject=kinds.subject(app_id))
        _publish(core)
        return detail(core, app_id)


def disable(core: Core, app_id: str, *, actor: str = "owner",
            reason: str = "owner") -> dict[str, Any]:
    """Switch an installed app off. Its code and data stay. ``reason`` is "crashing" when
    the owner chose to have an app that keeps crashing switched off."""
    with store.service(core).lock:
        if store.need(core, app_id)["enabled"]:
            core.tape.print("apps.disabled", {"app_id": app_id, "reason": reason},
                            actor=actor, subject=kinds.subject(app_id))
        _close(core, app_id)
        _publish(core)
        return detail(core, app_id)


def _publish(core: Core) -> None:
    """Tell the supervisor which full apps' programs should run now (``runtime``)."""
    from jimmyhq.apps import runtime  # noqa: PLC0415

    try:
        runtime.publish(core)
    except OSError as exc:
        log.warning("could not write which apps should run: %s", exc)
    from jimmyhq.apps import plugs  # noqa: PLC0415 - it imports this package

    plugs.setup(core)  # its settings on the Settings screen, its sources in Memory


def _close(core: Core, app_id: str) -> None:
    """Stop serving an app's files to frames already open, and let go of its database so
    the files can be moved or deleted (an open file cannot be, on Windows)."""
    from jimmyhq.apps import appdata, files  # noqa: PLC0415 - they import this package

    files.forget_mounts(core, app_id)
    appdata.stores(core).drop(app_id)
    core.auth.revoke_app_tokens(app_id)  # a running program's token stops working now


def check_data_deleted(core: Core, app_id: str, name: str) -> None:
    """Delete an app's data for good: its own data folder (the private store in it too),
    every backup taken when it changed version, and its program's log files. Refuses, and
    leaves the app installed, when any of it could not be deleted."""
    from jimmyhq.apps import runtime  # noqa: PLC0415

    home = store.app_dir(core, app_id)
    logs = runtime.log_file(core.config.log_dir, app_id)
    parts = [store.data_dir(core, app_id), home / "backups"]
    gone = all([store.delete_tree(part) for part in parts])
    for path in (logs, logs.with_name(logs.name + ".1")):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            gone = False
    if not gone:
        raise AppError(500, (
            f"JimmyHQ could not delete all the data of {name}, so it was not removed. "
            "Switch the app off, close anything that has its files open, then try again."))


def remove(core: Core, app_id: str, *, keep_data: bool | None = None,
           erase_record: bool | None = None, actor: str = "owner") -> dict[str, Any]:
    """Uninstall an app: its code, environments and secrets go, and its data stays unless
    the owner says otherwise (``apps.remove_keeps_data`` when ``keep_data`` is None).

    Deleting the data deletes its backups and logs too (``check_data_deleted``), and, unless
    ``erase_record`` is False, erases what it wrote on the Tape (``update.erase_record``):
    the events keep their place and lose what they said. With the data kept, its events
    stay readable unless ``erase_record`` is True."""
    with store.service(core).lock:
        row = store.need(core, app_id)
        if keep_data is None:
            keep_data = bool(core.settings.get("apps.remove_keeps_data"))
        if erase_record is None:
            erase_record = not keep_data
        home = store.app_dir(core, app_id)
        from jimmyhq.apps import runtime, update  # noqa: PLC0415 - they import this module

        paused = row.get("tier") == "full"
        removed = False
        # The program stays out of run.json from here to the end: listed again, the
        # supervisor would start it while its data is deleted. Only a removal that fails
        # lists it again; one that succeeds drops the pause quietly
        # (``runtime.forget_pause``), and the last ``_publish`` writes the list without it.
        asked = runtime.pause(core, app_id) if paused else 0
        try:
            if paused and not runtime.wait_stopped(core, app_id, asked):
                raise update.not_stopped(row["name"], "removed")
            _close(core, app_id)
            if not keep_data:
                check_data_deleted(core, app_id, row["name"])
            erased = update.erase_record(core, app_id) if erase_record else 0
            core.tape.print("apps.removed", {
                "app_id": app_id, "version": row["version"],
                "data": "kept" if keep_data else "deleted", "tape_erased": bool(erase_record),
            }, actor=actor, subject=kinds.subject(app_id))
            removed = True
        finally:
            if paused and not removed:
                runtime.resume(core, app_id)
            elif paused:
                runtime.forget_pause(app_id)
        for name in core.secrets.names():
            if name.startswith(f"app.{app_id}."):
                core.secrets.delete(name)
        from jimmyhq.apps import inbox  # noqa: PLC0415 - it imports this package

        inbox.forget(core, app_id)  # what was waiting for it, its heartbeat, its plugs
        from jimmyhq.apps.plugs.state import state as plug_state  # noqa: PLC0415

        plug_state(core).forget(app_id)  # its jobs' last runs and what it emitted
        update.forget(core, app_id)  # an update found for it
        from jimmyhq.apps import counts  # noqa: PLC0415

        counts.forget(core, app_id)  # the number it said waits on the owner
        from jimmyhq.apps import inbound  # noqa: PLC0415

        inbound.on_removed(core, app_id, keep_data=keep_data,  # its device keys
                           origin=origin_of_row(core, row))
        # --- roles: no capability, charter or open work of the app is left behind ---
        try:
            from jimmyhq.apps import roles  # noqa: PLC0415

            roles.on_removed(core, app_id, actor=actor)
        except Exception:  # noqa: BLE001 - the worker's tidy-up (roles.reconcile) finishes it
            log.exception("could not empty the roles of %s", app_id)
        _publish(core)
        for part in ("versions", "venvs"):
            store.delete_tree(home / part)
        if home.is_dir() and not any(home.iterdir()):
            store.delete_tree(home)
        return {"id": app_id, "name": row["name"], "version": row["version"],
                "data": "kept" if keep_data else "deleted", "erased_events": erased}


# --- reading --------------------------------------------------------------------------------


def _storage(folder: Path) -> int:
    total = 0
    for here, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.lstat(os.path.join(here, name)).st_size
            except OSError:
                continue
    return total


def _row(core: Core, row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row["app_id"], **{k: row.get(k) for k in (
            "name", "version", "tier", "source", "repo", "commit", "tree", "reviewed",
            "enabled", "disabled_reason", "installed_at", "updated_at", "consented_at",
            "fingerprint")},
        "permissions": list(row["permissions"]),
        "summary": "", "author": "", "icon": "", "licence": "", "described": False,
        "screens": [], "dock": [], "wall": [], "broken": None,
    }
    # The installed files are byte for byte an example that ships with this JimmyHQ, and
    # they were installed from a folder: what the Apps screen calls "Comes with JimmyHQ".
    came_from = (BUNDLED if row.get("source") == FOLDER and row.get("tree")
                 and bundled_tree(core, row["app_id"]) == row["tree"]
                 else row.get("source"))
    out["provenance"] = came_from
    out["review"] = review_sentence(came_from, bool(row.get("reviewed")))
    try:
        m = store.manifest_of(core, row["app_id"])
        out |= {"summary": m.summary, "author": m.author, "icon": m.icon, "licence": m.licence,
                "described": store.no_code(m), **store.places(m)}
    except AppError as error:
        out["broken"] = error.detail
    from jimmyhq.apps import dev  # noqa: PLC0415

    out["development"] = dev.is_development(core, row["app_id"])
    from jimmyhq.apps import update  # noqa: PLC0415 - it imports this module

    out["update"] = update.waiting(core, row["app_id"])
    out["update_available"] = update.available_label(out["update"])
    out["health"] = None
    if row.get("tier") == "full":
        from jimmyhq.apps import runtime  # noqa: PLC0415 - it imports this package

        state = runtime.health(core, row["app_id"])["state"]
        out["health"] = state
        out["running"] = state == "running"
    return out


def listing(core: Core) -> list[dict[str, Any]]:
    """Every installed app, for the owner's Apps screen, by name."""
    rows = sorted(store.installed(core).values(), key=lambda r: r["name"].lower())
    return [_row(core, row) for row in rows]


def detail(core: Core, app_id: str) -> dict[str, Any]:
    """One installed app: what it is, where it came from, and what was consented to, in
    the dialog's sentences, with when and by whom."""
    row = store.need(core, app_id)
    out = _row(core, row)
    lines = perms.describe_permissions(row["tier"], row["permissions"])
    out["owner_hosts"] = []
    out["net_notes"] = []
    if out["broken"] is None:
        from jimmyhq.apps import owner_hosts  # noqa: PLC0415 - it imports this module

        m = store.manifest_of(core, app_id)
        lines = perms.with_hints(lines, m)  # what it said it expects, in plain words
        from jimmyhq.apps import bridge  # noqa: PLC0415 - it imports this package

        out["net_notes"] = bridge.expect_notes(core, m)
        hosts_line = perms.owner_hosts_line(m)  # agreed to with the version in use
        if hosts_line is not None:
            lines = perms.ordered([*lines, hosts_line])
        out["owner_hosts"] = list(owner_hosts.granted(m, row))
    out["lines"] = feeds.explain(core, [line.as_dict() for line in lines])
    out["feeds"] = []  # who reads what: the feeds it offers, and those it reads
    if out["broken"] is None:
        out["feeds"] = feeds.who_reads_what(core, store.manifest_of(core, app_id),
                                            tuple(row["permissions"]))
    out["warning"] = tier_warning(row["tier"])
    out["consents"] = row["consents"]
    out["storage_bytes"] = _storage(store.data_dir(core, app_id))
    out["adds"] = None
    if out["broken"] is None:
        out["adds"] = store.adds(store.manifest_of(core, app_id))
    from jimmyhq.apps import environment, update  # noqa: PLC0415

    out["versions"] = update.kept(core, app_id)
    from jimmyhq.apps import local_reach  # noqa: PLC0415 - it imports this package

    # Named in the rollback dialog: a version of another tier does not keep them.
    out["local_addresses"] = list(local_reach.granted(row))
    out["auto_update"] = update.auto_allowed(core, app_id)
    out["environment_note"] = None
    if row["tier"] == perms.FULL and out["broken"] is None:
        name = store.version_name(row["version"], row["tree"])
        out["environment_note"] = environment.note(
            core, app_id, row["name"], name, store.version_dir(core, app_id, row["version"],
                                                               row["tree"]))
    return out
