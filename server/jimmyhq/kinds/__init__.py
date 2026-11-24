"""Core event kinds. Importing this package registers them.

Each core module keeps its kinds in its own file here. Apps declare theirs in
their own package under the ``app.<id>.`` prefix.
"""

from jimmyhq.kinds import system  # noqa: F401
