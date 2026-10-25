import asyncio
from collections.abc import Callable
import dataclasses
from datetime import UTC, datetime
from pathlib import Path
import socket

import pytest

from mygrid_voltbridge import VoltBridgeResponseError
from mygrid_voltbridge.models import P1Data, P1Status, PhaseData
from mygrid_voltbridge.udp import BroadcastStream, P1Broadcast, _crc16_arc, async_listen, parse_datagram

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / f"{name}.hex").read_text().strip())


def rebuild(payload: bytes, *, stream: int = 0) -> bytes:
    header = b"P1\x01" + bytes([stream, 0, len(payload)]) + _crc16_arc(payload).to_bytes(2, "big")
    return header + payload


def test_live_stream_matches_http_capture() -> None:
    broadcast = parse_datagram(load("p1"))
    assert broadcast.stream is BroadcastStream.P1
    assert broadcast.timestamp == datetime(2026, 10, 23, 18, 51, 22, tzinfo=UTC)
    assert (broadcast.rssi, broadcast.country, broadcast.software_version) == (
        -79,
        "BE",
        "1.0",
    )
    data = broadcast.data
    assert (data.meter_serial, data.meter_version) == ("1SAG0000000000", "50217")
    assert (data.import_power, data.export_power, data.net_power) == (267, 0, 267)
    assert data.avg_import_1min is None
    assert (data.avg_demand, data.peak_demand) == (111, 2348)
    assert data.peak_demand_at == datetime(2026, 10, 21, 18, 15, tzinfo=UTC)
    assert data.tariff == 1
    assert (data.energy_import_t1, data.energy_import_t2) == (3673578, 4432452)
    assert (data.energy_export_t1, data.energy_export_t2) == (0, 0)
    assert data.phases == {1: PhaseData(267, 0, 237.4, 1.72)}


def test_aggregated_stream() -> None:
    broadcast = parse_datagram(load("aggregated"))
    assert broadcast.stream is BroadcastStream.AGGREGATED
    assert broadcast.software_version == "build-15.6"
    data = broadcast.data
    assert (data.import_power, data.net_power) == (None, None)
    assert (data.avg_import_1min, data.avg_export_1min, data.avg_demand) == (261, 0, 90)
    assert data.peak_demand is None
    assert data.phases == {}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b[:7],
        lambda b: b"XX" + b[2:],
        lambda b: b[:2] + b"\x02" + b[3:],
        lambda b: b[:3] + b"\x07" + b[4:],
        lambda b: b[:-1],
        lambda b: b[:20] + bytes([b[20] ^ 0xFF]) + b[21:],
    ],
)
def test_rejects_corrupt_datagrams(mutate: Callable[[bytes], bytes]) -> None:
    with pytest.raises(VoltBridgeResponseError):
        parse_datagram(mutate(load("p1")))


@pytest.mark.parametrize(
    "payload",
    [
        b"\x0a",  # truncated length
        b"\x0a\x05\x01",  # length past end
        b"\x0d\x00",  # truncated fixed32
        b"\x09\x00",  # truncated fixed64
        b"\x0b",  # unsupported wire type 3
        b"\x08" + b"\xff" * 11,  # varint too long
        b"\x0a\x00",  # no timestamp
        b"\x0a\x00\x2a\x03\x0a\x01\x41\x08",  # truncated trailing field
    ],
)
def test_rejects_malformed_payloads(payload: bytes) -> None:
    with pytest.raises(VoltBridgeResponseError):
        parse_datagram(rebuild(payload))


def test_rejects_non_numeric_scalars() -> None:
    # timestamp message whose field 1 is a string, then a string rssi value
    with pytest.raises(VoltBridgeResponseError):
        parse_datagram(rebuild(b"\x0a\x03\x0a\x01\x41"))
    ok_ts = b"\x0a\x06\x08\xaa\xd9\xee\xd6\x06"
    with pytest.raises(VoltBridgeResponseError):
        parse_datagram(rebuild(ok_ts + b"\x2a\x03\x0a\x01\x41"))


async def test_listener_delivers_and_filters() -> None:
    received: list[P1Broadcast] = []
    transport = await async_listen(received.append, host="127.0.0.1", port=0, bind="127.0.0.1")
    port = transport.get_extra_info("sockname")[1]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"garbage", ("127.0.0.1", port))
            sender.sendto(load("p1"), ("127.0.0.1", port))
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.02)
        assert [b.data.import_power for b in received] == [267]
    finally:
        transport.close()


async def test_listener_ignores_other_hosts() -> None:
    received: list[P1Broadcast] = []
    transport = await async_listen(received.append, host="10.9.9.9", port=0, bind="127.0.0.1")
    port = transport.get_extra_info("sockname")[1]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(load("p1"), ("127.0.0.1", port))
        await asyncio.sleep(0.2)
        assert received == []
    finally:
        transport.close()


