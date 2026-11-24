"""Put the built website (site/dist) into a cPanel account, over cPanel's own API.

    uv run python scripts/deploy_site_cpanel.py check   --host example.org
    uv run python scripts/deploy_site_cpanel.py preview --host example.org --subdomain new
    uv run python scripts/deploy_site_cpanel.py upload  --host example.org --dir public_html

``check`` only reads: it asks the account where the domain's files live. ``preview`` makes
the subdomain if it is not there (its files go in a folder of their own, outside the main
site) and uploads the site into it. ``upload`` uploads into a folder you name.

    uv run python scripts/deploy_site_cpanel.py golive --host example.org --replace-live-site
    uv run python scripts/deploy_site_cpanel.py undo   --host example.org --from site_before_…

``golive`` replaces what the domain serves. It starts a full backup of the account, moves
everything now in the domain's folder into a dated folder outside the web (nothing is
deleted; ``.well-known``, ``cgi-bin`` and preview folders stay), then uploads the site.
``undo`` puts a kept folder's files back, moving the site's own aside the same way.

Who it signs in as is never on the command line and never printed. It comes from the
environment (``CPANEL_USER`` and ``CPANEL_TOKEN``) or, on macOS, from a Keychain entry you
make yourself (``--keychain NAME``, by default ``jimmyhq-site-cpanel``):

    security add-generic-password -U -s jimmyhq-site-cpanel -a YOUR_CPANEL_USERNAME -w

Make the token in cPanel under Manage API Tokens, for the account that holds this one site.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "site" / "dist"
KEYCHAIN = "jimmyhq-site-cpanel"
BATCH_FILES = 8
BATCH_BYTES = 6 * 1024 * 1024

# Served by Apache beside the pages: the 404 page, no folder listings, and how long a
# browser may keep each kind of file. The pages carry their own content security policy.
HTACCESS = """\
DirectoryIndex index.html
Options -Indexes
ErrorDocument 404 /404.html
# A folder above this one may hold another site's rewrite rules (a blog engine's, say).
# They must not reach these pages: a missing page here is this site's own 404.
<IfModule mod_rewrite.c>
  RewriteEngine Off
</IfModule>
<IfModule mod_headers.c>
  Header always set X-Content-Type-Options "nosniff"
  Header always set Referrer-Policy "no-referrer"
  Header always set X-Frame-Options "DENY"
  <FilesMatch "\\.(html|xml|txt)$">
    Header set Cache-Control "no-cache"
  </FilesMatch>
  <FilesMatch "\\.(css|js|png|svg|ico|webp|woff2)$">
    Header set Cache-Control "public, max-age=3600"
  </FilesMatch>
