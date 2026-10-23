import json
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from mygrid_voltbridge import (
    P1Data,
    PowerReading,
    VoltBridgeClient,
    VoltBridgeConnectionError,
    VoltBridgeNoDataError,
    VoltBridgeResponseError,
)
from mygrid_voltbridge.cli import main
from mygrid_voltbridge.models import PhaseData


def make_client(
    server: TestServer, session: aiohttp.ClientSession, **kwargs: float
) -> VoltBridgeClient:
    return VoltBridgeClient("127.0.0.1", session, port=server.port, **kwargs)


async def test_power(device: TestServer, session: aiohttp.ClientSession) -> None:
    reading = await make_client(device, session).async_get_power()
    assert reading == PowerReading(
        net_power=261, import_power=261, export_power=0, tariff=1
    )


async def test_p1_data_real_capture(
    device: TestServer, session: aiohttp.ClientSession
) -> None:
    data = await make_client(device, session).async_get_p1_data()
    assert data.meter_serial == "1SAG0000000000"
    assert data.meter_version == "50217"
    assert (data.import_power, data.export_power, data.net_power) == (261, 0, 261)
    assert (data.avg_import_1min, data.avg_export_1min, data.avg_demand) == (
        261,
        0,
        199,
    )
    assert data.peak_demand == 2348
    assert data.peak_demand_at == datetime(2026, 10, 21, 18, 15, tzinfo=UTC)
    assert data.energy_import_t1 == 3673536
    assert data.energy_import_t2 == 4432452
    assert data.energy_export_t1 == data.energy_export_t2 == 0
    assert data.gas == 440.448
    assert data.phases == {1: PhaseData(261, 0, 237.3, 1.7)}


async def test_optional_fields_missing(
    device: TestServer,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/p1data"] = (200, json.dumps({"hasData": True}))
    data = await make_client(device, session).async_get_p1_data()
    assert data == P1Data(*[None] * 16)


async def test_no_data(
    device: TestServer,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/power"] = (200, json.dumps({"hasData": False}))
    with pytest.raises(VoltBridgeNoDataError):
        await make_client(device, session).async_get_power()


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        json.dumps({"hasData": True, "netPowerW": 1}),
        json.dumps(
            {"hasData": True, "netPowerW": "x", "importPowerW": 0, "exportPowerW": 0}
        ),
    ],
)
async def test_bad_power_bodies(
    device: TestServer,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
    body: str,
) -> None:
    routes["/api/power"] = (200, body)
    with pytest.raises(VoltBridgeResponseError):
        await make_client(device, session).async_get_power()


async def test_bad_timestamp(
    device: TestServer,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/p1data"] = (
        200,
        json.dumps({"hasData": True, "peakDemandAt": "yesterday"}),
    )
    with pytest.raises(VoltBridgeResponseError):
        await make_client(device, session).async_get_p1_data()


