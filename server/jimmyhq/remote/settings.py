"""Settings for reaching JimmyHQ from other devices. Importing this module declares them."""

from __future__ import annotations

from jimmyhq.settings import Setting, SettingError, declare

MAX_PATH = 500


def _command_path(value: str) -> str:
    value = value.strip()
    # A path copied from a file manager often arrives wrapped in quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if len(value) > MAX_PATH or any(ord(ch) < 32 for ch in value):
        raise SettingError("The path to the Tailscale command must be one line: the full path "
                           "of the tailscale program, or empty to look in the usual places.")
    return value


declare(Setting(
    key="remote.tailscale_path", section="access", type="str", default="",
    portable=False,
    label="Path to the Tailscale command",
    help="Leave empty and JimmyHQ looks for Tailscale in your PATH and the usual install "
         "folders. Fill it in only if Tailscale is installed somewhere else: the full path "
         "of the tailscale program.",
    validate=_command_path,
))
