from __future__ import annotations

import pytest

from jimmyhq import config
from jimmyhq.core import open_core


@pytest.fixture
def cfg(tmp_path):
    return config.Config(data=tmp_path, host="127.0.0.1", port=0,
                         allowed_hosts=("testserver",), remote_setup=True)


@pytest.fixture
def core(cfg):
    core = open_core(cfg)
    yield core
    core.close()



@pytest.fixture(autouse=True)
def _apps_leave_the_registries_as_they_were():
    """An installed app declares its settings and memory sources in registries every
    test shares. What an app declared there is gone when the test ends; nothing else is
    touched, so what a module declares on first import stays."""
    yield
    from jimmyhq import settings
    from jimmyhq.memory import sources

    for key in [k for k in settings._DECLARED if k.startswith("app.")]:
        del settings._DECLARED[key]
    for section in [k for k in settings._SECTIONS if k.startswith("app.")]:
        del settings._SECTIONS[section]
    for source in [k for k in sources._SOURCES if k.startswith("app_")]:
        del sources._SOURCES[source]
    for key in [k for k in settings._DECLARED if k.startswith("memory.source.app_")]:
        del settings._DECLARED[key]  # an app source's switch
