"""Async client for the VoltBridge's local, unauthenticated HTTP API."""

import json
from typing import Any

import aiohttp

from .exceptions import VoltBridgeConnectionError, VoltBridgeResponseError
from .models import P1Data, PowerReading

DEFAULT_TIMEOUT = 5.0


class VoltBridgeClient:
    """Talks to one VoltBridge; the caller owns the ``aiohttp`` session.

    The device is a small microcontroller: poll ``/api/p1data`` every few
    seconds at most, and prefer ``async_get_power`` for fast updates.
    """

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        port: int = 80,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._base = f"http://{host}:{port}"
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def async_get_power(self) -> PowerReading:
        """Read instantaneous power (``/api/power``)."""
        return PowerReading.from_payload(await self._json("/api/power"))

    async def async_get_p1_data(self) -> P1Data:
        """Read the full P1 snapshot (``/api/p1data``)."""
        return P1Data.from_payload(await self._json("/api/p1data"))

    async def async_reboot(self) -> None:
        """Reboot the device (``POST /reboot``). Not retried."""
        await self._request("POST", "/reboot")

    async def _json(self, path: str) -> Any:
        body = await self._request("GET", path)
        try:
            return json.loads(body)
        except ValueError as err:
            raise VoltBridgeResponseError(f"{path} did not return JSON") from err

    async def _request(self, method: str, path: str) -> bytes:
        try:
            async with self._session.request(
                method, self._base + path, timeout=self._timeout
            ) as response:
                if response.status != 200:
                    raise VoltBridgeResponseError(
                        f"{path} returned HTTP {response.status}"
                    )
                return await response.read()
        except TimeoutError as err:
            raise VoltBridgeConnectionError(f"{self._base} timed out") from err
        except aiohttp.ClientError as err:
            raise VoltBridgeConnectionError(
                f"cannot reach {self._base}: {err}"
            ) from err