</IfModule>
"""


class Stop(Exception):
    """Something is not right. The message says what; it never holds the token."""


@dataclass(frozen=True)
class Login:
    user: str
    token: str

    def __repr__(self) -> str:  # a Login must never show its token, even by accident
        return "Login(…)"


def login_from(env: dict[str, str], keychain: str | None, run=subprocess.run) -> Login:
    """The cPanel user and token: from the environment, else from the macOS Keychain."""
    user, token = env.get("CPANEL_USER", ""), env.get("CPANEL_TOKEN", "")
    if user and token:
        return Login(user, token)
    if not keychain or sys.platform != "darwin":
        raise Stop("Set CPANEL_USER and CPANEL_TOKEN, or on macOS make the Keychain entry "
                   "this script's help describes. Nothing was sent.")
    found = run(["security", "find-generic-password", "-s", keychain],  # noqa: S603, S607
                capture_output=True, text=True, check=False)
    name = re.search(r'"acct"<blob>="([^"]+)"', found.stdout or "")
    secret = run(["security", "find-generic-password", "-s", keychain, "-w"],  # noqa: S603, S607
                 capture_output=True, text=True, check=False)
    if found.returncode != 0 or secret.returncode != 0 or not name or not secret.stdout.strip():
        raise Stop(f"There is no usable Keychain entry called {keychain!r}. Make it with the "
                   "command in this script's help. Nothing was sent.")
    return Login(name.group(1), secret.stdout.strip())


class CPanel:
    """The few cPanel calls this needs. Every answer is checked; a refusal raises Stop with
    cPanel's own words."""

    def __init__(self, host: str, login: Login, *, port: int = 2083,
                 transport: httpx.BaseTransport | None = None):
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise Stop("The host should be a plain name such as example.org.")
        self._client = httpx.Client(
            base_url=f"https://{host}:{port}/execute/", transport=transport, timeout=120,
            headers={"Authorization": f"cpanel {login.user}:{login.token}"})
        self._secret = login.token

    def close(self) -> None:
        self._client.close()

    def _answer(self, response: httpx.Response, what: str) -> Any:
        if response.status_code in (401, 403):
            raise Stop(f"cPanel refused the sign-in while trying to {what} "
                       f"(HTTP {response.status_code}). Check the user and the token.")
        try:
            body = response.json()
        except ValueError:
            raise Stop(f"cPanel's answer could not be read while trying to {what} "
                       f"(HTTP {response.status_code}).") from None
        if not isinstance(body, dict) or body.get("status") != 1:
            errors = body.get("errors") if isinstance(body, dict) else None
            words = "; ".join(str(e) for e in errors) if errors else "no reason given"
            raise Stop(f"cPanel would not {what}: {words}".replace(self._secret, "…"))
        return body.get("data")

    def call(self, function: str, what: str, **params: str) -> Any:
        try:
            return self._answer(self._client.get(function, params=params), what)
        except httpx.HTTPError as error:
            raise Stop(f"Could not reach cPanel to {what}: {type(error).__name__}.") from None

    def domain(self, name: str) -> dict[str, str]:
        """Where a domain's files live: its document root and the account's home folder."""
        data = self.call("DomainInfo/single_domain_data", f"look up {name}", domain=name) or {}
        return {"documentroot": str(data.get("documentroot", "")),
                "homedir": str(data.get("homedir", ""))}

    def exists(self, name: str) -> bool:
        try:
            return bool(self.domain(name)["documentroot"])
        except Stop:
            return False

    def add_subdomain(self, label: str, root_domain: str, folder: str) -> None:
        self.call("SubDomain/addsubdomain", f"make {label}.{root_domain}", domain=label,
                  rootdomain=root_domain, dir=folder)

    def entries(self, folder: str) -> list[str]:
        """The names in a folder (relative to home), hidden ones included."""
        data = self.call("Fileman/list_files", f"list {folder}", dir=folder, show_hidden="1")
        return sorted(str(e["file"]) for e in (data or []) if e.get("file") not in (".", ".."))

    def move(self, source: str, into: str) -> None:
        """Move a file or folder into a folder that exists. Both are full paths."""
        self.call("Fileman/move_file", "move a file aside", source=source, destination=into)

    def start_backup(self) -> None:
        self.call("Backup/fullbackup_to_homedir", "start a full backup", homedir="include")

    def upload(self, folder: str, files: list[tuple[str, bytes]]) -> None:
        """Upload files into one folder (relative to the home folder), replacing any there."""
        parts = {f"file-{n}": (name, data) for n, (name, data) in enumerate(files, start=1)}
        what = f"upload {len(files)} file(s) to {folder}"
        try:
            response = self._client.post("Fileman/upload_files",
                                         data={"dir": folder, "overwrite": "1"}, files=parts)
        except httpx.HTTPError as error:
            raise Stop(f"Could not reach cPanel to {what}: {type(error).__name__}.") from None
        data = self._answer(response, what) or {}
        bad = [u for u in data.get("uploads", []) if not u.get("status")]
        if bad or int(data.get("succeeded", len(files))) != len(files):
            names = ", ".join(f"{u.get('file')}: {u.get('reason')}" for u in bad) or "see cPanel"
            raise Stop(f"cPanel did not take every file for {folder}: {names}")


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.gmtime())


def site_files(dist: Path) -> dict[str, list[Path]]:
    """The built site's files, by the folder (relative, '' for the top) each belongs in."""
    if not (dist / "index.html").is_file():
        raise Stop(f"{dist} holds no built site. Build it first: "
                   "uv run python site/build.py --strict")
    by_folder: dict[str, list[Path]] = {}
    for path in sorted(p for p in dist.rglob("*") if p.is_file()):
        by_folder.setdefault(path.parent.relative_to(dist).as_posix().strip("."), []).append(path)
    return by_folder


