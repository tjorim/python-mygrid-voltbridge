import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp.test_utils import TestServer
import pytest

from mygrid_voltbridge import (
    DeviceInfo,
    P1Data,
    PowerReading,
    VoltBridgeClient,
    VoltBridgeConnectionError,
    VoltBridgeNoDataError,
    VoltBridgeResponseError,
)
from mygrid_voltbridge.cli import main
from mygrid_voltbridge.models import PhaseData
from tests.fake_device import FakeDevice


def port_of(server: TestServer) -> int:
    port = server.port
    assert isinstance(port, int)
    return port


def make_client(
    server: TestServer, session: aiohttp.ClientSession, *, retries: int = 2, timeout: float = 5.0
) -> VoltBridgeClient:
    return VoltBridgeClient(
        "127.0.0.1", session, port=port_of(server), retries=retries, timeout=timeout, min_request_interval=0
    )


async def test_power(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    reading = await make_client(device, session).async_get_power()
    assert reading == PowerReading(net_power=261, import_power=261, export_power=0, tariff=1)


async def test_p1_data_real_capture(device: FakeDevice, session: aiohttp.ClientSession) -> None:
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
    device: FakeDevice,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/p1data"] = (200, json.dumps({"hasData": True}))
    data = await make_client(device, session).async_get_p1_data()
    assert data == P1Data(**dict.fromkeys(_P1_SCALAR_FIELDS), phases={})


async def test_no_data(
    device: FakeDevice,
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
        json.dumps({"hasData": True, "netPowerW": "x", "importPowerW": 0, "exportPowerW": 0}),
    ],
)
async def test_bad_power_bodies(
    device: FakeDevice,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
    body: str,
) -> None:
    routes["/api/power"] = (200, body)
    with pytest.raises(VoltBridgeResponseError):
        await make_client(device, session).async_get_power()


