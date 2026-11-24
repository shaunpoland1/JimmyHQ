"""Opens the stores every process shares."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

import jimmyhq.kinds  # noqa: F401 - registers the core event kinds
import jimmyhq.settings_core  # noqa: F401 - registers the core settings
from jimmyhq import __version__, config, modules
from jimmyhq.auth import Auth
from jimmyhq.lens import LensRunner
from jimmyhq.metrics import Metrics
from jimmyhq.secretstore import SecretStore
from jimmyhq.settings import Settings
from jimmyhq.tape import Tape, Vault

T = TypeVar("T")


@dataclass
class Core:
    config: config.Config
    tape: Tape
    vault: Vault
    lenses: LensRunner
    settings: Settings
    auth: Auth
    metrics: Metrics
    secrets: SecretStore
    _services: dict[str, Any] = field(default_factory=dict)
    _services_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def service(self, name: str, factory: Callable[[Core], T]) -> T:
        """A module's shared object for this process, created on first use."""
        with self._services_lock:  # two first requests must not build two
            if name not in self._services:
                self._services[name] = factory(self)
            return self._services[name]

    def close(self) -> None:
        with self._services_lock:
            services, self._services = list(self._services.values()), {}
        for service in services:  # a module's own stores, if it has any to close
            close = getattr(service, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
        for store in (self.lenses, self.auth, self.metrics, self.tape):
            store.close()


def open_core(cfg: config.Config | None = None) -> Core:
    cfg = cfg or config.load()
    modules.load()
    tape = Tape(cfg.tape_db)
    lenses = LensRunner(tape, cfg.views_db)
    core = Core(
        config=cfg,
        tape=tape,
        vault=Vault(cfg.vault_dir),
        lenses=lenses,
        settings=Settings(tape, lenses),
        auth=Auth(cfg.auth_db),
        metrics=Metrics(cfg.metrics_db),
        secrets=SecretStore(cfg.secrets_file),
    )
    if tape.last_seq() == 0:
        tape.print("system.opened", {"version": __version__}, actor="system")
    return core
