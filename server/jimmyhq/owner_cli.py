"""``jimmyhq owner``: what only someone at the computer itself can do to the owner account.

``set-password`` is the way back in after a forgotten password. It needs no old
password, because whoever can run it can already read and replace every file JimmyHQ
keeps. It opens the same stores a running server does, so it works whether or not
JimmyHQ is running: a running server looks each session up on every request and reads
the password hash on every sign-in, so it sees the change at once.

The new password is typed at a prompt that does not show it. It is never taken from an
argument (which other programs on the machine could read, and the shell would keep in
its history) and never from a pipe or a file.

``token list|create|revoke`` manages API tokens, as Settings, Access does: a token is
made with a label and a level (reader or writer), and the token itself is printed once.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Any, TextIO

from jimmyhq import tokens
from jimmyhq.auth import TOKEN_LEVELS, AuthError, check_password_rules

if TYPE_CHECKING:
    from jimmyhq.core import Core

ACTOR = "owner"
REFUSED = 1


def add_parser(sub: Any) -> None:
    """Register ``owner`` and its sub-commands on the main parser."""
    parser = sub.add_parser(
        "owner", help="set a new owner password, or manage API tokens, from this computer")
    parser.set_defaults(owner_parser=parser)
    commands = parser.add_subparsers(dest="owner_command", metavar="command")
    setter = commands.add_parser(
        "set-password",
        help="set a new password without the old one, and sign out every browser",
        description="Set a new owner password without the old one. You are asked for it "
                    "twice and it is not shown as you type. Every browser is signed out. "
                    "API tokens, door clients and joined machines keep working.")
    # Anything typed after the command is caught here, so it can be refused with a
    # sentence that does not repeat it (argparse's own refusal would print it back).
    setter.add_argument("typed_after", nargs="*", help=argparse.SUPPRESS)
    token = commands.add_parser(
        "token", help="list, create or revoke API tokens",
        description="API tokens let a script or a screen use JimmyHQ without the password. "
                    "A reader can only read; a writer can also make changes. Neither can "
                    "change settings or credentials.")
    token.set_defaults(token_parser=token)
    what = token.add_subparsers(dest="action", metavar="command")
    what.add_parser("list", help="every token, with its id, label, level and state")
    create = what.add_parser("create", help="make a token and print it once")
    create.add_argument("label", help="what the token is for, such as wall-screen")
    create.add_argument("--level", choices=sorted(TOKEN_LEVELS), default="reader",
                        help="reader (the default) or writer")
    revoke = what.add_parser("revoke", help="stop a token at once")
    revoke.add_argument("token_id", help="the id from `token list`")


def at_a_terminal() -> bool:
    """Whether a person is typing: without a terminal the prompt could not hide the text."""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def ask_twice() -> str:
    """The new password, typed twice without being shown. Raises ``AuthError`` with the
    sentence to print if it breaks the rules or the two do not match."""
    first = getpass.getpass("New password: ")
    check_password_rules(first)
    if getpass.getpass("Type it again: ") != first:
        raise AuthError("The two passwords do not match.")
    return first


def set_password(core: Core, out: TextIO, err: TextIO, typed_after: bool = False) -> int:
    if typed_after:
        print("Nothing was changed. The password is not taken from the command line. Run "
              "`uv run jimmyhq owner set-password` with nothing after it and type the password "
              "when asked. If you typed a password just now, your shell may have kept it "
              "in its history, so choose a different one.", file=err)
        return REFUSED
    if not at_a_terminal():
        print("Nothing was changed. Run this command in a terminal and type the new "
              "password when asked: it is not read from a pipe, a file or an argument.",
              file=err)
        return REFUSED
    try:
        ended = core.auth.set_password(ask_twice())
    except AuthError as exc:
        message = str(exc)
        if "Nothing was changed" not in message:
            message += " Nothing was changed. Run the command again."
        print(message, file=err)
        return REFUSED
    except (KeyboardInterrupt, EOFError):
        print("\nNothing was changed.", file=err)
        return REFUSED
    core.tape.print("owner.password_changed",
                    {"via": "command", "sessions_ended": ended}, actor=ACTOR)
    signed_out = ("No browser was signed in." if ended == 0 else
                  "1 browser was signed out." if ended == 1 else
                  f"{ended} browsers were signed out.")
    print(f"The password was changed. {signed_out} Sign in with the new password.", file=out)
    return 0


def iso(ms: int) -> str:
    """A stored time (unix milliseconds) as this computer's local date and time."""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")  # noqa: DTZ006


def token_main(args: argparse.Namespace, core: Core, out: TextIO, err: TextIO) -> int:
    """``jimmyhq owner token ...``: 0 done, 1 refused, 2 no command given."""
    if args.action == "list":
        found = tokens.listed(core)
        if not found:
            print("There are no API tokens. Make one with "
                  "`uv run jimmyhq owner token create <label>`.", file=out)
        for t in found:
            state = f"revoked {iso(t['revoked'])}" if t["revoked"] else "live"
            print(f"{t['id']}  {t['label']}  {t['level']}  made {iso(t['created'])}  {state}",
                  file=out)
        return 0
    if args.action == "create":
        try:
            made, secret = tokens.create(core, args.label, args.level, actor=ACTOR)
        except AuthError as exc:
            print(str(exc), file=err)
            return REFUSED
        print(f"Made the {made['level']} token \"{made['label']}\" ({made['id']}). This is "
              "the only time it is shown, so copy it now:", file=out)
        print(secret, file=out)
        return 0
    if args.action == "revoke":
        try:
            ended = tokens.revoke(core, args.token_id, actor=ACTOR)
        except LookupError as exc:
            print(str(exc), file=err)
            return REFUSED
        print(f"The token \"{ended['label']}\" is revoked. It no longer works.", file=out)
        return 0
    args.token_parser.print_help()
    return 2


def main_for(args: argparse.Namespace, core: Core) -> int:
    if args.owner_command == "set-password":
        return set_password(core, sys.stdout, sys.stderr, typed_after=bool(args.typed_after))
    if args.owner_command == "token":
        return token_main(args, core, sys.stdout, sys.stderr)
    args.owner_parser.print_help()
    return 2
