"""Async client for the VoltBridge's local, unauthenticated HTTP API."""

import asyncio
import json
import logging
from types import TracebackType
from typing import Any, Self

import aiohttp

from .exceptions import (
    VoltBridgeConnectionError,
    VoltBridgeResponseError,
    VoltBridgeUnsupportedError,
)
from .models import P1Data, PowerReading
from .telegram import Telegram, parse_telegram

_LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 5.0
DEFAULT_RETRIES = 2


class VoltBridgeClient:
    """Talks to one VoltBridge.

    Pass your own ``aiohttp`` session (Home Assistant's, for example) or let
    the client create one; it then allows one connection at a time, because
    the device is a small microcontroller with very little free memory, and
    closes it in ``close()`` or when leaving ``async with``. Reads are retried
    with exponential backoff; writes never are. Poll ``/api/p1data`` every few
    seconds at most and prefer ``async_get_power`` or UDP for fast updates.
    """

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession | None = None,
        *,
        port: int = 80,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._base = f"http://{host}:{port}"
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._retries = retries

    async def async_get_power(self) -> PowerReading:
        """Read instantaneous power (``/api/power``)."""
        return PowerReading.from_payload(await self._json("/api/power"))

    async def async_get_p1_data(self) -> P1Data:
        """Read the full P1 snapshot (``/api/p1data``)."""
        return P1Data.from_payload(await self._json("/api/p1data"))

    async def async_get_telegram_text(self) -> str:
        """Read the raw DSMR telegram (``/api/telegram``), unmodified."""
        return (await self._request("GET", "/api/telegram")).decode("latin-1")

    async def async_get_telegram(self) -> Telegram:
        """Read and decode the telegram: M-Bus devices, breaker, peak history."""
        return parse_telegram(await self.async_get_telegram_text())

    async def async_reboot(self) -> None:
        """Reboot the device (``POST /reboot``). Not retried."""
        await self._request("POST", "/reboot", retries=0)

    async def close(self) -> None:
        """Close the session if this client created it."""
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def _json(self, path: str) -> Any:
        body = await self._request("GET", path)
        try:
            return json.loads(body)
        except ValueError as err:
            raise VoltBridgeResponseError(f"{path} did not return JSON") from err

    async def _request(
        self, method: str, path: str, *, retries: int | None = None
    ) -> bytes:
        attempts = (self._retries if retries is None else retries) + 1
        for attempt in range(attempts):
            try:
                return await self._request_once(method, path)
            except VoltBridgeConnectionError:
                if attempt == attempts - 1:
                    raise
                delay = 0.25 * 2**attempt
                _LOGGER.debug("%s %s failed, retrying in %.2fs", method, path, delay)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _request_once(self, method: str, path: str) -> bytes:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit_per_host=1)
            )
        try:
            async with self._session.request(
                method, self._base + path, timeout=self._timeout
            ) as response:
                if response.status == 404:
                    raise VoltBridgeUnsupportedError(
                        f"{path} is not available on this firmware"
                    )
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
