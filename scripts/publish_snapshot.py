"""Copy a clean snapshot of this repository into another checkout, as one new commit.

For keeping development in one (private) repository and publishing from another: the
public one receives the tracked files of a commit, never the history, branches, issues or
pull requests of the one they were built in.

    uv run python scripts/publish_snapshot.py --to ../jimmyhq-public -m "0.1.1: what changed"

What it does, in order, and stops at the first thing that is not right:

1. The commit to publish (``--ref``, by default ``HEAD``) is exported with ``git archive``,
   so only tracked files go, exactly as committed. Untracked and ignored files cannot.
   Paths the commit's own ``.publicignore`` lists (one per line, ``#`` for comments, a folder
   holds everything under it, ``*`` matches any run of characters) are left out of the
   export. A line that matches nothing stops the run, so a renamed folder cannot quietly
   start going out. The README's generated parts are then written again for the apps that
   are left (`scripts/readme_tables.py --write` in the export).
2. The exported files are checked: the patterns of ``tests/test_public_safe.py`` (home
   paths, private addresses and hostnames, email addresses), every line of ``--terms``
   (a file of your own private words, one per line, kept outside the repository), and
   ``gitleaks`` when it is installed.
3. The target checkout must be a git repository with nothing uncommitted. Its tracked files
   are replaced by the exported ones and committed with your message.

It never pushes. It prints the command to push, which is yours to run.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".toml", ".yml", ".yaml", ".html",
        ".css", ".svg", ".txt", ".cfg", ".ini", ".sh", ".ps1", ""}
SKIP = {"package-lock.json", "uv.lock", "LICENSE"}
# The two files that hold the patterns themselves.
HOLDS_PATTERNS = {"tests/test_public_safe.py", "scripts/publish_snapshot.py"}

PATTERNS = {
    "a home directory path": re.compile(r"(/Users/|/home/|C:\\Users\\)[A-Za-z0-9._-]+"),
    "a private network address": re.compile(
        r"\b(10\.\d{1,3}|192\.168|172\.(1[6-9]|2\d|3[01])|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7]))"
        r"\.\d{1,3}\.\d{1,3}\b"
    ),
    "a private network hostname": re.compile(
        r"(?<![\w-])(?!example-)[a-z0-9-]+\.(ts\.net|local|lan)\b(?!\.)"
    ),
    "an email address": re.compile(r"\b[\w.+-]+@(?!anthropic\.com|example\.)[\w-]+\.[a-z]{2,}\b"),
}


class Stop(Exception):
    """Something is not right, so nothing was published. The message says what."""


def git(where: Path, *args: str, data: bytes | None = None) -> bytes:
    done = subprocess.run(["git", *args], cwd=where, input=data,  # noqa: S603, S607
                          capture_output=True, check=False)
    if done.returncode != 0:
        raise Stop(f"git {' '.join(args)} failed in {where}: "
                   f"{done.stderr.decode(errors='replace').strip()}")
    return done.stdout


IGNORE_FILE = ".publicignore"


def read_ignore(text: str) -> list[str]:
    """The paths a ``.publicignore`` lists: one per line, no blank lines or comments."""
    lines = (line.strip().replace("\\", "/") for line in text.splitlines())
    return [line.strip("/") for line in lines if line and not line.startswith("#")]


def held_back(path: str, patterns: list[str]) -> str | None:
    """The pattern that holds ``path`` (a file's path from the root, with ``/``) back, or None.
    A pattern holds back what it names and everything under it."""
    parts = path.split("/")
    for pattern in patterns:
        for end in range(1, len(parts) + 1):
            if fnmatch.fnmatchcase("/".join(parts[:end]), pattern):
                return pattern
    return None


def export(source: Path, ref: str, into: Path, *, keep_private: bool = False) -> str:
    """The tracked files of ``ref`` written under ``into``, less what its ``.publicignore``
    lists (unless ``keep_private``). Returns the commit's short id."""
    short = git(source, "rev-parse", "--short", ref).decode().strip()
    with tarfile.open(fileobj=io.BytesIO(git(source, "archive", "--format=tar", ref))) as tar:
        members = tar.getmembers()
        patterns: list[str] = []
        listing = next((m for m in members if m.name == IGNORE_FILE and m.isfile()), None)
        if listing is not None and not keep_private:
            patterns = read_ignore(tar.extractfile(listing).read().decode("utf-8"))
        files = [m.name.rstrip("/") for m in members if not m.isdir()]
        unused = [p for p in patterns if not any(held_back(name, [p]) for name in files)]
        if unused:
            raise Stop(f"{IGNORE_FILE} lists paths that are not in {ref}: {', '.join(unused)}. "
                       "Fix or remove those lines; nothing was published.")
        tar.extractall(into, members=[m for m in members if not held_back(m.name, patterns)],
                       filter="data")
    if patterns:
        regenerate(into)
    return short


def regenerate(tree: Path) -> None:
    """The parts of the exported files that are written from what is on disk (the README's
    table of apps and the links to their guides) are written again for what is left, so the
    copy does not list what was held back."""
    script = tree / "scripts" / "readme_tables.py"
    if not script.is_file():
        return
    done = subprocess.run([sys.executable, str(script), "--write"], cwd=tree,  # noqa: S603
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise Stop("The README's generated parts could not be written for the copy: "
                   f"{(done.stderr or done.stdout).strip()}. Nothing was published.")


def read_terms(path: Path | None) -> list[str]:
    if path is None:
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def mask(term: str) -> str:
    return term[:2] + "…"


def problems(tree: Path, terms: list[str]) -> list[str]:
    """Every place the exported files hold something that should not be published. A private
    term is reported masked, so this list is itself safe to show."""
    found: list[str] = []
    # A line "allow: some text" in the terms names text that may stay even though it holds
    # a term: the address of the public repository holds its owner's handle, for one.
    allowed = [t[6:].strip().lower() for t in terms if t.lower().startswith("allow:")]
    lowered = [(term, term.lower()) for term in terms if not term.lower().startswith("allow:")]

    def without_allowed(text: str) -> str:
        for ok in allowed:
            text = text.replace(ok, " " * len(ok))  # same length, so line numbers hold
        return text
    for path in sorted(p for p in tree.rglob("*") if p.is_file()):
        real = path.relative_to(tree).as_posix()
        name = real
        for term, low in lowered:  # a name that holds a term is itself shown masked
            name = re.sub(re.escape(low), mask(term), name, flags=re.IGNORECASE)
        for term, low in lowered:
            if low in real.lower():
                found.append(f"{name}: the file's name holds a private term ({mask(term)})")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = path.read_bytes().lower()
            for term, low in lowered:
                if low.encode() in raw:
                    found.append(f"{name}: holds a private term ({mask(term)})")
            continue
        low_text = without_allowed(text.lower())
        for term, low in lowered:
            at = low_text.find(low)
            if at >= 0:
                found.append(f"{name}:{text.count(chr(10), 0, at) + 1}: holds a private term "
                             f"({mask(term)})")
        if path.suffix not in TEXT or path.name in SKIP or real in HOLDS_PATTERNS:
            continue
        for what, pattern in PATTERNS.items():
            match = pattern.search(text)
            if match:
                found.append(f"{name}:{text.count(chr(10), 0, match.start()) + 1}: holds {what}")
    return found


def secrets(tree: Path) -> list[str]:
    """What gitleaks finds in the exported files, when gitleaks is installed."""
    tool = shutil.which("gitleaks")
    if tool is None:
        return []
    done = subprocess.run([tool, "detect", "--no-git", "--source", str(tree), "--redact",  # noqa: S603
                           "--no-banner"], capture_output=True, text=True, check=False)
    if done.returncode == 0:
        return []
    return ["gitleaks found what looks like a secret:\n" + (done.stdout + done.stderr).strip()]


def replace_tracked(target: Path, tree: Path) -> None:
    """The target's tracked files become exactly the exported ones."""
    if git(target, "status", "--porcelain").strip():
        raise Stop(f"{target} has changes that are not committed. Commit or discard them "
                   "first; nothing was published.")
    if git(target, "ls-files").strip():
        git(target, "rm", "-r", "-q", "--", ".")
    for path in sorted(p for p in tree.rglob("*") if p.is_file()):
        to = target / path.relative_to(tree)
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, to)
    git(target, "add", "-A")


def publish(source: Path, target: Path, message: str, *, ref: str = "HEAD",
            terms: list[str] | None = None, check_secrets: bool = True) -> str:
    """One new commit in ``target`` holding ``ref``'s files. Returns what was done, in words."""
    if not (target / ".git").exists():
        raise Stop(f"{target} is not a git checkout. Make the public repository's checkout "
                   "first (git init, or clone it); nothing was published.")
    if target.resolve() == source.resolve():
        raise Stop("The target is this repository itself; nothing was published.")
    with tempfile.TemporaryDirectory() as temp:
        tree = Path(temp)
        short = export(source, ref, tree)
        found = problems(tree, terms or []) + (secrets(tree) if check_secrets else [])
        if found:
            raise Stop("Nothing was published. Fix these first:\n  " + "\n  ".join(found))
        replace_tracked(target, tree)
    if not git(target, "status", "--porcelain").strip():
        return f"{target} already holds exactly these files, so there is nothing to commit."
    git(target, "commit", "-q", "-m", message)
    made = git(target, "rev-parse", "--short", "HEAD").decode().strip()
    return (f"Committed {made} in {target} from {short}. Nothing was pushed. To publish it:\n"
            f"  git -C {target} push")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--to", required=True, type=Path, help="the public repository's checkout")
    parser.add_argument("-m", "--message", required=True, help="the commit message")
    parser.add_argument("--ref", default="HEAD", help="the commit to publish (default HEAD)")
    parser.add_argument("--terms", type=Path, default=None,
                        help="a file of private words, one per line, that must not appear")
    args = parser.parse_args(argv)
    try:
        if args.terms is not None and not args.terms.is_file():
            raise Stop(f"{args.terms} is not a file; nothing was published.")
        print(publish(ROOT, args.to, args.message, ref=args.ref, terms=read_terms(args.terms)))
    except Stop as stop:
        print(stop, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