def batches(paths: list[Path]) -> list[list[Path]]:
    """Files grouped so no one request is too many or too large."""
    out: list[list[Path]] = [[]]
    size = 0
    for path in paths:
        weight = path.stat().st_size
        if out[-1] and (len(out[-1]) >= BATCH_FILES or size + weight > BATCH_BYTES):
            out.append([])
            size = 0
        out[-1].append(path)
        size += weight
    return [b for b in out if b]


def upload_site(panel: CPanel, dist: Path, folder: str, say=print) -> int:
    """Every file of the built site into ``folder``, and the .htaccess beside them."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", folder) or ".." in folder:
        raise Stop("The folder should be a plain path inside the account, such as "
                   "public_html or site_preview.")
    count = 0
    for sub, paths in site_files(dist).items():
        target = f"{folder}/{sub}".rstrip("/")
        for group in batches(paths):
            panel.upload(target, [(p.name, p.read_bytes()) for p in group])
            count += len(group)
        say(f"  {target}: {len(paths)} file(s)")
    panel.upload(folder, [(".htaccess", HTACCESS.encode())])
    return count + 1


def clear_dropped(panel: CPanel, home: str, dist: Path, folder: str, stamp: str,
                  say=print) -> list[str]:
    """Move aside what an earlier build left in ``folder`` and this build no longer has.
    Only the site's own places are looked at: the pages at the top, and the folders the
    build makes (such as assets and shots). Nothing is deleted; the files go to a folder
    named site_dropped_… in the home folder."""
    holding, dropped = f"site_dropped_{stamp}", []
    by_folder = site_files(dist)
    for sub, paths in by_folder.items():
        here = f"{folder}/{sub}".rstrip("/")
        # the files the build has here, and the folders it has inside this one
        built = {p.name for p in paths} | {
            other[len(sub):].strip("/").split("/")[0]
            for other in by_folder if other != sub and (not sub or other.startswith(sub + "/"))}
        stale = [n for n in panel.entries(here)
                 if n not in built and (sub or n.endswith(".html"))]
        if not stale:
            continue
        into = f"{holding}/{sub}".rstrip("/")
        panel.upload(into, [("ABOUT.txt", f"Moved out of {here} by the site deploy: the "
                                           "newer site no longer has these.\n".encode())])
        for name in stale:
            panel.move(f"{home}/{here}/{name}", f"{home}/{into}")
            dropped.append(f"{here}/{name}")
    if dropped:
        say(f"  moved {len(dropped)} file(s) the site no longer has into {holding}")
    return dropped


STAYS = {".well-known", "cgi-bin"}


def move_aside(panel: CPanel, home: str, folder: str, holding: str, say=print) -> list[str]:
    """Everything in ``folder`` moved into ``holding`` (made if missing), except what must
    stay: certificate checks, cgi-bin and preview folders. Nothing is deleted."""
    for name in (folder, holding):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", name) or ".." in name:
            raise Stop("A folder should be a plain path inside the account.")
    panel.upload(holding, [("ABOUT.txt", f"Moved out of {folder} by the site deploy. "
                                          "Move these back to undo it.\n".encode())])
    moved = []
    for name in panel.entries(folder):
        if name in STAYS or name.startswith("site_"):
            continue
        panel.move(f"{home}/{folder}/{name}", f"{home}/{holding}")
        moved.append(name)
    say(f"  moved {len(moved)} item(s) from {folder} into {holding}")
    return moved


def go_live(panel: CPanel, host: str, dist: Path, stamp: str, *, backup: bool = True,
            say=print) -> str:
    """Replace what ``host`` serves with the built site. Returns the folder the old files
    were moved into."""
    site_files(dist)  # refuse before touching anything if there is no built site
    found = panel.domain(host)
    home = found["homedir"].rstrip("/")
    folder = found["documentroot"].removeprefix(home).strip("/")
    if not home or not folder:
        raise Stop(f"cPanel did not say where {host} is served from. Nothing was changed.")
    if backup:
        panel.start_backup()
        say("  a full backup of the account has been started (cPanel writes backup-….tar.gz "
            "in the home folder)")
    holding = f"site_before_{stamp}"
    move_aside(panel, home, folder, holding, say)
    upload_site(panel, dist, folder, say)
    return holding


def undo(panel: CPanel, host: str, kept: str, stamp: str, say=print) -> str:
    """Put a kept folder's files back where ``host`` serves from. The site's own files are
    moved aside the same way, and that folder's name is returned."""
    if not re.fullmatch(r"site_before_[0-9A-Za-z_-]+", kept):
        raise Stop("--from should be a folder this script made, named site_before_…")
    found = panel.domain(host)
    home = found["homedir"].rstrip("/")
    folder = found["documentroot"].removeprefix(home).strip("/")
    back = [n for n in panel.entries(kept) if n != "ABOUT.txt"]
    if not back:
        raise Stop(f"{kept} holds nothing to put back. Nothing was changed.")
    aside = f"site_after_{stamp}"
    move_aside(panel, home, folder, aside, say)
    for name in back:
        panel.move(f"{home}/{kept}/{name}", f"{home}/{folder}")
    say(f"  put {len(back)} item(s) back into {folder}")
    return aside


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=["check", "preview", "upload", "golive", "undo"])
    parser.add_argument("--replace-live-site", action="store_true",
                        help="for golive: say that you mean to replace the live site")
    parser.add_argument("--no-backup", action="store_true", help="for golive: skip the backup")
    parser.add_argument("--from", dest="kept", default=None, help="for undo: the kept folder")
    parser.add_argument("--host", required=True, help="the site's domain, e.g. example.org")
    parser.add_argument("--subdomain", default="new", help="for preview: the label to use")
    parser.add_argument("--dir", default=None, help="for upload: the folder inside the account")
    parser.add_argument("--dist", type=Path, default=DIST)
    parser.add_argument("--keychain", default=KEYCHAIN)
    args = parser.parse_args(argv)
    try:
        panel = CPanel(args.host, login_from(dict(os.environ), args.keychain))
        try:
            if args.action == "check":
                found = panel.domain(args.host)
                inside = found["documentroot"].removeprefix(found["homedir"]).lstrip("/")
                print(f"Signed in. {args.host} is served from the folder {inside!r} "
                      "inside the account. Nothing was changed.")
            elif args.action == "preview":
                if not re.fullmatch(r"[a-z0-9-]{1,30}", args.subdomain):
                    raise Stop("The subdomain should be a short plain label such as new.")
                name, asked = f"{args.subdomain}.{args.host}", f"site_{args.subdomain}"
                if panel.exists(name):
                    print(f"{name} is already there.")
                else:
                    panel.add_subdomain(args.subdomain, args.host, asked)
                    print(f"Made {name}.")
                # cPanel decides where a subdomain's files really live (often inside
                # public_html whatever was asked for), so ask it and upload there.
                found = panel.domain(name)
                folder = found["documentroot"].removeprefix(found["homedir"]).strip("/")
                print(f"It is served from the folder {folder!r}.")
                count = upload_site(panel, args.dist, folder)
                clear_dropped(panel, found["homedir"].rstrip("/"), args.dist, folder, stamp())
                # A preview is for looking at, not for search engines to list.
                panel.upload(folder, [("robots.txt", b"User-agent: *\nDisallow: /\n")])
                print(f"Uploaded {count} files. Look at https://{name}/ (its certificate "
                      "may take cPanel a little while to issue).")
            elif args.action == "golive":
                if not args.replace_live_site:
                    raise Stop(f"This replaces what {args.host} serves. Say so with "
                               "--replace-live-site. Nothing was changed.")
                kept = go_live(panel, args.host, args.dist, stamp(), backup=not args.no_backup)
                print(f"{args.host} now serves the new site. The old files are in {kept!r}. "
                      f"To go back: undo --host {args.host} --from {kept}")
            elif args.action == "undo":
                if not args.kept:
                    raise Stop("Say which kept folder with --from.")
                aside = undo(panel, args.host, args.kept, stamp())
                print(f"The files from {args.kept!r} are back. The site that was there is "
                      f"in {aside!r}.")
            else:
                if not args.dir:
                    raise Stop("Say which folder with --dir.")
                count = upload_site(panel, args.dist, args.dir)
                home = panel.domain(args.host)["homedir"].rstrip("/")
                clear_dropped(panel, home, args.dist, args.dir.strip("/"), stamp())
                print(f"Uploaded {count} files into {args.dir!r}.")
        finally:
            panel.close()
    except Stop as stop:
        print(stop, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
