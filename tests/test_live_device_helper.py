"""Offline tests for the live-test helper, so its classification is verified without a device."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from mygrid_voltbridge import (
    VoltBridgeConnectionError,
    VoltBridgeNoDataError,
    VoltBridgeResponseError,
    VoltBridgeUnsupportedError,
)
from tests.live.live_device import INCOMPATIBLE, NO_DATA, REJECTED, UNAVAILABLE, LiveDevice


@pytest.mark.parametrize(
    ("error", "label"),
    [
        (VoltBridgeUnsupportedError("404"), REJECTED),
        (VoltBridgeNoDataError("none yet"), NO_DATA),
        (VoltBridgeResponseError("not json"), INCOMPATIBLE),
        (VoltBridgeConnectionError("down"), UNAVAILABLE),
    ],
)
async def test_failures_are_classified(error: Exception, label: str) -> None:
    request = AsyncMock(side_effect=error)
    with pytest.raises(pytest.fail.Exception) as raised:
        await LiveDevice(MagicMock()).fetch("/api/x", request)
    assert label in str(raised.value)


async def test_success_passes_through() -> None:
    assert await LiveDevice(MagicMock()).fetch("/api/x", AsyncMock(return_value=42)) == 42
