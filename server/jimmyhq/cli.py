"""The ``jimmyhq`` command."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import re
import signal
import sys
import threading

from jimmyhq import __version__, config
from jimmyhq.storeversion import StoreTooNew, refuse_newer

# A device's key can travel in its address (apps/inbound.py: /api/in/<app>/<name>/k/<key>).
# Under /api/in/ only the app and the name are logged; and a key of ours ("jhqi_", see
# inbound.KEY_PREFIX) is hidden wherever it turns up in a logged message or argument.
_DEVICE_PATH = re.compile(r"(/api/in/[^/?]*/[^/?]*)/[^?]*")
# "j" may arrive written as %6A in an address that was not decoded.
_DEVICE_KEY = re.compile(r"(?:j|%6[aA])hqi_[^\s/?&\"']*")
# The loggers whose records are cleaned where they are made. The handlers are cleaned as
# well, so a record from any logger under these (jimmyhq.door, say) is cleaned too.
SCRUBBED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "jimmyhq")


def _hide_device_keys(text: str) -> str:
    return _DEVICE_KEY.sub("jhqi_...", _DEVICE_PATH.sub(r"\1/...", text))


class _NoQueryStrings(logging.Filter):
    """Keeps what was typed into a search, and a device's key, out of the logs.

    A query string can carry private text: a title search, a memory search.
    The path is enough to see what was asked for, so an access line is written without
    it. A device that can only be given an address carries its key in it, so that part
    of the path is never written by any logger: not in the message itself, and not in
    what is put into it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            # Only the key itself: a path pattern could swallow the message's own %s.
            msg = _DEVICE_KEY.sub("jhqi_...", record.msg)
            if msg != record.msg:
                record.msg = msg
        args = record.args
        if isinstance(args, tuple):
            args = tuple(_hide_device_keys(a) if isinstance(a, str) else a for a in args)
            if args != record.args:
                record.args = args
        elif isinstance(args, dict):
            record.args = {k: _hide_device_keys(v) if isinstance(v, str) else v
                           for k, v in args.items()}
        if record.name == "uvicorn.access" and isinstance(args, tuple) and len(args) >= 3 \
                and isinstance(args[2], str):
            path, mark, _ = args[2].partition("?")
            if mark:
                record.args = (*args[:2], path, *args[3:])
        return True


def scrub_logs(handlers: list[logging.Handler]) -> None:
    """Put the filter above on uvicorn's loggers, JimmyHQ's own and every handler."""
    scrub = _NoQueryStrings()
    for name in SCRUBBED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, _NoQueryStrings) for f in logger.filters):
            logger.addFilter(scrub)
    for handler in handlers:
        if not any(isinstance(f, _NoQueryStrings) for f in handler.filters):
            handler.addFilter(scrub)