async def test_http_error(
    device: TestServer,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/power"] = (500, "boom")
    with pytest.raises(VoltBridgeResponseError, match="HTTP 500"):
        await make_client(device, session).async_get_power()


async def test_unreachable(session: aiohttp.ClientSession) -> None:
    client = VoltBridgeClient("127.0.0.1", session, port=1, timeout=1)
    with pytest.raises(VoltBridgeConnectionError):
        await client.async_get_power()


async def test_timeout(
    device: TestServer, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TimesOut:
        async def __aenter__(self) -> None:
            raise TimeoutError

        async def __aexit__(self, *exc: object) -> None:
            return None

    def slow(*args: object, **kwargs: object) -> TimesOut:
        return TimesOut()

    monkeypatch.setattr(session, "request", slow)
    with pytest.raises(VoltBridgeConnectionError, match="timed out"):
        await make_client(device, session).async_get_power()


async def test_reboot(device: TestServer, session: aiohttp.ClientSession) -> None:
    await make_client(device, session).async_reboot()
    assert device.rebooted == ["x"]  # type: ignore[attr-defined]


async def test_cli(device: TestServer, capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    loop = asyncio.get_running_loop()
    code = await loop.run_in_executor(
        None, main, ["127.0.0.1", "--port", str(device.port)]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["meter_serial"] == "1SAG0000000000"
    code = await loop.run_in_executor(
        None, main, ["127.0.0.1", "--port", "1", "--power"]
    )
    assert code == 1
    assert "error:" in capsys.readouterr().out


def test_discovery_info() -> None:
    from mygrid_voltbridge import VoltBridgeInfo

    info = VoltBridgeInfo.from_properties(
        {
            "model": "Voltbridge P1 Meter",
            b"manufacturer": b"MyGrid",
            "device_id": "907069d02d80",
            "api_path": "/api/p1data",
            "firmware_version": "app:build-15.6",
            "hardware_version": "P1Wifi-rev1",
        }
    )
    assert info.device_id == "907069D02D80"
    assert info.mac_address == "90:70:69:d0:2d:80"
    assert info.api_path == "/api/p1data"


@pytest.mark.parametrize(
    "properties",
    [
        {},
        {"manufacturer": "MyGrid", "device_id": "907069D02D80", "model": "ModuleTwo"},
        {"manufacturer": "MyGrid", "device_id": "nope", "model": "Voltbridge"},
        {"manufacturer": "Other", "device_id": "907069D02D80", "model": "Voltbridge"},
        {"manufacturer": "MyGrid", "device_id": "907069D02D80"},
    ],
)
def test_discovery_rejects_other_devices(properties: dict[str, str]) -> None:
    from mygrid_voltbridge import VoltBridgeInfo

    with pytest.raises(VoltBridgeResponseError):
        VoltBridgeInfo.from_properties(properties)


TELEGRAM = (
    (Path(__file__).parent / "fixtures" / "telegram.txt").read_bytes().decode("latin-1")
)


def test_telegram_parsing() -> None:
    from mygrid_voltbridge import GAS, WATER, parse_telegram

    telegram = parse_telegram(TELEGRAM)
    assert telegram.crc_valid
    assert telegram.header == "/FLU5\\000000000_A"
    assert telegram.timestamp == datetime(2026, 10, 23, 19, 16, 35, tzinfo=UTC)
    assert telegram.breaker_state == 1
    assert (telegram.limiter_threshold, telegram.fuse_threshold) == (999900, 999)
    assert (telegram.avg_demand, telegram.peak_demand) == (26, 2348)
    assert telegram.peak_demand_at == datetime(2026, 10, 21, 18, 15, tzinfo=UTC)
    assert [p.power for p in telegram.monthly_peaks] == [2780, 3576, 2811]
    assert telegram.monthly_peaks[0].peak_at == datetime(
        2026, 7, 19, 14, 15, tzinfo=UTC
    )
    gas = telegram.channel_of("gas")
    assert gas is not None and gas.device_type == GAS and gas.kind == "gas"
    assert (gas.reading, gas.unit, gas.valve_state) == (440.448, "m3", 1)
    assert gas.equipment_id == "GAS0000000"
    water = telegram.channel_of("water")
    assert water is not None and water.device_type == WATER
    assert (water.reading, water.unit, water.channel) == (33.692, "m3", 2)
    assert water.reading_at == datetime(2026, 10, 23, 19, 15, 30, tzinfo=UTC)
    assert telegram.channel_of("heat") is None
    assert telegram.objects["1-0:32.7.0"] == (("237.7*V",),)


def test_telegram_detects_corruption_and_garbage() -> None:
    from mygrid_voltbridge import parse_telegram

    assert not parse_telegram(TELEGRAM.replace("003673.673", "003673.674")).crc_valid
    for bad in ("", "no telegram here", "!1234/FLU5"):
        with pytest.raises(VoltBridgeResponseError):
            parse_telegram(bad)


def test_telegram_tolerates_unusual_content() -> None:
    from mygrid_voltbridge import parse_telegram

    minimal = parse_telegram(
        "/X\r\n\r\n0-0:1.0.0(991399250000S)\r\n1-0:1.4.0(abc)\r\n0-3:24.1.0(zz)\r\n0-3:96.1.1(zz)\r\n!0000\r\n"
    )
    assert not minimal.crc_valid
    assert minimal.timestamp is None and minimal.avg_demand is None
    channel = minimal.channels[3]
    assert (channel.device_type, channel.reading, channel.kind) == (
        None,
        None,
        "unknown",
    )
    assert channel.equipment_id == "zz"
    assert parse_telegram("/X\r\n\r\n!0000").channels == {}


async def test_telegram_over_http(
    device: TestServer,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/telegram"] = (200, TELEGRAM)
    client = make_client(device, session)
    assert await client.async_get_telegram_text() == TELEGRAM
    assert (await client.async_get_telegram()).crc_valid


async def test_unsupported_endpoint(
    device: TestServer, session: aiohttp.ClientSession
) -> None:
    from mygrid_voltbridge import VoltBridgeUnsupportedError

    with pytest.raises(VoltBridgeUnsupportedError):
        await make_client(device, session).async_get_telegram()


async def test_client_owns_session_when_none_given(device: TestServer) -> None:
    async with VoltBridgeClient("127.0.0.1", port=device.port) as client:
        assert (await client.async_get_power()).net_power == 261
        assert client._session is not None
    assert client._session is None
    await client.close()  # idempotent


async def test_reads_retry_with_backoff_but_writes_do_not(
    device: TestServer, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("mygrid_voltbridge.client.asyncio.sleep", fake_sleep)
    calls = 0
    real = session.request

    def flaky(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls < 3:
            raise aiohttp.ClientConnectionError("boom")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(session, "request", flaky)
    assert (await make_client(device, session).async_get_power()).net_power == 261
    assert calls == 3 and sleeps == [0.25, 0.5]

    calls = 0
    sleeps.clear()
    monkeypatch.setattr(
        session,
        "request",
        lambda *a, **k: (_ for _ in ()).throw(aiohttp.ClientConnectionError("x")),
    )
    with pytest.raises(VoltBridgeConnectionError):
        await make_client(device, session).async_reboot()
    assert sleeps == []