async def test_cli_watch(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from mygrid_voltbridge import cli

    real_listen = cli.async_listen

    async def listen(
        callback: Callable[[P1Broadcast], None], *, host: str | None, port: int
    ) -> asyncio.DatagramTransport:
        transport = await real_listen(callback, host=host, port=0, bind="127.0.0.1")
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(load("p1"), ("127.0.0.1", transport.get_extra_info("sockname")[1]))
        sender.close()
        return transport

    monkeypatch.setattr(cli, "async_listen", listen)
    task = asyncio.create_task(cli._watch("127.0.0.1", 0))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if capsys.readouterr().out:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # main() wiring: a keyboard interrupt ends --watch cleanly
    monkeypatch.setattr(
        cli.asyncio,
        "run",
        lambda coro: (coro.close(), (_ for _ in ()).throw(KeyboardInterrupt()))[1],
    )
    assert cli.main(["127.0.0.1", "--watch"]) == 0
    _ = json


def test_cli_exits_quietly_when_the_output_pipe_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    from mygrid_voltbridge import cli

    def broken(_coro: object) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(cli.asyncio, "run", lambda coro: (coro.close(), broken(coro))[1])
    assert cli.main(["127.0.0.1"]) == 0


def test_zero_average_demand_is_omitted_on_the_wire_but_decoded_as_zero() -> None:
    """Proto3 drops zero floats; the shorter 146-byte datagrams lack only this field."""
    payload = load("p1")[8:]
    assert payload.count(b"\x52\x54") == 1
    assert payload.count(b"\x5a\x12\x0d\xf8\x53\xe3\x3d") == 1
    shorter = payload.replace(b"\x52\x54", b"\x52\x4f").replace(b"\x5a\x12\x0d\xf8\x53\xe3\x3d", b"\x5a\x0d")
    assert len(shorter) == len(payload) - 5
    broadcast = parse_datagram(rebuild(shorter))
    assert broadcast.data.avg_demand == 0
    assert broadcast.data.peak_demand == 2348
    assert broadcast.data.import_power == parse_datagram(load("p1")).data.import_power


def _snapshot() -> P1Data:
    """An HTTP-style snapshot of the meter in the captured datagrams."""
    return P1Data(
        meter_serial="1SAG0000000000",
        meter_version="50217",
        import_power=100,
        export_power=0,
        net_power=100,
        avg_import_1min=90,
        avg_export_1min=0,
        avg_demand=50,
        peak_demand=2348,
        peak_demand_at=datetime(2026, 10, 21, 18, 15, tzinfo=UTC),
        tariff=2,
        energy_import_t1=1,
        energy_import_t2=2,
        energy_export_t1=3,
        energy_export_t2=4,
        gas=440.5,
        phases={1: PhaseData(100, 0, 230.0, 0.5), 3: PhaseData(1, 1, 1.0, 1.0)},
    )


def test_live_broadcast_overlays_the_snapshot() -> None:
    broadcast = parse_datagram(load("p1"))
    merged = broadcast.apply_to(_snapshot())
    assert (merged.import_power, merged.net_power) == (267, 267)
    assert merged.tariff == 1
    assert merged.gas == 440.5  # the stream has no gas
    assert merged.avg_import_1min == 90  # nor one-minute averages
    assert merged.meter_version == "50217"
    assert merged.phases == broadcast.data.phases  # L3 disappeared with the meter


def test_aggregated_broadcast_refreshes_averages_only() -> None:
    broadcast = parse_datagram(load("aggregated"))
    snapshot = _snapshot()
    merged = broadcast.apply_to(snapshot)
    assert (merged.avg_import_1min, merged.avg_export_1min) == (
        broadcast.data.avg_import_1min,
        broadcast.data.avg_export_1min,
    )
    assert (merged.import_power, merged.net_power) == (100, 100)
    assert merged.phases == snapshot.phases


def test_broadcast_without_phases_keeps_the_snapshot_phases() -> None:
    broadcast = parse_datagram(load("p1"))
    bare = dataclasses.replace(broadcast, data=dataclasses.replace(broadcast.data, phases={}))
    assert bare.apply_to(_snapshot()).phases == _snapshot().phases


@pytest.mark.parametrize("serial", ["1SAG9999999999", None])
def test_broadcast_from_another_meter_is_ignored(serial: str | None) -> None:
    broadcast = parse_datagram(load("p1"))
    other = dataclasses.replace(broadcast, data=dataclasses.replace(broadcast.data, meter_serial=serial))
    snapshot = _snapshot()
    assert other.apply_to(snapshot) is snapshot
    assert dataclasses.replace(snapshot, meter_serial=None) == broadcast.apply_to(
        dataclasses.replace(snapshot, meter_serial=None)
    )


def test_broadcast_updates_the_signal_strength() -> None:
    status = P1Status(rssi=-90, p1_fresh=True, packets_total=10, packets_valid=10, packets_error=0, error_percent=0.0)
    broadcast = parse_datagram(load("p1"))
    assert broadcast.apply_to_status(status).rssi == -79
    assert dataclasses.replace(broadcast, rssi=None).apply_to_status(status) is status