def _logging(cfg: config.Config, name: str) -> None:
    handler = logging.handlers.RotatingFileHandler(
        cfg.log_dir / f"{name}.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [handler]
    # A supervised child's output goes to its .err.log, which is for crashes
    # only. Echoing every log line there would duplicate the rotating log.
    if os.environ.get("JIMMYHQ_SUPERVISED") != "1":
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    # These log every outbound request's address, which can carry what an
    # owner typed into it. Warnings and errors only.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    scrub_logs(handlers)


def _apps_parser(sub: argparse._SubParsersAction) -> None:
    """``jimmyhq apps install|remove|list|new|check|dev``. Works whether or not JimmyHQ is
    running; ``dev`` puts changes in place for a running one."""
    parser = sub.add_parser("apps", help="install, remove and list apps")
    parser.set_defaults(apps_parser=parser)
    commands = parser.add_subparsers(dest="apps_command", metavar="command")
    inst = commands.add_parser(
        "install", help="install an app from a folder, an https git address, or one that "
                        "ships with JimmyHQ by its id")
    inst.add_argument("source", help="a folder on this computer, an https git address, or "
                                     "the id of an app that ships with JimmyHQ")
    inst.add_argument("--commit", help="the full commit hash to install (git only)")
    inst.add_argument("--ref", help="a branch or tag to look up instead of a commit")
    inst.add_argument("--yes", action="store_true",
                      help="install without asking (a full app still needs --name)")
    inst.add_argument("--name", help="a full app's name, typed out to show you mean it")
    rem = commands.add_parser("remove", help="remove an installed app")
    rem.add_argument("id")
    rem.add_argument("--delete-data", action="store_true",
                     help="also delete the app's own data for good")
    rem.add_argument("--keep-data", action="store_true", help="keep the app's own data")
    commands.add_parser("list", help="show the installed apps")
    new = commands.add_parser("new", help="write a skeleton for a new app")
    new.add_argument("id", help="the new app's id, such as plant_care")
    new.add_argument("--tier", choices=("full", "sandboxed"), default="full")
    new.add_argument("--folder", help="where to write it (default: a new folder named after "
                                      "the id, here)")
    chk = commands.add_parser("check", help="run the checks an app must pass")
    chk.add_argument("folder")
    dev = commands.add_parser(
        "dev", help="install a folder for development and put each change in place")
    dev.add_argument("folder")
    dev.add_argument("--yes", action="store_true", help="install without asking (a full "
                                                        "app still needs --name)")
    dev.add_argument("--name", help="a full app's name, typed out to show you mean it")


def _ask(question: str) -> str | None:
    """The owner's answer to a question, or None when there is nobody to ask (the input
    is closed: a script, a pipe) or they pressed Ctrl-C."""
    try:
        return input(question)
    except (EOFError, KeyboardInterrupt):
        print()  # leave the unanswered question on a line of its own
        return None


def _names_a_shipped_app(source: str) -> bool:
    """Whether ``jimmyhq apps install <source>`` means an app that ships with JimmyHQ,
    named by its id. A folder of that name where the command is run comes first, as it
    always has."""
    from pathlib import Path

    from jimmyhq.apps import install

    return not Path(source).expanduser().exists() and install.bundled_folder(source) is not None


def _apps_main(args: argparse.Namespace, core: object) -> int:
    """Run one ``jimmyhq apps`` command as the owner. 0 done, 1 refused or declined."""
    from jimmyhq.apps import install
    from jimmyhq.apps.store import AppError

    try:
        if args.apps_command in ("new", "check", "dev"):
            return _apps_dev_main(args, core)
        if args.apps_command == "list":
            rows = install.listing(core)
            for row in rows:
                state = "on" if row["enabled"] else "off"
                print(f"{row['id']}\t{row['version']}\t{row['tier']}\t{state}\t{row['name']}")
            if not rows:
                print("No apps are installed. Install one from the Apps screen, or with "
                      "`jimmyhq apps install`.")
            return 0
        if args.apps_command == "remove":
            keep = False if args.delete_data else True if args.keep_data else None
            done = install.remove(core, args.id, keep_data=keep)
            print(f"Removed {done['name']} {done['version']}. Its data was {done['data']}.")
            return 0
        if "://" in args.source or "::" in args.source:
            plan = install.plan_git(core, args.source, commit=args.commit, ref=args.ref)
        elif _names_a_shipped_app(args.source):
            plan = install.plan_example(core, args.source)  # as the Apps screen does
        else:
            plan = install.plan_folder(core, args.source)
        app, source = plan["app"], plan["source"]
        print(f"{app['name']} {app['version']} ({app['tier']})"
              + (f" by {app['author']}" if app["author"] else ""))
        print(app["summary"])
        where = source["repo"] or ("the apps that ship with JimmyHQ" if source.get("example")
                                   else "a folder on this computer")
        print(f"From {where}" + (f" at commit {source['commit']}" if source["commit"] else ""))
        print(f"Files: {source['files']}, tree {source['tree']}")
        print(plan["review"])
        print("It will be able to:")
        for line in plan["permissions"]:
            print(f"  {'!! ' if line['strong'] else '- '}{line['sentence']}")
        typed = args.name
        if plan["needs_typed_name"]:
            if typed is None:
                typed = _ask(f"Type the app's name ({app['name']}) to install it: ")
                if typed is None:
                    install.cancel(core, plan["plan"])
                    print("Nothing was installed. Run this in a terminal and type the name "
                          f"when asked, or add --name \"{app['name']}\".", file=sys.stderr)
                    return 1
        elif not args.yes:
            answer = _ask("Install it? Type yes: ")
            if answer is None:
                install.cancel(core, plan["plan"])
                print("Nothing was installed. Run this in a terminal and answer yes when "
                      "asked, or add --yes.", file=sys.stderr)
                return 1
            if answer.strip().lower() != "yes":
                install.cancel(core, plan["plan"])
                print("Nothing was installed.")
                return 1
        try:
            done = install.confirm(core, plan["plan"], plan["fingerprint"], typed_name=typed)
        except AppError:
            install.cancel(core, plan["plan"])
            raise
        print(f"Installed {done['name']} {done['version']}.")
        return 0
    except AppError as error:
        print(error.detail, file=sys.stderr)
        return 1


def _apps_dev_main(args: argparse.Namespace, core: object) -> int:
    """``jimmyhq apps new|check|dev``: writing an app (``jimmyhq.apps.dev``)."""
    import signal
    import threading

    from jimmyhq.apps import dev

    if args.apps_command == "new":
        try:
            where = dev.new(args.id, args.folder or args.id, tier=args.tier)
        except (ValueError, FileExistsError) as error:
            print(error, file=sys.stderr)
            return 1
        print(f"Wrote a new {args.tier} app in {where}. Try it with: jimmyhq apps dev {where}")
        return 0
    if args.apps_command == "check":
        findings = dev.check(core, args.folder)
        for finding in findings:
            print(finding.line())
        if dev.passes(findings):
            print("It passes the checks." + (" Look at the warnings." if findings else ""))
            return 0
        print("It does not pass. Nothing was changed.")
        return 1

    def agree(plan: dict) -> str | None:
        app = plan["app"]
        print(f"{app['name']} {app['version']} ({app['tier']})")
        print(plan["review"])
        for line in plan["permissions"]:
            print(f"  {'!! ' if line['strong'] else '- '}{line['sentence']}")
        if plan["needs_typed_name"]:
            return args.name if args.name is not None else input(
                f"Type the app's name ({app['name']}) to install it: ")
        if args.yes or input("Install it for development? Type yes: ").strip().lower() == "yes":
            return ""
        return None

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    return dev.watch(core, args.folder, agree=agree, stop=stop)


def start_lines(address: str, data: str, *, built: bool) -> list[str]:
    """What ``jimmyhq run`` prints before anything is started. It does not say JimmyHQ is
    up: the supervisor says that when the web process answers."""
    from jimmyhq.web.dist import NOT_BUILT

    lines = [f"JimmyHQ {__version__} is starting. It will be at {address} once it is ready.",
             f"Data: {data}"]
    if not built:
        lines.append(NOT_BUILT)
    return lines


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except StoreTooNew as exc:  # data from a newer JimmyHQ: a sentence, not a traceback
        print(f"JimmyHQ cannot start: {exc}", file=sys.stderr)
        return 1


def _main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(prog="jimmyhq", description="Run and manage JimmyHQ.")
    parser.add_argument("--version", action="version", version=f"jimmyhq {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser(
        "run", help="start JimmyHQ (web, worker and village, restarted if they stop)")
    sub.add_parser("web", help="run only the web process")
    sub.add_parser("worker", help="run only the worker process")
    sub.add_parser("village", help="run only the village process (the agents)")
    sub.add_parser("data-dir", help="print where JimmyHQ keeps its data")
    from jimmyhq import settings_cli

    settings_cli.add_parser(sub)
    _apps_parser(sub)
    from jimmyhq import owner_cli

    owner_cli.add_parser(sub)
    from jimmyhq.nodes import cli as nodes_cli

    nodes_cli.add_parser(sub)
    from jimmyhq.backup import cli as backup_cli

    backup_cli.add_parser(sub)
    service = sub.add_parser("service", help="start JimmyHQ automatically at login")
    service.add_argument("action", choices=("install", "uninstall", "print"))
    service.add_argument("--node", action="store_true",
                         help="the service for a second machine: runs `jimmyhq node run`")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        from jimmyhq import service as _service

        _service.load_saved_env()
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"JimmyHQ cannot start: {exc}", file=sys.stderr)
        return 2
    command = args.command or "run"

    if command == "data-dir":
        print(cfg.data)
        return 0

    if command == "service":
        from jimmyhq import service as svc

        if args.action == "print":
            print(svc.render(node=args.node).content)
            return 0
        if args.action == "install":
            spec = svc.install(node=args.node)
            print(f"Wrote {spec.path}")
            print(f"To switch it on, run:\n  {spec.enable}")
            return 0
        spec = svc.render(node=args.node)
        # The definition is left in place: the command below needs it, and
        # deleting it first would leave the service running with no way to stop it.
        print(f"To switch it off, run:\n  {spec.disable}")
        print(f"Then delete {spec.path.parent if spec.extra_files else spec.path}")
        return 0

    if command == "settings":
        from jimmyhq.core import open_core

        if not args.settings_command:
            args.settings_parser.print_help()
            return 2
        core = open_core(cfg)
        try:
            from jimmyhq.apps import sync as apps_sync

            apps_sync.sync(core)  # the installed apps' own settings, as the screen has them
            return settings_cli.main_for(args, core)
        finally:
            core.close()

    if command == "owner":
        from jimmyhq.core import open_core

        if not args.owner_command:
            args.owner_parser.print_help()
            return 2
        core = open_core(cfg)
        try:
            return owner_cli.main_for(args, core)
        finally:
            core.close()

    if command == "apps":
        from jimmyhq.core import open_core

        if not args.apps_command:
            args.apps_parser.print_help()
            return 2
        core = open_core(cfg)
        try:
            return _apps_main(args, core)
        finally:
            core.close()

    if command == "backup":
        if not args.backup_command:
            args.backup_parser.print_help()
            return 2
        return backup_cli.main_for(args, cfg)

    if command == "node":
        if args.node_command == "run":
            _logging(cfg, "node")
        return nodes_cli.main_for(args, cfg)

    _logging(cfg, "supervisor" if command == "run" else command)

    if command == "web":
        import uvicorn

        from jimmyhq.supervisor import watch_parent
        from jimmyhq.web.app import create_app

        watch_parent()
        app = create_app()
        server = uvicorn.Server(uvicorn.Config(
            app, host=cfg.host, port=cfg.port, log_config=None,
            # A browser tab holds the live feed open; do not let that hold up a stop.
            timeout_graceful_shutdown=5,
            # Forwarding headers decide which client a wrong password is counted
            # against, so they are believed only from proxies the owner has named.
            proxy_headers=bool(cfg.trusted_proxies),
            forwarded_allow_ips=list(cfg.trusted_proxies) or None,
        ))
        app.state.server = server
        # The MCP door has a port of its own, which opens only while the owner has
        # switched the door on. It answers /mcp and nothing else.
        from jimmyhq import door

        listener = door.start_listener(app.state.core)
        try:
            server.run()
        finally:
            listener.stop()
        return 0

    if command == "worker":
        from jimmyhq import worker
        from jimmyhq.supervisor import watch_parent

        stop = threading.Event()
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: stop.set())
        watch_parent(stop.set)
        worker.run(stop)
        return 0

    if command == "village":
        from jimmyhq.supervisor import watch_parent
        from jimmyhq.village import process

        stop = threading.Event()
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: stop.set())
        watch_parent(stop.set)
        process.run(stop)
        return 0

    from jimmyhq import supervisor
    from jimmyhq.core import open_core

    try:
        lock = supervisor.take_lock(cfg.data)
    except supervisor.AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        # Asked once, before anything is started: a web process that cannot have its port
        # would only be restarted again and again.
        taken = supervisor.port_in_use(cfg.host, cfg.port)
        if taken is not None:
            print(taken, file=sys.stderr)
            return 1
        # Every store is looked at before anything starts, so one written by a newer
        # JimmyHQ stops this here with a sentence, not a child restarted again and again.
        refuse_newer(cfg.data)
        # Create the databases once here, so the children never race to do it.
        open_core(cfg).close()
        from jimmyhq.web.dist import web_dist

        address = f"http://{cfg.host}:{cfg.port}"
        for line in start_lines(address, str(cfg.data), built=web_dist() is not None):
            print(line, flush=True)
        from jimmyhq.apps.runtime import api_url

        return supervisor.run(cfg.log_dir, cfg.data, api_url(cfg.host, cfg.port),
                              ready_line=f"JimmyHQ is ready at {address}")
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
