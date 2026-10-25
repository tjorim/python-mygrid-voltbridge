"""A live feed that prefers UDP push and falls back to HTTP when it goes quiet.

UDP broadcasts are the cheapest way to follow the meter, but they are lost on
weak Wi-Fi (about one in five on the test device) and cannot start at all
where broadcasts do not reach the host. ``async_stream`` hides that: it yields
a merged ``P1Data`` whenever any source delivers, reads ``/api/power`` while
the broadcasts are silent, and polls ``/api/p1data`` for what they lack (gas).
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import logging
from typing import Any

from .client import VoltBridgeClient
from .exceptions import VoltBridgeConnectionError, VoltBridgeError
from .models import P1Data
from .udp import AGGREGATED_PORT, P1_PORT, BroadcastStream, P1Broadcast, async_listen

_LOGGER = logging.getLogger(__name__)

# How often the silence of the UDP stream is checked, independent of how often the fallback reads.
_CHECK_INTERVAL = 0.25


class UpdateSource(StrEnum):
    """Where a ``LiveUpdate`` came from."""

    UDP = "udp"
    POWER = "power"
    P1DATA = "p1data"


@dataclass(frozen=True)
class LiveUpdate:
    """One merged state of the meter.

    ``data`` mixes sources: fields not delivered by ``source`` keep the value
    of the last update that had them. ``snapshot_at`` is when the full HTTP
    snapshot underneath was read, so ``received_at - snapshot_at`` bounds the
    age of fields only that snapshot carries, such as gas.
    """

    data: P1Data
    source: UpdateSource
    received_at: datetime
    snapshot_at: datetime


@dataclass(frozen=True)
class _Settings:
    poll_interval: float
    silence_timeout: float
    fallback_interval: float
    offline_after: float
    udp_port: int
    aggregated_port: int | None
    bind: str


def async_stream(
    client: VoltBridgeClient,
    *,
    poll_interval: float = 5.0,
    silence_timeout: float = 2.0,
    fallback_interval: float = 1.0,
    offline_after: float = 30.0,
    udp_port: int = P1_PORT,
    aggregated_port: int | None = AGGREGATED_PORT,
    bind: str = "0.0.0.0",  # noqa: S104 - broadcasts arrive on any interface
) -> AsyncIterator[LiveUpdate]:
    """Follow one VoltBridge, combining UDP push with HTTP reads.

    - The first update is a full ``/api/p1data`` read; its errors propagate.
    - Each broadcast from this meter is merged into the state and yielded.
    - If no live broadcast arrives for ``silence_timeout`` seconds (two missed
      datagrams at the usual 1 Hz), or UDP could not start, ``/api/power`` is
      read every ``fallback_interval`` seconds until broadcasts resume. Keep
      that at a second or more: the device has little memory.
    - ``/api/p1data`` is read every ``poll_interval`` seconds for gas and totals.
    - Failed reads are skipped. If nothing at all arrives for ``offline_after``
      seconds, ``VoltBridgeConnectionError`` is raised and the stream ends.

    Stop it with ``contextlib.aclosing(...)`` or ``await stream.aclose()`` so
    the sockets and tasks are released. Raises ``ValueError`` for an interval
    that is not positive.
    """
    for name, value in (
        ("poll_interval", poll_interval),
        ("silence_timeout", silence_timeout),
        ("fallback_interval", fallback_interval),
        ("offline_after", offline_after),
    ):
        if value <= 0:
            msg = f"{name} must be greater than 0"
            raise ValueError(msg)
    settings = _Settings(
        poll_interval,
        silence_timeout,
        fallback_interval,
        offline_after,
        udp_port,
        aggregated_port,
        bind,
    )
    return _Feed(client, settings).run()


class _Feed:
    """The state and background work behind one ``async_stream``."""

    def __init__(self, client: VoltBridgeClient, settings: _Settings) -> None:
        self._client = client
        self._settings = settings
        self._loop: asyncio.AbstractEventLoop
        self._queue: asyncio.Queue[tuple[UpdateSource | None, Any]] = asyncio.Queue()
        self._transports: list[asyncio.DatagramTransport] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._serial: str | None = None
        self._udp_active = False
        self._last_udp = 0.0

    async def run(self) -> AsyncIterator[LiveUpdate]:
        """Yield updates until the device has been silent for ``offline_after``."""
        self._loop = asyncio.get_running_loop()
        data = await self._client.async_get_p1_data()
        snapshot_at = datetime.now(UTC)
        self._serial = data.meter_serial
        self._last_udp = self._loop.time()
        try:
            # Listen before the first yield, so no broadcast is missed while the consumer handles it.
            await self._start()
            yield LiveUpdate(data, UpdateSource.P1DATA, snapshot_at, snapshot_at)
            last_ok = self._loop.time()  # the consumer's time spent on the update does not count
            while True:
                source, payload = await self._next(last_ok)
                merged = self._merge(data, source, payload)
                if merged is data:  # another meter's broadcast
                    continue
                if source is UpdateSource.P1DATA:
                    snapshot_at = datetime.now(UTC)
                data = merged
                yield LiveUpdate(data, source, datetime.now(UTC), snapshot_at)
                last_ok = self._loop.time()
        finally:
            await self._stop()

    async def _start(self) -> None:
        settings = self._settings
        try:
            await self._listen(settings.udp_port)
            self._udp_active = True
        except OSError as err:
            _LOGGER.warning("UDP push unavailable, falling back to polling: %s", err)
        if self._udp_active and settings.aggregated_port is not None:
            try:
                await self._listen(settings.aggregated_port)
            except OSError as err:
                _LOGGER.debug("Aggregated stream unavailable: %s", err)
        self._tasks = [asyncio.create_task(self._guarded(work)) for work in (self._poll, self._fallback)]

    async def _stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for transport in self._transports:
            transport.close()

    async def _listen(self, port: int) -> None:
        transport = await async_listen(self._on_broadcast, host=self._client.host, port=port, bind=self._settings.bind)
        self._transports.append(transport)

    def _on_broadcast(self, broadcast: P1Broadcast) -> None:
        if broadcast.stream is BroadcastStream.P1 and broadcast.data.meter_serial == self._serial:
            self._last_udp = self._loop.time()
        self._queue.put_nowait((UpdateSource.UDP, broadcast))

    async def _next(self, last_ok: float) -> tuple[UpdateSource, Any]:
        offline_after = self._settings.offline_after
        try:
            async with asyncio.timeout(max(offline_after - (self._loop.time() - last_ok), 0)):
                source, payload = await self._queue.get()
        except TimeoutError as err:
            msg = f"no data from {self._client.host} for {offline_after:g} s"
            raise VoltBridgeConnectionError(msg) from err
        if source is None:
            raise payload
        return source, payload

    @staticmethod
    def _merge(data: P1Data, source: UpdateSource, payload: Any) -> P1Data:
        if source is UpdateSource.P1DATA:
            return payload
        return payload.apply_to(data)  # a P1Broadcast or a PowerReading

    async def _guarded(self, work: Callable[[], Coroutine[Any, Any, None]]) -> None:
        try:
            await work()
        except Exception as err:  # noqa: BLE001 - surface bugs through the stream
            self._queue.put_nowait((None, err))

    async def _poll(self) -> None:
        while True:
            await asyncio.sleep(self._settings.poll_interval)
            try:
                self._queue.put_nowait((UpdateSource.P1DATA, await self._client.async_get_p1_data()))
            except VoltBridgeError as err:
                _LOGGER.debug("Snapshot poll failed: %s", err)

    async def _fallback(self) -> None:
        settings = self._settings
        tick = min(settings.fallback_interval, _CHECK_INTERVAL)
        last_read = float("-inf")
        while True:
            await asyncio.sleep(tick)
            now = self._loop.time()
            if self._udp_active and now - self._last_udp < settings.silence_timeout:
                continue
            if now - last_read < settings.fallback_interval:
                continue
            last_read = now
            try:
                self._queue.put_nowait((UpdateSource.POWER, await self._client.async_get_power()))
            except VoltBridgeError as err:
                _LOGGER.debug("Power fallback failed: %s", err)


__all__ = ["LiveUpdate", "UpdateSource", "async_stream"]
