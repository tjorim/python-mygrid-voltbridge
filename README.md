# mygrid-voltbridge

A small typed async Python client for the local HTTP API of the [**MyGrid VoltBridge**](https://moduletwo.mygrid.energy/nl/voltbridge) P1 meter adapter. Requires Python **3.12+** and `aiohttp`. Install it with `pip install mygrid-voltbridge` ([PyPI](https://pypi.org/project/mygrid-voltbridge/)).

The API is not documented by the manufacturer. It was reverse-engineered from the device's own status page and verified against one device (firmware `voltbridge` build 15.6); see [docs/api.md](docs/api.md). It may change between firmware versions.

## Documentation

See the [MyGrid documentation hub](https://tjorim.github.io/custom-mygrid/) for the integration guides and related projects. The [hub README](https://github.com/tjorim/custom-mygrid#readme) is also available as a fallback.

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
        print(data.energy_import, data.phases[1].voltage)  # Wh: T1 + T2


asyncio.run(main())
```

| API | Contract |
| --- | --- |
| `async_get_power()` | `PowerReading` from `/api/power`; cheap, suitable for ~1 s polling |
| `async_get_p1_data()` | `P1Data` from `/api/p1data`; poll every few seconds at most |
| `async_listen(callback, host=...)` | Push updates: UDP broadcasts on 49491, about once a second; see below |
| `async_stream(client)` | A merged live feed that follows UDP and falls back to HTTP when it goes quiet; see [Choosing a source](#choosing-a-source) |
| `parse_datagram(bytes)` | Decode one UDP datagram into a `P1Broadcast` |
| `VoltBridgeInfo.from_properties(txt)` | Identify a device from its `_mygrid._tcp` mDNS TXT record (`device_id` is the MAC) |
| `async_get_status()` | `P1Status` from `/api/p1data?d=1`: Wi-Fi signal and P1 packet error statistics |
| `async_get_device_info()` | `DeviceInfo` from the HTML status page `/` (no JSON endpoint exists): MAC, hostname, firmware, uptime, boot reason, free heap, USB-C power. Scraped by row label, so call it rarely |
| `async_get_telegram()` | `Telegram` from `/api/telegram`: M-Bus devices (gas, **water**), breaker, limiter, monthly peak history, CRC check, meter model and DSMR version; limiter and fuse thresholds are `None` when unset |
| `async_get_telegram_text()` | The raw telegram string |
| `async_reboot()` | `POST /reboot`; never retried |

## Choosing a source

The device offers two ways in, and they have different strengths. Measured on a device with weak Wi-Fi (-81 dBm) over three runs of 45-90 s:

| Source | Strength | Weakness |
| --- | --- | --- |
| UDP broadcasts (`async_listen`) | About once a second, no requests to the device | Roughly 20-35 % of datagrams were lost; gaps of 2-4 s. Cannot start where broadcasts do not reach the host (containers, VLANs). No gas |
| `/api/power` (`async_get_power`) | Median 59 ms, tiny reply | Only power and tariff |
| `/api/p1data` (`async_get_p1_data`) | Everything the JSON API has, including gas | Median 71 ms; the device has little memory, so poll every few seconds at most |
| `/api/telegram` (`async_get_telegram`) | The only source of water, breaker state and the monthly peak history; also per-phase values | Same cost as the other HTTP reads |

All sources agreed to the watt. Gas and water meters report only every minute or two, so reading them faster adds nothing.

`async_stream` combines them so you do not have to:

```python
from contextlib import aclosing

from mygrid_voltbridge import async_stream

async with aclosing(async_stream(client)) as stream:
    async for update in stream:
        print(update.source, update.data.net_power, update.data.gas)
```

Each `LiveUpdate` carries the merged `P1Data`, where it came from (`udp`, `power` or `p1data`), and `snapshot_at`, the time of the HTTP snapshot underneath (gas can be that old). The feed merges each broadcast from your meter into the last snapshot, reads `/api/power` once a second while UDP has been silent for 2 s (or could not start), refreshes `/api/p1data` every 5 s, skips failed reads, and raises `VoltBridgeConnectionError` if nothing arrives for 30 s. All of these are keyword arguments. On the test device it produced an update about every second with no gap over 2.5 s, where UDP alone had gaps of about 4 s.

If you prefer to wire it yourself, `broadcast.apply_to(snapshot)` and `reading.apply_to(snapshot)` do the merging.

## Push updates

The device also broadcasts its telegram over UDP roughly once per second, so you do not have to poll:

```python
from mygrid_voltbridge import async_listen

transport = await async_listen(lambda b: print(b.data.net_power), host="192.168.0.45")
# ...
transport.close()
```

`host` filters by the device's IP; invalid datagrams are dropped. To keep a polled snapshot fresh, overlay each broadcast with `broadcast.apply_to(snapshot)` (and `broadcast.apply_to_status(status)` for the signal strength): broadcasts from other meters are ignored, fields the stream lacks (gas) keep their polled value, and a datagram without phase data does not wipe the phases. Use `port=49492` for the one-minute average stream. `uv run mygrid-voltbridge 192.168.0.45 --watch` prints the live stream as JSON lines. The broadcast carries no gas reading; use `async_get_p1_data()` for that. Broadcasts must reach the host, so a container or VLAN may need host networking or a broadcast relay. See [docs/api.md](docs/api.md) for the format.

Power is in **W**, energy in **Wh**, voltage in **V**, current in **A**; the device reports kW/kWh and the library converts. Fields the meter does not report are `None`. Errors derive from `VoltBridgeError`: `VoltBridgeConnectionError`, `VoltBridgeResponseError` and `VoltBridgeNoDataError` (no P1 telegram received yet).

Pass your own `aiohttp` session, or omit it: the client then creates one limited to a single connection (the device has very little memory) and closes it in `close()` or when leaving `async with VoltBridgeClient(host) as client`. Reads are retried twice with exponential backoff; the reboot is never retried. `VoltBridgeUnsupportedError` means the firmware lacks an endpoint (HTTP 404). There is no authentication or TLS on the device; keep it on the trusted LAN. Per-phase fields other than L1, and export power/energy on the UDP stream, are unverified.
