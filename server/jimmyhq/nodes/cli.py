"""``jimmyhq node ...``: the commands run on the second machine."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from jimmyhq import config
from jimmyhq.nodes import agent


def add_parser(sub: argparse._SubParsersAction) -> None:
    node = sub.add_parser(
        "node", help="lend this machine's models to a JimmyHQ running on another machine")
    node.set_defaults(node_parser=node)
    actions = node.add_subparsers(dest="node_command")
    join = actions.add_parser("join", help="join a hub with a one-time code from its settings")
    join.add_argument("hub", help="the hub's address, such as http://192.0.2.10:8440")
    join.add_argument("code", help="the join code shown under Settings, Nodes on the hub")
    join.add_argument("--name", help="what the hub calls this machine (default: its own name)")
    join.add_argument("--allow-plain-http", action="store_true",
                      help="accept sending the code and token unprotected to a hub that is "
                           "not on a private network")
    actions.add_parser("run", help="stay connected to the hub and do the work it sends")
    actions.add_parser("leave", help="leave the hub and forget its token")
    actions.add_parser("status", help="show the hub joined and what this machine offers")
    offer = actions.add_parser("offer", help="offer something on this machine to the hub")
    kinds = offer.add_subparsers(dest="offer_kind", required=True)
    model = kinds.add_parser("model", help="offer a model server running on this machine")
    model.add_argument("--name", required=True, help="a short name, such as big")
    model.add_argument("--url", help="its OpenAI-compatible address, such as "
                                     "http://127.0.0.1:8080/v1")
    model.add_argument("--remove", action="store_true", help="stop offering it")


def main_for(args: argparse.Namespace, cfg: config.Config) -> int:
    store = agent.NodeStore(cfg.data)
    command = args.node_command
    if not command:
        args.node_parser.print_help()
        return 2
    try:
        if command == "join":
            print(asyncio.run(agent.join(store, args.hub, args.code, args.name,
                                         accept_plain=args.allow_plain_http)))
        elif command == "leave":
            print(asyncio.run(agent.leave(store)))
        elif command == "status":
            print(agent.status(store))
        elif command == "offer":
            if args.remove:
                print(agent.remove_model(store, args.name))
            elif not args.url:
                raise agent.NodeProblem("Nothing was changed: give the model server's address "
                                        "with --url, such as http://127.0.0.1:8080/v1.")
            else:
                print(agent.offer_model(store, args.name, args.url))
        else:
            return asyncio.run(_run(store))
    except agent.NodeProblem as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


async def _run(store: agent.NodeStore) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name),
                          lambda *_: loop.call_soon_threadsafe(stop.set))
    return await agent.NodeAgent(store).run(stop)
