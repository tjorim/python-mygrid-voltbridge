import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
import socket

import aiohttp
import pytest

from mygrid_voltbridge import LiveUpdate, UpdateSource, VoltBridgeClient, VoltBridgeConnectionError, async_stream
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


def open_stream(client: VoltBridgeClient, udp_port: int, **overrides: float) -> AsyncIterator[LiveUpdate]:
    settings = {"poll_interval": 60, "silence_timeout": 60, "fallback_interval": 0.05, "offline_after": 5}
    return async_stream(
        client,
        **{**settings, **overrides},
        udp_port=udp_port,
        aggregated_port=None,
        bind="127.0.0.1",
    )


async def next_update(stream: AsyncIterator[LiveUpdate]) -> LiveUpdate:
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


async def test_silent_udp_falls_back_to_the_power_endpoint(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    async with aclosing(open_stream(client_for(device, session), free_udp_port(), silence_timeout=0.05)) as stream:
        first = await next_update(stream)
        update = await next_update(stream)
    assert update.source is UpdateSource.POWER
    assert update.data.net_power == 261
    assert update.data.gas == first.data.gas


async def test_udp_that_cannot_start_means_polling_only(
    device: FakeDevice, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*args: object, **kwargs: object) -> None:
        raise OSError

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
    async with aclosing(open_stream(client_for(device, session), free_udp_port(), offline_after=0.3)) as stream:
        await next_update(stream)
        for path in routes:
            routes[path] = (500, "down")
        with pytest.raises(VoltBridgeConnectionError):
            await next_update(stream)


async def test_failure_of_the_first_read_propagates(
    device: FakeDevice, session: aiohttp.ClientSession, routes: dict[str, tuple[int, str]]
) -> None:
    routes["/api/p1data"] = (500, "down")
    with pytest.raises(VoltBridgeConnectionError):
        await next_update(open_stream(client_for(device, session), free_udp_port()))


@pytest.mark.parametrize("name", ["poll_interval", "silence_timeout", "fallback_interval", "offline_after"])
def test_non_positive_intervals_are_rejected(device: FakeDevice, name: str) -> None:
    client = VoltBridgeClient("127.0.0.1")
    with pytest.raises(ValueError, match=name):
        async_stream(client, **{name: 0})
