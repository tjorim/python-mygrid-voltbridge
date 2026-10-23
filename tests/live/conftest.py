"""Fixtures for the live smoke tests, which talk to a real VoltBridge on the LAN.

Every test below tests/live is marked ``live`` and excluded from ordinary runs (see
``addopts`` in pyproject.toml). Run them with::

    VOLTBRIDGE_HOST=192.168.0.45 uv run pytest -m live

Without ``VOLTBRIDGE_HOST`` they are skipped.
"""

from collections.abc import AsyncIterator
import os

import pytest

from mygrid_voltbridge import VoltBridgeClient
from tests.live.live_device import LiveDevice


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test in this directory as live so ordinary runs deselect it."""
    for item in items:
        if "tests/live/" in item.nodeid:
            item.add_marker(pytest.mark.live)


@pytest.fixture(scope="session")
def host() -> str:
    value = os.environ.get("VOLTBRIDGE_HOST")
    if value is None or value == "":
        pytest.skip("set VOLTBRIDGE_HOST to run the live smoke tests")
    return value


@pytest.fixture
async def live(host: str) -> AsyncIterator[LiveDevice]:
    async with VoltBridgeClient(host) as client:
        yield LiveDevice(client)
