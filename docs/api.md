# VoltBridge local API (observed)

Captured 2026-10-23 from one device, firmware `voltbridge`, version 15, variant 6 (`app:build-15.6`, `idf:v6.0.1`). No authentication, plain HTTP on port 80. Other common ports (443, 502, 1883, 8080) are closed.

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/` | GET | HTML status page; polls the JSON endpoints below |
| `/api/power` | GET | `netPowerW`, `importPowerW`, `exportPowerW`, `tariff`, `hasData` |
| `/api/p1data` | GET | Full telegram, see `tests/fixtures/p1data.json` |
| `/api/p1data?d=1` | GET | Lighter variant used by the page: averages, totals, Wi-Fi RSSI, packet counters |
| `/reboot` | POST | Reboots the device (form on the status page); not exercised by the capture |

Despite their names, `netPowerW` and the other power fields are in **kW**, and energy fields are in **kWh**. Negative `netPowerW` means export. `hasData: false` means no P1 telegram has been received.

## UDP broadcasts (not implemented)

The status page lists "UDP broadcast P1" on port 49491 and "UDP broadcast Aggregated" on port 49492. 49491 sent a binary, protobuf-looking packet (header `P1\x01`, containing the meter serial, version and power values) within seconds; 49492 sent nothing during a 5 s listen. The format is undecoded.
