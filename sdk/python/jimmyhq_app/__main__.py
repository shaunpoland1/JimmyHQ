"""``python -m jimmyhq_app <module:attribute>``: how JimmyHQ starts a full app's program.

The module is imported from the app's version folder (the working directory). If the
attribute is an ``App``, its ``run()`` is called; an app that already called ``app.run()``
at the end of its module, as the examples do, has finished by then and nothing more
happens. Anything else callable is called.
"""

from __future__ import annotations

import importlib
import re
import sys

_ENTRY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*\Z")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not _ENTRY.match(args[0]):
        print("Start an app as: python -m jimmyhq_app module:attribute", file=sys.stderr)
        return 2
    module_name, _, attribute = args[0].partition(":")
    module = importlib.import_module(module_name)
    target = getattr(module, attribute, None)
    if target is None:
        print(f"{module_name} has no {attribute!r}: check \"server.entry\" in "
              "jimmyhq.app.json.", file=sys.stderr)
        return 2
    run = getattr(target, "run", None)
    if callable(run):
        run()
    elif callable(target):
        target()
    return 0


if __name__ == "__main__":
    sys.exit(main())
