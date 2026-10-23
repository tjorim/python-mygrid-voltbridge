"""Live smoke tests: check a real device still speaks what the library expects.

They assert stable structure and cross-endpoint consistency, never specific
readings. Detailed behaviour is covered by the offline suite.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from mygrid_voltbridge import BroadcastStream, P1Broadcast, async_listen
from tests.live.live_device import LiveDevice

TOLERANCE_W = 3000  # the endpoints are read seconds apart; this only catches unit mistakes


async def test_power_and_p1_data_agree(live: LiveDevice) -> None:
    power = await live.fetch("/api/power", lambda c: c.async_get_power())
    data = await live.fetch("/api/p1data", lambda c: c.async_get_p1_data())
    assert data.meter_serial is not None
    assert power.tariff in (1, 2)
    assert data.net_power is not None
    assert abs(power.net_power - data.net_power) < TOLERANCE_W
    assert data.energy_import_t1 is not None
    assert data.energy_import_t1 > 0
    assert data.phases, "no phase data"


async def test_telegram_is_valid_and_consistent_with_json(live: LiveDevice) -> None:
    data = await live.fetch("/api/p1data", lambda c: c.async_get_p1_data())
    telegram = await live.fetch("/api/telegram", lambda c: c.async_get_telegram())
    assert telegram.crc_valid
    assert telegram.timestamp is not None
    assert abs(datetime.now(UTC) - telegram.timestamp) < timedelta(minutes=5)
    assert telegram.peak_demand == data.peak_demand
    assert telegram.peak_demand_at == data.peak_demand_at
    gas = telegram.channel_of("gas")
    if gas is not None and data.gas is not None:
        assert gas.reading is not None
        assert abs(gas.reading - data.gas) < 1


async def test_udp_broadcast_matches_http(live: LiveDevice, host: str) -> None:
    data = await live.fetch("/api/p1data", lambda c: c.async_get_p1_data())
    queue: asyncio.Queue[P1Broadcast] = asyncio.Queue()
    transport = await async_listen(queue.put_nowait, host=host)
    try:
        broadcast = await asyncio.wait_for(queue.get(), timeout=10)
    finally:
        transport.close()
    assert broadcast.stream is BroadcastStream.P1
    assert broadcast.data.meter_serial == data.meter_serial
    assert broadcast.data.net_power is not None
    assert data.net_power is not None
    assert abs(broadcast.data.net_power - data.net_power) < TOLERANCE_W
    assert broadcast.data.energy_import_t1 is not None
    assert abs(broadcast.data.energy_import_t1 - (data.energy_import_t1 or 0)) < 1000  # Wh
