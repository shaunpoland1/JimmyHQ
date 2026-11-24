"""Run JimmyHQ at login, on each operating system.

``render`` returns the service definition and where it goes. ``install``
writes it and returns the one command that switches it on, which the user
runs themselves. Nothing here needs administrator rights.

Any ``JIMMYHQ_*`` variables set when the service is installed (a custom data
folder, host or port) are written into the definition, because a service does
not inherit the shell's environment.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "ai.jimmyhq"
TASK = "JimmyHQ"
_SKIP_ENV = {"JIMMYHQ_SUPERVISED"}


@dataclass(frozen=True)
class ServiceFile:
    path: Path
    content: str
    enable: str  # the command that switches it on
    disable: str  # the command that switches it off
    encoding: str = "utf-8"
    extra_files: dict[Path, str] = field(default_factory=dict)  # written beside it, as UTF-8


def _windows_dir(home: Path) -> Path:
    return home / "AppData" / "Local" / "JimmyHQ"


def load_saved_env(home: Path | None = None) -> None:
    """Windows only: apply the JIMMYHQ_* values saved when the service was installed.

    A scheduled task has no environment of its own. Values already set win.
    """
    saved = _windows_dir(home or Path.home()) / "service-env.json"
    with contextlib.suppress(OSError, ValueError):
        for key, value in json.loads(saved.read_text(encoding="utf-8")).items():
            if key.startswith("JIMMYHQ_") and isinstance(value, str):
                os.environ.setdefault(key, value)


def _command(platform: str, node: bool = False) -> list[str]:
    python = Path(sys.executable)
    if platform == "win32":
        # pythonw has no console window, so nothing pops up at login.
        windowless = python.with_name("pythonw.exe")
        if windowless.exists():
            python = windowless
    return [str(python), "-m", "jimmyhq", *(("node", "run") if node else ("run",))]


def _own_env(env: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ) if env is None else env
    return {k: v for k, v in sorted(env.items()) if k.startswith("JIMMYHQ_") and k not in _SKIP_ENV}


def render(
    platform: str | None = None,
    home: Path | None = None,
    env: dict[str, str] | None = None,
    account: str | None = None,
    node: bool = False,
) -> ServiceFile:
    """``node`` is the service for a second machine: it runs ``jimmyhq node run``
    under a name of its own, so it never collides with a hub's service."""
    platform = platform or sys.platform
    home = home or Path.home()
    cmd = _command(platform, node)
    own = _own_env(env)
    label = f"{LABEL}.node" if node else LABEL
    unit = "jimmyhq-node" if node else "jimmyhq"
    task = f"{TASK}Node" if node else TASK
    title = "JimmyHQ node" if node else "JimmyHQ"

    if platform == "darwin":
        path = home / "Library" / "LaunchAgents" / f"{label}.plist"
        args = "\n".join(f"    <string>{escape(a)}</string>" for a in cmd)
        env_block = ""
        if own:
            pairs = "\n".join(
                f"    <key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in own.items()
            )
            env_block = f"  <key>EnvironmentVariables</key>\n  <dict>\n{pairs}\n  </dict>\n"
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
{env_block}  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
"""
        return ServiceFile(
            path, content,
            enable=f'launchctl bootstrap gui/$(id -u) "{path}"',
            disable=f"launchctl bootout gui/$(id -u)/{label}",
        )

    if platform.startswith("linux"):
        path = home / ".config" / "systemd" / "user" / f"{unit}.service"
        exec_start = " ".join(f'"{a}"' for a in cmd)
        env_lines = "".join(
            'Environment="{}={}"\n'.format(k, v.replace("\\", "\\\\").replace('"', '\\"'))
            for k, v in own.items()
        )
        content = f"""[Unit]
Description={title}
After=network.target

[Service]
ExecStart={exec_start}
{env_lines}Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""
        enable = f"systemctl --user daemon-reload && systemctl --user enable --now {unit}"
        return ServiceFile(path, content, enable, f"systemctl --user disable --now {unit}")

    if platform == "win32":
        # A task definition file avoids shell quoting, which differs between
        # the Command Prompt and PowerShell. The task is scoped to the person
        # installing it, which needs no administrator rights.
        folder = _windows_dir(home)
        path = folder / f"{unit}-task.xml"
        if account is None:
            domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
            account = f"{domain}\\{user}" if domain else user
        who = escape(account)
        content = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Starts {title} when you sign in.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{who}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{who}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>10</Count></RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(cmd[0])}</Command>
      <Arguments>{escape(" ".join(cmd[1:]))}</Arguments>
    </Exec>
  </Actions>
</Task>
"""
        return ServiceFile(
            path, content,
            enable=f'schtasks /Create /F /TN {task} /XML "{path}"',
            disable=f"schtasks /End /TN {task} & schtasks /Delete /F /TN {task}",
            encoding="utf-16",
            # The program is started directly, so its settings are saved in a
            # file it reads at start-up (see load_saved_env).
            extra_files={folder / "service-env.json": json.dumps(own, indent=2)},
        )

    raise RuntimeError(f"JimmyHQ does not know how to install a service on {platform!r}.")


def install(node: bool = False) -> ServiceFile:
    spec = render(node=node)
    spec.path.parent.mkdir(parents=True, exist_ok=True)
    spec.path.write_text(spec.content, encoding=spec.encoding)
    for extra, text in spec.extra_files.items():
        extra.write_text(text, encoding="utf-8")
    return spec