async def test_bad_timestamp(
    device: FakeDevice,
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
    device: FakeDevice,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/power"] = (418, "teapot")
    with pytest.raises(VoltBridgeResponseError, match="HTTP 418") as raised:
        await make_client(device, session).async_get_power()
    assert raised.value.status == 418


async def test_unreachable(session: aiohttp.ClientSession) -> None:
    client = VoltBridgeClient("127.0.0.1", session, port=1, timeout=1)
    with pytest.raises(VoltBridgeConnectionError):
        await client.async_get_power()


async def test_timeout(device: FakeDevice, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch) -> None:
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


async def test_reboot(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    await make_client(device, session).async_reboot()
    assert device.rebooted == ["x"]


async def test_cli(device: FakeDevice, capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    loop = asyncio.get_running_loop()
    code = await loop.run_in_executor(None, main, ["127.0.0.1", "--port", str(device.port)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["meter_serial"] == "1SAG0000000000"
    code = await loop.run_in_executor(None, main, ["127.0.0.1", "--port", "1", "--power"])
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
def test_discovery_rejects_other_devices(properties: dict[str | bytes, str | bytes | None]) -> None:
    from mygrid_voltbridge import VoltBridgeInfo

    with pytest.raises(VoltBridgeResponseError):
        VoltBridgeInfo.from_properties(properties)


_P1_SCALAR_FIELDS = tuple(f.name for f in dataclasses.fields(P1Data) if f.name != "phases")
TELEGRAM = (Path(__file__).parent / "fixtures" / "telegram.txt").read_bytes().decode("latin-1")


def test_telegram_parsing() -> None:
    from mygrid_voltbridge import GAS, WATER, parse_telegram

    telegram = parse_telegram(TELEGRAM)
    assert telegram.crc_valid
    assert telegram.header == "/FLU5\\000000000_A"
    assert telegram.timestamp == datetime(2026, 10, 23, 19, 16, 35, tzinfo=UTC)
    assert telegram.breaker_state == 1
    assert (telegram.limiter_threshold, telegram.fuse_threshold) == (None, None)
    assert (telegram.meter_manufacturer, telegram.meter_model) == ("FLU", "000000000_A")
    assert telegram.dsmr_version == "50"
    assert (telegram.avg_demand, telegram.peak_demand) == (26, 2348)
    assert telegram.peak_demand_at == datetime(2026, 10, 21, 18, 15, tzinfo=UTC)
    assert [p.power for p in telegram.monthly_peaks] == [2780, 3576, 2811]
    assert telegram.monthly_peaks[0].peak_at == datetime(2026, 7, 19, 14, 15, tzinfo=UTC)
    gas = telegram.channel_of("gas")
    assert gas is not None
    assert gas.device_type == GAS
    assert gas.kind == "gas"
    assert (gas.reading, gas.unit, gas.valve_state) == (440.448, "m3", 1)
    assert gas.equipment_id == "GAS0000000"
    water = telegram.channel_of("water")
    assert water is not None
    assert water.device_type == WATER
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
    assert minimal.timestamp is None
    assert minimal.avg_demand is None
    channel = minimal.channels[3]
    assert (channel.device_type, channel.reading, channel.kind) == (
        None,
        None,
        "unknown",
    )
    assert channel.equipment_id == "zz"
    assert parse_telegram("/X\r\n\r\n!0000").channels == {}


async def test_telegram_over_http(
    device: FakeDevice,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
) -> None:
    routes["/api/telegram"] = (200, TELEGRAM)
    client = make_client(device, session)
    assert await client.async_get_telegram_text() == TELEGRAM
    assert (await client.async_get_telegram()).crc_valid


async def test_unsupported_endpoint(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    from mygrid_voltbridge import VoltBridgeUnsupportedError

    with pytest.raises(VoltBridgeUnsupportedError):
        await make_client(device, session).async_get_telegram()


async def test_client_owns_session_when_none_given(device: FakeDevice) -> None:
    async with VoltBridgeClient("127.0.0.1", port=port_of(device)) as client:
        assert (await client.async_get_power()).net_power == 261
        assert client._session is not None
    assert client._session is None
    await client.close()  # idempotent


async def test_reads_retry_with_backoff_but_writes_do_not(
    device: FakeDevice, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("mygrid_voltbridge.client.asyncio.sleep", fake_sleep)
    calls = 0
    real = session.request

    def flaky(method: str, url: str, **kwargs: Any) -> object:
        nonlocal calls
        calls += 1
        if calls < 3:
            msg = "boom"
            raise aiohttp.ClientConnectionError(msg)
        return real(method, url, **kwargs)

    monkeypatch.setattr(session, "request", flaky)
    assert (await make_client(device, session).async_get_power()).net_power == 261
    assert calls == 3
    assert sleeps == [0.25, 0.5]

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


async def test_server_errors_are_retried_then_reported_as_unreachable(
    device: FakeDevice,
    session: aiohttp.ClientSession,
    routes: dict[str, tuple[int, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("mygrid_voltbridge.client.asyncio.sleep", fake_sleep)
    routes["/api/power"] = (503, "busy")
    with pytest.raises(VoltBridgeConnectionError, match="HTTP 503"):
        await make_client(device, session, retries=2).async_get_power()
    assert sleeps == [0.25, 0.5]


@pytest.mark.parametrize(
    "kwargs",
    [{"retries": -1}, {"timeout": 0}, {"timeout": -1}, {"min_request_interval": -0.1}],
)
def test_constructor_validates_arguments(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must be"):
        VoltBridgeClient("127.0.0.1", **kwargs)


async def test_requests_are_serialised_and_spaced(
    device: FakeDevice, session: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def record(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr("mygrid_voltbridge.client.asyncio.sleep", record)
    client = VoltBridgeClient("127.0.0.1", session, port=port_of(device), min_request_interval=5)
    results = await asyncio.gather(*(client.async_get_power() for _ in range(3)))
    assert len(results) == 3
    assert len(sleeps) == 2
    assert all(0 < s <= 5 for s in sleeps)


async def test_status_real_capture(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    from mygrid_voltbridge import P1Status

    status = await make_client(device, session).async_get_status()
    assert status == P1Status(
        rssi=-76,
        p1_fresh=True,
        packets_total=47957,
        packets_valid=47957,
        packets_error=0,
        error_percent=0.0,
    )


async def test_status_without_statistics(
    device: FakeDevice, session: aiohttp.ClientSession, routes: dict[str, tuple[int, str]]
) -> None:
    routes["/api/p1data?d=1"] = (200, json.dumps({"hasData": True, "hasStats": False, "rssi": -60}))
    status = await make_client(device, session).async_get_status()
    assert status.rssi == -60
    assert status.packets_total is None
    assert status.error_percent is None
    assert status.p1_fresh is None


async def test_status_needs_data(
    device: FakeDevice, session: aiohttp.ClientSession, routes: dict[str, tuple[int, str]]
) -> None:
    routes["/api/p1data?d=1"] = (200, json.dumps({"hasData": False}))
    with pytest.raises(VoltBridgeNoDataError):
        await make_client(device, session).async_get_status()


def test_telegram_reports_configured_limits_and_unknown_identity() -> None:
    from mygrid_voltbridge import parse_telegram

    text = TELEGRAM.replace("0-0:17.0.0(999.9*kW)", "0-0:17.0.0(006.9*kW)")
    text = text.replace("1-0:31.4.0(999*A)", "1-0:31.4.0(040*A)")
    telegram = dataclasses.replace(parse_telegram(text), header="not a header", objects={})
    assert telegram.meter_manufacturer is None
    assert telegram.meter_model is None
    assert telegram.dsmr_version is None
    # CRC is not valid after the edit, but the thresholds are still decoded.
    edited = parse_telegram(text)
    assert (edited.limiter_threshold, edited.fuse_threshold) == (6900, 40)
    assert not edited.crc_valid


def test_p1_data_energy_totals() -> None:
    assert P1Data(**dict.fromkeys(_P1_SCALAR_FIELDS), phases={}).energy_import is None
    data = dataclasses.replace(
        P1Data(**dict.fromkeys(_P1_SCALAR_FIELDS), phases={}),
        energy_import_t1=3673837,
        energy_import_t2=4444808,
        energy_export_t2=65,
    )
    assert data.energy_import == 8118645
    assert data.energy_export == 65


async def test_device_info(device: FakeDevice, session: aiohttp.ClientSession) -> None:
    info = await make_client(device, session).async_get_device_info()
    assert info.mac_address == "aa:bb:cc:dd:ee:ff"
    assert info.hostname == "voltbridge-DDEEFF.local"
    assert (info.firmware_type, info.firmware_version, info.firmware_variant) == ("voltbridge", 15, 6)
    assert info.build_info == "app:build-15.6, idf:v6.0.1, date:202609272035"
    assert info.uptime == timedelta(hours=9, minutes=23, seconds=32)
    assert (info.boot_reason, info.last_reboot_reason) == ("Power on", "Power on")
    assert (info.free_heap, info.min_free_heap) == (38896, 768)
    assert (info.usb_power_connected, info.p1_out_connected) == (False, True)


def test_device_info_tolerates_missing_and_odd_rows() -> None:
    page = (
        "<table><tr><td>MAC address</td><td>not a mac</td></tr>"
        "<tr><td>Firmware type, version, variant</td><td>voltbridge, 16, 1</td></tr>"
        "<tr><td>Uptime</td><td>2 days, 3:04:05</td></tr>"
        "<tr><td>Free heap</td><td>plenty</td></tr>"
        "<tr><td>USB-C power</td><td><b>Connected</b></td></tr></table>"
    )
    info = DeviceInfo.from_html(page)
    assert info.mac_address is None
    assert (info.firmware_version, info.firmware_variant) == (16, 1)
    assert info.uptime == timedelta(days=2, hours=3, minutes=4, seconds=5)
    assert (info.free_heap, info.min_free_heap, info.hostname, info.build_info) == (None, None, None, None)
    assert info.usb_power_connected is True
    assert info.p1_out_connected is None


@pytest.mark.parametrize(
    "page", ["", "<html>Not Found</html>", "<table><tr><td>Uptime</td><td>1:00:00</td></tr></table>"]
)
def test_device_info_rejects_other_pages(page: str) -> None:
    with pytest.raises(VoltBridgeResponseError):
        DeviceInfo.from_html(page)


def test_telegram_decodes_phases_with_the_standard_codes() -> None:
    from mygrid_voltbridge import parse_telegram

    assert parse_telegram(TELEGRAM).phases == {1: PhaseData(250, 0, 237.7, 1.83)}
    three_phase = TELEGRAM.replace(
        "1-0:31.4.0(999*A)\r\n",
        "1-0:31.4.0(999*A)\r\n"
        "1-0:41.7.0(00.100*kW)\r\n1-0:42.7.0(00.020*kW)\r\n1-0:52.7.0(231.5*V)\r\n1-0:51.7.0(000.43*A)\r\n"
        "1-0:61.7.0(00.300*kW)\r\n1-0:72.7.0(229.0*V)\r\n",
    )
    assert three_phase != TELEGRAM
    phases = parse_telegram(three_phase).phases
    assert phases[2] == PhaseData(100, 20, 231.5, 0.43)
    assert phases[3] == PhaseData(300, None, 229.0, None)


def test_telegram_gas_and_water_shortcuts() -> None:
    from mygrid_voltbridge import parse_telegram

    telegram = parse_telegram(TELEGRAM)
    assert telegram.gas is telegram.channel_of("gas")
    assert telegram.water is telegram.channel_of("water")
    assert telegram.gas is not None
    assert telegram.water is not None


def test_tariff_names_and_power_overlay() -> None:
    from mygrid_voltbridge import TARIFF_NAMES

    assert TARIFF_NAMES == {1: "day", 2: "night"}
    snapshot = dataclasses.replace(P1Data(**dict.fromkeys(_P1_SCALAR_FIELDS), phases={}), tariff=1)
    assert snapshot.tariff_name == "day"
    assert dataclasses.replace(snapshot, tariff=None).tariff_name is None
    assert dataclasses.replace(snapshot, tariff=9).tariff_name is None
    reading = PowerReading(net_power=-50, import_power=0, export_power=50, tariff=2)
    merged = reading.apply_to(snapshot)
    assert (merged.net_power, merged.import_power, merged.export_power, merged.tariff) == (-50, 0, 50, 2)
    assert reading.tariff_name == "night"
    kept = dataclasses.replace(reading, tariff=None).apply_to(snapshot)
    assert kept.tariff == 1
