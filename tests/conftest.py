from __future__ import annotations

import pytest

from cairn.core.events import EventBus
from cairn.core.models import Config, cairnDeps, Provider


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def config() -> Config:
    return Config(provider=Provider.ANTHROPIC, model="test-model", api_key="test-key")


@pytest.fixture
def deps(config: Config) -> cairnDeps:
    return cairnDeps(config=config, cwd="/tmp/cairn-test", fs=None)

