# mygrid-voltbridge

A small typed async Python client for the local HTTP API of the **MyGrid VoltBridge** P1 meter adapter. Requires Python **3.12+** and `aiohttp`. Version 0.1.0 has not been published to PyPI.

The API is not documented by the manufacturer. It was reverse-engineered from the device's own status page and verified against one device (firmware `voltbridge` build 15.6); see [docs/api.md](docs/api.md). It may change between firmware versions.

## Query a meter

The helper is read-only:

```sh
uv sync
uv run mygrid-voltbridge 192.168.0.45
uv run mygrid-voltbridge 192.168.0.45 --power
```

## Library

```python
import asyncio

import aiohttp

from mygrid_voltbridge import VoltBridgeClient


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = VoltBridgeClient("192.168.0.45", session)
        power = await client.async_get_power()  # W, positive = import
        print(power.net_power, power.tariff)
        data = await client.async_get_p1_data()  # full snapshot
        print(data.energy_import_t1, data.phases[1].voltage)


asyncio.run(main())
```

| API | Contract |
| --- | --- |
| `async_get_power()` | `PowerReading` from `/api/power`; cheap, suitable for ~1 s polling |
| `async_get_p1_data()` | `P1Data` from `/api/p1data`; poll every few seconds at most |
| `async_listen(callback, host=...)` | Push updates: UDP broadcasts on 49491, about once a second; see below |
| `parse_datagram(bytes)` | Decode one UDP datagram into a `P1Broadcast` |
| `VoltBridgeInfo.from_properties(txt)` | Identify a device from its `_mygrid._tcp` mDNS TXT record (`device_id` is the MAC) |
| `async_get_telegram()` | `Telegram` from `/api/telegram`: M-Bus devices (gas, **water**), breaker, limiter, monthly peak history, CRC check |
| `async_get_telegram_text()` | The raw telegram string |
| `async_reboot()` | `POST /reboot`; never retried |

## Push updates

The device also broadcasts its telegram over UDP roughly once per second, so you do not have to poll:

```python
from mygrid_voltbridge import async_listen

transport = await async_listen(lambda b: print(b.data.net_power), host="192.168.0.45")
# ...
transport.close()
```

`host` filters by the device's IP; invalid datagrams are dropped. Use `port=49492` for the one-minute average stream. `uv run mygrid-voltbridge 192.168.0.45 --watch` prints the live stream as JSON lines. The broadcast carries no gas reading; use `async_get_p1_data()` for that. Broadcasts must reach the host, so a container or VLAN may need host networking or a broadcast relay. See [docs/api.md](docs/api.md) for the format.

Power is in **W**, energy in **Wh**, voltage in **V**, current in **A**; the device reports kW/kWh and the library converts. Fields the meter does not report are `None`. Errors derive from `VoltBridgeError`: `VoltBridgeConnectionError`, `VoltBridgeResponseError` and `VoltBridgeNoDataError` (no P1 telegram received yet).

Pass your own `aiohttp` session, or omit it: the client then creates one limited to a single connection (the device has very little memory) and closes it in `close()` or when leaving `async with VoltBridgeClient(host) as client`. Reads are retried twice with exponential backoff; the reboot is never retried. `VoltBridgeUnsupportedError` means the firmware lacks an endpoint (HTTP 404). There is no authentication or TLS on the device; keep it on the trusted LAN. Per-phase fields other than L1, and export power/energy on the UDP stream, are unverified.
