"""Async client for the VoltBridge's local, unauthenticated HTTP API."""

import asyncio
from http import HTTPStatus
import json
import logging
import time
from types import TracebackType
from typing import Any, Self

import aiohttp

from .exceptions import VoltBridgeConnectionError, VoltBridgeResponseError, VoltBridgeUnsupportedError
from .models import P1Data, PowerReading
from .telegram import Telegram, parse_telegram

_LOGGER = logging.getLogger(__name__)
HTTP_PORT = 80
DEFAULT_TIMEOUT = 5.0
DEFAULT_RETRIES = 2
# The device is a microcontroller with very little free memory: space requests out.
DEFAULT_MIN_INTERVAL = 0.1


class _ServerError(aiohttp.ClientError):
    """HTTP 5xx, retried like a network failure."""


class VoltBridgeClient:
    """Talks to one VoltBridge.

    Pass your own ``aiohttp`` session (Home Assistant's, for example) or let
    the client create one; it then allows one connection at a time, because
    the device is a small microcontroller with very little free memory, and
    closes it in ``close()`` or when leaving ``async with``. Requests are
    serialised and spaced at least ``min_request_interval`` seconds apart.
    Reads are retried with exponential backoff after a network error, timeout
    or HTTP 5xx; writes never are. Poll ``/api/p1data`` every few seconds at
    most and prefer ``async_get_power`` or UDP for fast updates.

    Raises ``ValueError`` for a negative ``retries`` or ``min_request_interval``
    and for a ``timeout`` that is not positive.
    """

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession | None = None,
        *,
        port: int = HTTP_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        min_request_interval: float = DEFAULT_MIN_INTERVAL,
    ) -> None:
        """Create a client; see the class docstring for the arguments."""
        if retries < 0:
            msg = "retries must be 0 or greater"
            raise ValueError(msg)
        if timeout <= 0:
            msg = "timeout must be greater than 0"
            raise ValueError(msg)
        if min_request_interval < 0:
            msg = "min_request_interval must be 0 or greater"
            raise ValueError(msg)
        self._base = f"http://{host}:{port}"
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._retries = retries
        self._min_interval = min_request_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0

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
        """Enter the async context; the client is ready to use."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Leave the async context and close an owned session."""
        await self.close()

    async def _json(self, path: str) -> Any:
        body = await self._request("GET", path)
        try:
            return json.loads(body)
        except ValueError as err:
            msg = f"{path} did not return JSON"
            raise VoltBridgeResponseError(msg) from err

    async def _request(self, method: str, path: str, *, retries: int | None = None) -> bytes:
        attempts = (self._retries if retries is None else retries) + 1
        for attempt in range(attempts):
            try:
                async with self._lock:
                    await self._space_out()
                    return await self._request_once(method, path)
            except VoltBridgeConnectionError:
                if attempt == attempts - 1:
                    raise
                delay = 0.25 * 2**attempt
                _LOGGER.debug("%s %s failed, retrying in %.2fs", method, path, delay)
                await asyncio.sleep(delay)
        msg = "unreachable"
        raise AssertionError(msg)  # pragma: no cover

    async def _space_out(self) -> None:
        """Wait until ``min_request_interval`` has passed since the last request."""
        wait = self._last_request + self._min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def _request_once(self, method: str, path: str) -> bytes:
        if self._session is None:
            self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit_per_host=1))
        try:
            async with self._session.request(method, self._base + path, timeout=self._timeout) as response:
                if response.status == HTTPStatus.NOT_FOUND:
                    msg = f"{path} is not available on this firmware"
                    raise VoltBridgeUnsupportedError(msg)
                if response.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    msg = f"HTTP {response.status}"
                    raise _ServerError(msg)
                if response.status != HTTPStatus.OK:
                    msg = f"{path} returned HTTP {response.status}"
                    raise VoltBridgeResponseError(
                        msg,
                        status=response.status,
                    )
                return await response.read()
        except TimeoutError as err:
            msg = f"{self._base} timed out"
            raise VoltBridgeConnectionError(msg) from err
        except aiohttp.ClientError as err:
            msg = f"cannot reach {self._base}{path}: {err}"
            raise VoltBridgeConnectionError(msg) from err
