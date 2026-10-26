import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
import json
from pathlib import Path
import socket

import aiohttp
import pytest

from mygrid_voltbridge import (
    LiveUpdate,
    P1Data,
    UpdateSource,
    VoltBridgeClient,
    VoltBridgeConnectionError,
    VoltBridgeNoDataError,
    async_stream,
)
from tests.fake_device import FakeDevice

FIXTURES = Path(__file__).parent / "fixtures"
DATAGRAM = bytes.fromhex((FIXTURES / "p1.hex").read_text().strip())


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert isinstance(port, int)
    return port


def client_for(device: FakeDevice, session: aiohttp.ClientSession) -> VoltBridgeClient:
    port = device.port
    assert isinstance(port, int)
    return VoltBridgeClient("127.0.0.1", session, port=port, retries=0, min_request_interval=0)


def open_stream(
    client: VoltBridgeClient, udp_port: int, initial: P1Data | None = None, **overrides: float
) -> AsyncGenerator[LiveUpdate]:
    settings: dict[str, float] = {
        "poll_interval": 60,
        "silence_timeout": 60,
        "fallback_interval": 0.05,
        "offline_after": 5,
    }
    return async_stream(
        client,
        initial=initial,
        **{**settings, **overrides},
        udp_port=udp_port,
        aggregated_port=None,
        bind="127.0.0.1",
    )


async def next_update(stream: AsyncGenerator[LiveUpdate]) -> LiveUpdate:
    async with asyncio.timeout(3):
        return await anext(stream)


def send(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(DATAGRAM, ("127.0.0.1", port))


async def test_first_update_is_a_full_snapshot(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    async with aclosing(open_stream(client_for(device, session), free_udp_port())) as stream:
        update = await next_update(stream)
    assert update.source is UpdateSource.P1DATA
    assert update.data.gas is not None
    assert update.snapshot_at == update.received_at


async def test_initial_snapshot_skips_the_first_read(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    client = client_for(device, session)
    snapshot = await client.async_get_p1_data()
    device.requests.clear()
    async with aclosing(open_stream(client, free_udp_port(), initial=snapshot)) as stream:
        update = await next_update(stream)
    assert update.data is snapshot
    assert update.source is UpdateSource.P1DATA
    assert update.snapshot_at == update.received_at
    assert device.requests == []


async def test_udp_updates_merge_without_http_reads(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    port = free_udp_port()
    async with aclosing(open_stream(client_for(device, session), port)) as stream:
        first = await next_update(stream)
        send(port)
        update = await next_update(stream)
    assert update.source is UpdateSource.UDP
    assert update.data.import_power == 267
    assert update.data.gas == first.data.gas
    assert update.snapshot_at == first.snapshot_at
    assert device.requests == ["/api/p1data"]


async def test_rssi_follows_the_broadcasts(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    port = free_udp_port()
    async with aclosing(open_stream(client_for(device, session), port, silence_timeout=0.05)) as stream:
        first = await next_update(stream)
        assert first.rssi is None
        send(port)
        update = await next_update(stream)
        assert update.source is UpdateSource.UDP
        assert update.rssi == -79
        while (later := await next_update(stream)).source is not UpdateSource.POWER:
            pass
    assert later.rssi == -79


async def test_silent_udp_falls_back_to_the_power_endpoint(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    async with aclosing(open_stream(client_for(device, session), free_udp_port(), silence_timeout=0.05)) as stream:
        first = await next_update(stream)
        update = await next_update(stream)
    assert update.source is UpdateSource.POWER
    assert update.data.net_power == 261
    assert update.data.gas == first.data.gas


@pytest.mark.parametrize("error", [OSError, ValueError])  # ValueError: no SO_REUSEPORT on the platform
async def test_udp_that_cannot_start_means_polling_only(
    device: FakeDevice, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    async def refuse(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("mygrid_voltbridge.stream.async_listen", refuse)
    async with aclosing(open_stream(client_for(device, session), free_udp_port())) as stream:
        await next_update(stream)
        assert (await next_update(stream)).source is UpdateSource.POWER


async def test_snapshot_is_refreshed_periodically(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    async with aclosing(open_stream(client_for(device, session), free_udp_port(), poll_interval=0.05)) as stream:
        first = await next_update(stream)
        update = await next_update(stream)
    assert update.source is UpdateSource.P1DATA
    assert update.snapshot_at > first.snapshot_at


async def test_stream_gives_up_when_the_device_goes_quiet(
    device: FakeDevice, session: aiohttp.ClientSession, routes: dict[str, tuple[int, str]]
) -> None:
    async with aclosing(
        open_stream(client_for(device, session), free_udp_port(), silence_timeout=0.05, offline_after=0.3)
    ) as stream:
        await next_update(stream)
        for path in routes:
            routes[path] = (500, "down")
        with pytest.raises(VoltBridgeConnectionError) as caught:
            await next_update(stream)
    assert isinstance(caught.value.__cause__, VoltBridgeConnectionError)  # the last failed read


async def test_stream_ends_with_no_data_error_when_the_device_has_no_p1_data(
    device: FakeDevice, session: aiohttp.ClientSession, routes: dict[str, tuple[int, str]]
) -> None:
    async with aclosing(
        open_stream(client_for(device, session), free_udp_port(), silence_timeout=0.05, offline_after=0.3)
    ) as stream:
        await next_update(stream)
        routes["/api/power"] = (200, json.dumps({"hasData": False}))
        with pytest.raises(VoltBridgeNoDataError):
            await next_update(stream)


async def test_failure_of_the_first_read_propagates(
    device: FakeDevice, session: aiohttp.ClientSession, routes: dict[str, tuple[int, str]]
) -> None:
    routes["/api/p1data"] = (500, "down")
    with pytest.raises(VoltBridgeConnectionError):
        await next_update(open_stream(client_for(device, session), free_udp_port()))


def test_non_positive_intervals_are_rejected() -> None:
    client = VoltBridgeClient("127.0.0.1")
    with pytest.raises(ValueError, match="poll_interval"):
        async_stream(client, poll_interval=0)
    with pytest.raises(ValueError, match="silence_timeout"):
        async_stream(client, silence_timeout=0)
    with pytest.raises(ValueError, match="fallback_interval"):
        async_stream(client, fallback_interval=-1)
    with pytest.raises(ValueError, match="offline_after"):
        async_stream(client, offline_after=0)
