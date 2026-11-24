"""Which browser program is used, and exactly how it is started.

JimmyHQ ships no browser and depends on no browser library. It drives a Chromium-family
browser that is already on the machine (Chrome, Chromium, Edge or Brave) over the
DevTools protocol, through a pipe that only the process which started it holds. A machine
with none of them simply has no browser capability: ``problem`` says so, and what to
install.

``flags`` is the whole of how the browser is started, in one tested place. What matters:

- its own profile folder, never the machine's (and no key ring, so none of the owner's
  saved passwords);
- no debugging port: the protocol runs over an inherited pipe, so no other program on
  the machine, and no page, can drive the browser;
- every connection through the fence's proxy, loopback included, and no name lookups of
  its own;
- no extensions, no sync, no background services that call home.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
         "microsoft-edge", "microsoft-edge-stable", "brave-browser", "chrome",
         "chrome-headless-shell")
MAC_APPS = (("Google Chrome", "Google Chrome"), ("Chromium", "Chromium"),
            ("Microsoft Edge", "Microsoft Edge"), ("Brave Browser", "Brave Browser"))
WINDOWS_PATHS = (("Google", "Chrome", "Application", "chrome.exe"),
                 ("Microsoft", "Edge", "Application", "msedge.exe"),
                 ("Chromium", "Application", "chrome.exe"),
                 ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"))

NOT_INSTALLED = (
    "No Chromium-family browser was found on this machine, so agents have no browser yet. "
    "Install Google Chrome, Chromium, Microsoft Edge or Brave ({how}), or put the full "
    "path of one under Settings, Browser. Nothing else needs installing.")
HOW = {"darwin": "for example: brew install --cask google-chrome",
       "win32": "Edge comes with Windows; or: winget install Google.Chrome",
       "linux": "for example: sudo apt install chromium, or sudo dnf install chromium"}
NOT_A_FILE = ("The browser program named under Settings, Browser is not a file on this "
              "machine: {path}. Correct the path, or leave it empty to use an installed "
              "browser.")


def candidates(platform: str | None = None, env: dict[str, str] | None = None) -> list[Path]:
    """Where a Chromium-family browser usually is on this kind of machine."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    out: list[Path] = []
    if platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            out += [root / f"{app}.app" / "Contents" / "MacOS" / binary
                    for app, binary in MAC_APPS]
    elif platform == "win32":
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if env.get(var):
                out += [Path(env[var], *parts) for parts in WINDOWS_PATHS]
    return out


def find(core: Any) -> Path | None:
    """The browser program to start, or None when there is none."""
    named = str(core.settings.get("browser.executable") or "").strip()
    if named:
        path = Path(named).expanduser()
        return path if path.is_file() else None
    for path in candidates():
        if path.is_file():
            return path
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def problem(core: Any) -> str | None:
    """What stops the browser from being used on this machine, for the owner."""
    if find(core) is not None:
        return None
    named = str(core.settings.get("browser.executable") or "").strip()
    if named:
        return NOT_A_FILE.format(path=named)
    how = HOW.get(sys.platform, HOW["linux"])
    return NOT_INSTALLED.format(how=how)


def flags(profile: Path, proxy_port: int, width: int, height: int) -> list[str]:
    """Every argument the browser is started with, after the program itself."""
    return [
        "--headless=new",
        f"--user-data-dir={profile}",
        "--remote-debugging-pipe",
        # The fence: one way out, for everything, loopback included, and no lookups.
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--no-pings",
        # Nothing of the owner's, and nothing that is not the page.
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-default-apps",
        "--disable-sync",
        "--allow-browser-signin=false",
        "--password-store=basic",
        "--use-mock-keychain",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-client-side-phishing-detection",
        "--disable-breakpad",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-service-autorun",
        "--deny-permission-prompts",
        "--disable-notifications",
        "--mute-audio",
        "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication,"
        "DialMediaRouteProvider,InterestFeedContentSuggestions",
        f"--window-size={int(width)},{int(height)}",
        "about:blank",
    ]
