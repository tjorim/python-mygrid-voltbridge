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

## UDP broadcasts (push)

The device broadcasts on the LAN; no subscription is needed. Port 49491 ("P1") sends the live telegram about once per second; port 49492 ("Aggregated") sends one-minute averages roughly once per minute (observed: one packet in 40 s). Both come from the device's own IP.

### Framing

| Bytes | Meaning |
| --- | --- |
| 0-1 | Magic `P1` |
| 2 | Protocol version, `1` |
| 3 | Stream: `0` live (49491), `1` aggregated (49492) |
| 4 | `0` in every capture (unknown) |
| 5 | Payload length |
| 6-7 | CRC-16/ARC of the payload, big endian (matched 30 of 30 captured packets) |
| 8- | Protobuf payload |

### Payload fields (observed, 1.0 / build 15.6)

Floats are kW/kWh/V/A as on the HTTP API; proto3 omits zeros, so an absent number means 0.

| Field | Meaning |
| --- | --- |
| `1.1` | Unix timestamp (seconds) |
| `2` | String: `1.0` on the live stream, `build-15.6` on the aggregated stream |
| `4` | Country, `BE` (live stream only) |
| `5.1` | Wi-Fi RSSI as a 64-bit signed varint |
| `6.1` / `6.2` | Meter serial / meter version |
| `10` (live) or `11` (aggregated) | Measurements, below |

Measurements: `1` = {`1` import kW, `2` export kW}; `2`/`3`/`4` = L1/L2/L3 {`1` import kW, `2` export kW, `3` volts, `4` amps} (empty when the phase has no data); `10` = {`1`/`2` import T1/T2 kWh as doubles, `3`/`4` export T1/T2 kWh, `5` tariff}; `11` = {`1` average demand kW, `2` peak demand kW, `3.1` peak timestamp}. On the aggregated stream `1` and `2` hold one-minute averages. Field `10.12` ({1, 999.9, 999.0}) is undecoded; gas does not appear in the broadcasts.

Verified against a simultaneous `/api/p1data` read: import power, average demand, voltage, current and T1 energy all matched. **Unverified:** export power and export energy (field numbers inferred from the symmetric import layout, since this installation has no export) and L2/L3.

## mDNS

The device advertises `<hostname>.local` (`voltbridge-` + the last three MAC bytes) as `_http._tcp` and `_mygrid._tcp` on port 80. The `_mygrid._tcp` TXT record carries `model=Voltbridge P1 Meter`, `manufacturer=MyGrid`, `device_id` (the MAC without separators), `api_path=/api/p1data`, `firmware_version` and `hardware_version=P1Wifi-rev1`.
