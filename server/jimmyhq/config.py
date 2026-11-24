"""Bootstrap configuration: the few values needed before the Tape can be opened.

Everything else is a setting (see ``jimmyhq.settings``). These come from
environment variables, then ``config.toml`` in the data directory, then defaults.

The data directory lives outside the source tree on purpose, so a user's data
cannot be committed by accident.
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8440


def data_dir() -> Path:
    override = os.environ.get("JIMMYHQ_DATA")
    path = Path(override).expanduser() if override else Path(user_data_dir("JimmyHQ", False))
    if not path.exists():
        path.mkdir(parents=True)
        # Owner-only: the folder holds the password hash, sessions and service
        # keys, including in SQLite's side files, which are created with the
        # default mask. A folder that already existed is left as its owner set it.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)
    return path


@dataclass(frozen=True)
class Config:
    data: Path
    host: str
    port: int
    # Extra names this server may be reached by, beyond the local machine's own.
    allowed_hosts: tuple[str, ...] = ()
    # Whether the owner account may be created from another machine.
    remote_setup: bool = False
    # Addresses of reverse proxies whose forwarding headers may be believed.
    trusted_proxies: tuple[str, ...] = ()
    # Whether a request Tailscale marks as from the public internet (Funnel) is answered.
    allow_public: bool = False

    @property
    def tape_db(self) -> Path:
        return self.data / "tape.db"

    @property
    def views_db(self) -> Path:
        return self.data / "views.db"

    @property
    def auth_db(self) -> Path:
        return self.data / "auth.db"

    @property
    def metrics_db(self) -> Path:
        return self.data / "metrics.db"

    @property
    def vault_dir(self) -> Path:
        return self.data / "vault"

    @property
    def secrets_file(self) -> Path:
        return self.data / "secrets.json"

    @property
    def log_dir(self) -> Path:
        path = self.data / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path


class ConfigError(ValueError):
    """config.toml or an environment variable is not usable. The message says what to fix."""


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load() -> Config:
    data = data_dir()
    file_values: dict = {}
    config_file = data / "config.toml"
    if config_file.exists():
        try:
            # utf-8-sig: some Windows editors save a byte order mark.
            parsed = tomllib.loads(config_file.read_text(encoding="utf-8-sig"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{config_file} could not be read: {exc}") from exc
        file_values = parsed.get("server", {})
        if not isinstance(file_values, dict):
            raise ConfigError(f"{config_file}: [server] must be a table of settings.")
    host = os.environ.get("JIMMYHQ_HOST") or file_values.get("host") or DEFAULT_HOST
    raw_port = os.environ.get("JIMMYHQ_PORT") or file_values.get("port", DEFAULT_PORT)
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise ConfigError(f"The port must be a number, not {raw_port!r}.") from None
    # Not 0 ("any free port"): the address is told to the owner, to each app's program
    # and to the service before anything listens, so it has to be known in advance.
    if not 1 <= port <= 65535:
        raise ConfigError(
            f"The port must be between 1 and 65535, not {port}. Set JIMMYHQ_PORT, or port "
            "in config.toml, to a port that is free.")
    env_hosts = os.environ.get("JIMMYHQ_ALLOWED_HOSTS")
    hosts = env_hosts.split(",") if env_hosts else file_values.get("allowed_hosts", [])
    if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
        raise ConfigError(f"{config_file}: allowed_hosts must be a list of names.")
    remote = os.environ.get("JIMMYHQ_REMOTE_SETUP", file_values.get("remote_setup", False))
    env_proxies = os.environ.get("JIMMYHQ_TRUSTED_PROXIES")
    proxies = env_proxies.split(",") if env_proxies else file_values.get("trusted_proxies", [])
    if not isinstance(proxies, list) or not all(isinstance(p, str) for p in proxies):
        raise ConfigError(f"{config_file}: trusted_proxies must be a list of addresses.")
    public = os.environ.get("JIMMYHQ_ALLOW_PUBLIC", file_values.get("allow_public", False))
    return Config(
        data=data,
        host=str(host),
        port=port,
        allowed_hosts=tuple(h.strip().lower() for h in hosts if h.strip()),
        remote_setup=_truthy(remote),
        trusted_proxies=tuple(p.strip() for p in proxies if p.strip()),
        allow_public=_truthy(public),
    )
