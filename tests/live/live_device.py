"""Failure-classifying wrapper for the live smoke tests (modelled on pyrail's ``LiveApi``).

A live failure means one of three different things, so each gets its own message:
the device cannot be reached, it rejects a request, or it answers with something
the library can no longer use (a firmware change).
"""

from collections.abc import Awaitable, Callable

import pytest

from mygrid_voltbridge import (
    VoltBridgeClient,
    VoltBridgeConnectionError,
    VoltBridgeError,
    VoltBridgeNoDataError,
    VoltBridgeResponseError,
    VoltBridgeUnsupportedError,
)

UNAVAILABLE = "DEVICE UNAVAILABLE"
REJECTED = "DEVICE REJECTED REQUEST"
INCOMPATIBLE = "DEVICE INCOMPATIBLE"
NO_DATA = "DEVICE HAS NO P1 DATA"


class LiveDevice:
    """A client whose failures fail the test with a classified message."""

    def __init__(self, client: VoltBridgeClient) -> None:
        self.client = client

    async def fetch[T](self, endpoint: str, request: Callable[[VoltBridgeClient], Awaitable[T]]) -> T:
        try:
            return await request(self.client)
        except VoltBridgeUnsupportedError as err:
            pytest.fail(f"{REJECTED}: '{endpoint}': {err}", pytrace=False)
        except VoltBridgeNoDataError as err:
            pytest.fail(f"{NO_DATA}: '{endpoint}': {err}", pytrace=False)
        except VoltBridgeResponseError as err:
            pytest.fail(f"{INCOMPATIBLE}: '{endpoint}': {str(err)[:300]}", pytrace=False)
        except VoltBridgeConnectionError as err:
            pytest.fail(f"{UNAVAILABLE}: '{endpoint}': {str(err)[:300]}", pytrace=False)
        except VoltBridgeError as err:  # pragma: no cover - future subclasses
            pytest.fail(
                f"{INCOMPATIBLE}: '{endpoint}': {type(err).__name__}: {err}",
                pytrace=False,
            )
