import json
from datetime import UTC, datetime

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
