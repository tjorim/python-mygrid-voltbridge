# VoltBridge local API reference (reverse-engineered)

This is an unofficial community reference, not a manufacturer API contract.
Observations cover **one VoltBridge, one single-phase meter and one firmware
build**. Export readings, L2/L3 and other firmware have not been verified on
hardware. Please [report findings](https://github.com/tjorim/python-mygrid-voltbridge/issues)
from other firmware or phase setups, including the firmware build and which
HTTP/UDP fields you checked. Remove MAC addresses, meter/equipment serials,
hostnames, IP addresses and personal network details from captures before sharing.
Examples below use field names and placeholders, not device identifiers.

For installation, see the [Home Assistant setup guide](https://tjorim.github.io/custom-mygrid/docs/voltbridge/); for client
usage, see the [Python library](../README.md).

Captured 2026-10-23 from one device, firmware `voltbridge`, version 15, variant 6 (`app:build-15.6`, `idf:v6.0.1`). The observed device serves plain HTTP on port 80 without authentication. Keep it on a trusted LAN. Ports 443, 502, 1883 and 8080 were closed in that capture; this is not a guarantee for other firmware.

## HTTP endpoints

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/` | GET | HTML status page; polls the JSON endpoints below. Its System table is the only source of the adapter's MAC address, hostname, firmware (`voltbridge, 15, 6` and `app:build-15.6, idf:v6.0.1, date:...`), uptime, boot reasons, free/minimum free heap and USB-C power; decoded by `DeviceInfo.from_html`. No JSON endpoint carries these (`/api/info`, `/api/device`, `/api/version`, `/api/status`, `/api/system` return 404) |
| `/api/power` | GET | `netPowerW`, `importPowerW`, `exportPowerW`, `tariff`, `hasData` |
| `/api/p1data` | GET | Full JSON P1 snapshot: meter identity, power, phases, demand, energy totals and gas (not the raw telegram) |
| `/api/telegram` | GET | The raw DSMR/Fluvius telegram as `text/plain` with the meter's `!XXXX` CRC (CRC-16/ARC, valid as served). Carries everything the JSON endpoints omit: water and other M-Bus devices, breaker and limiter state, monthly peak history |
| `/api/p1data?d=1` | GET | Lighter variant used by the page: averages, totals, Wi-Fi RSSI, packet counters |
| `/reboot` | POST | Reboots the device (form on the status page); not exercised by the original capture. The library never retries this command |

Despite their names, `netPowerW` and the other power fields are in **kW**, and energy fields are in **kWh**. Negative `netPowerW` means export. `hasData: false` means no P1 telegram has been received.

The library multiplies kW/kWh by 1000 and rounds to integer **W/Wh**; voltage
and current stay in **V/A**, and gas/water volumes stay in **m³**. For example,
`netPowerW: 1.234` represents 1234 W, despite the HTTP field's suffix. Missing
HTTP readings remain `None` in the library; do not interpret them as zero.

### The `d` variant

With `d` present, `/api/p1data` drops the power, serial, peak, gas and L1 fields and adds `rssi`, `p1Fresh`, `packetsTotal`, `packetsValid`, `packetsError`, `packetsErrPerc` and the flags `hasAvg`, `hasTotals` and `hasStats`. The fields both variants share (averages and energy totals) had identical values. So the status read gives no gas, and the plain read gives no Wi-Fi or packet statistics. `P1Status` reads only `hasStats` from the flags.

### Explored and not available

Probed with GET only, one request per second, on firmware build 15.6: `/api`, `/api/`, `/api/p1`, `/api/info`, `/api/device`, `/api/version`, `/api/status`, `/api/system`, `/api/about`, `/api/config`, `/api/network`, `/api/health`, `/api/stats`, `/api/mbus`, `/api/gas`, `/api/water`, `/api/peaks`, `/api/history`, `/api/log`, `/api/logs`, `/api/wifi`, `/api/settings`, `/api/diag`, `/api/debug`, `/api/meter`, `/api/p1status`, `/api/udp`, `/api/mqtt`, `/api/telegram/raw`, `/info`, `/version`, `/status`, `/device`, `/health`, `/metrics`, `/debug`, `/settings`, `/robots.txt`, `/favicon.ico` and `/manifest.json` all return 404 (`text/html`, 29 bytes). Not probed, because they may change state: update/OTA, reset and other action-style paths. The status page's script calls only `/api/power` and `/api/p1data?d=1`.

### JSON field meanings

| Fields | Meaning / device units |
| --- | --- |
| `netPowerW`, `importPowerW`, `exportPowerW` | `/api/power`: instantaneous net, import and export power, kW |
| `hasData` | Whether a P1 telegram has been received; false is not a zero-consumption reading |
| `meterSerial`, `meterVersion` | P1 meter identity and version, not the VoltBridge MAC or firmware |
| `activePowerNet`, `importPower`, `exportPower` | Full snapshot instantaneous power, kW; positive net means import |
| `importPowerL<n>`, `exportPowerL<n>`, `voltageL<n>`, `currentL<n>` | Phase n (1–3): kW, kW, V, A; only L1 verified |
| `avgImport1min`, `avgExport1min` | One-minute average import/export power, kW |
| `avgDemand` | Current quarter-hour average demand, kW |
| `peakDemand`, `peakDemandAt` | Monthly peak demand in kW and its timestamp |
| `energyImportT1`, `energyImportT2`, `energyExportT1`, `energyExportT2` | Cumulative tariff energy counters, kWh |
| `tariff` | Active tariff number (T1/T2); the integration labels these day/night |
| `gasM3` | Cumulative gas volume, m³; water is available through the raw telegram instead |

Gas does **not** arrive in the decoded UDP broadcasts. Read `/api/p1data` or
`/api/telegram` for gas, and `/api/telegram` for water. Their timestamps and
updates follow the attached M-Bus meters, not the one-second electricity stream:
readings can change only every few minutes to an hour. The exact cadence is
meter-dependent and has not been established for every setup; faster polling
cannot make a meter publish a new reading.

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

Validate the magic, version, stream, payload length and CRC before decoding.
The CRC covers **only bytes 8 onward**, not the eight-byte header, and its two
header bytes are big endian. CRC-16/ARC uses initial value `0x0000`, reflected
polynomial `0xA001` (normal form `0x8005`) and no final XOR. It matched all 30
packets in the original capture. Packet length and contents may vary by firmware.

### Payload fields (observed, 1.0 / build 15.6)

Power, energy, voltage and current use kW/kWh/V/A as on HTTP. In a present measurement message, proto3 omits zero-valued scalars; a missing scalar can therefore mean 0. An absent or empty phase message does not establish that the phase exists: the library leaves unavailable phases absent.

| Field | Meaning |
| --- | --- |
| `1.1` | Unix timestamp (seconds) |
| `2` | String: `1.0` on the live stream, `build-15.6` on the aggregated stream |
| `4` | Country, `BE` (live stream only) |
| `5.1` | Wi-Fi RSSI as a 64-bit signed varint |
| `6.1` / `6.2` | Meter serial / meter version |
| `10` (live) or `11` (aggregated) | Measurements, below |

The dotted paths above mean nested protobuf field numbers, not decimal numbers.
Fixed32 floats and fixed64 doubles use protobuf little-endian encoding; RSSI
uses a signed 64-bit value encoded as a varint (not ZigZag).

Paths in the next table are relative to the measurements message (`10` on
live packets, `11` on aggregated packets).

| Measurement field | Meaning / device units |
| --- | --- |
| `1.1`, `1.2` | Import/export power, kW; one-minute averages on the aggregated stream |
| `2`, `3`, `4` | L1/L2/L3 messages: `1` import kW, `2` export kW, `3` V, `4` A; empty when unavailable |
| `10.1`, `10.2` | Import T1/T2 energy, kWh, encoded as doubles |
| `10.3`, `10.4` | Export T1/T2 energy, kWh, encoded as doubles; inferred mapping |
| `10.5` | Active tariff |
| `11.1`, `11.2`, `11.3.1` | Average demand kW, peak demand kW, peak Unix timestamp in seconds |

About one live datagram in a hundred in the capture was **146 bytes instead of
151** because average demand (measurement field `11.1`) was exactly 0 at the
start of a quarter hour. Proto3 omits that zero float, saving its one-byte tag
and four-byte value. Decode the missing scalar as 0. This is normal encoding,
not a truncated packet or a firmware bug; validate the declared payload length
and CRC rather than requiring 151 bytes.

Verified against a simultaneous `/api/p1data` read: import power, average demand, voltage, current and T1 energy all matched. **Unverified:** export power and export energy (field numbers inferred from the symmetric import layout, since this installation has no export) and L2/L3.

## mDNS

The `_http._tcp` service has no TXT record; hardware version is not shown on
the HTML status page. The product page documents only MQTT for Home Assistant;
the HTTP and UDP interfaces described here are unofficial.

The device advertises `<hostname>.local` (`voltbridge-` + the last three MAC bytes) as `_http._tcp` and `_mygrid._tcp` on port 80. The `_mygrid._tcp` TXT record carries `model=Voltbridge P1 Meter`, `manufacturer=MyGrid`, `device_id` (the MAC without separators), `api_path=/api/p1data`, `firmware_version` and `hardware_version=P1Wifi-rev1`.

<span id="telegram"></span>

## DSMR telegram parsing and checksum

Preserve the original line endings when checking a telegram. Compute the same
CRC-16/ARC described above over the bytes from the opening `/` through the `!`
inclusive, including the intervening CR/LF bytes. Compare it with the **four
hexadecimal characters after `!`**, not with binary big-endian header bytes as
in UDP. The library uses Latin-1 to preserve those bytes, exposes `crc_valid`,
and retains parsed unknown OBIS objects in `Telegram.objects`. Parsing a
telegram alone does not imply its CRC is valid; inspect `crc_valid`.

OBIS lines contain a code followed by parenthesized values, with quantities
written as `value*unit`. Repeated objects are retained. Timestamps have a
summer/winter suffix and are converted to UTC by the library. M-Bus channels
must be identified by their device type, not assumed to be gas on a fixed channel.

`/api/telegram` returns the meter's telegram as received (Belgian Fluvius, DSMR 5 style). The library decodes it in `mygrid_voltbridge.telegram`. Relevant objects seen on the test meter:

| OBIS | Meaning |
| --- | --- |
| `0-0:1.0.0` | Meter timestamp, `YYMMDDhhmmssS` (`S` summer/CEST, `W` winter/CET) |
| `1-0:1.4.0` | Current quarter-hour average demand, kW. This is the JSON `avgDemand`; checked against `/api/p1data` read at the same time |
| `1-0:1.6.0` | Monthly peak so far: timestamp and kW (JSON `peakDemand` / `peakDemandAt`) |
| `0-0:98.1.0` | Peak history: count, then (month end)(peak time)(kW) triples |
| `0-0:96.3.10` | Breaker state: 0 open, 1 closed, 2 ready for reconnection |
| `0-0:17.0.0` | Limiter threshold, kW (`999.9` means none; the library returns `None`) |
| `1-0:31.4.0` | Fuse threshold, A (`999` means none; the library returns `None`) |
| header `/FLU5\<model>` | Manufacturer code (`FLU`) and model (`253770234_A` on the test meter); `Telegram.meter_manufacturer` and `meter_model` |
| `0-0:96.1.4` | Version info; the first two digits (`50`) match the DSMR version another P1 reader reports for this meter. Used for `Telegram.dsmr_version` when `1-3:0.2.8` is absent (inferred) |
| `1-0:21.7.0` / `41.7.0` / `61.7.0` | Instantaneous import power on L1 / L2 / L3, kW |
| `1-0:22.7.0` / `42.7.0` / `62.7.0` | Instantaneous export power on L1 / L2 / L3, kW |
| `1-0:32.7.0` / `52.7.0` / `72.7.0` | Voltage on L1 / L2 / L3, V |
| `1-0:31.7.0` / `51.7.0` / `71.7.0` | Current on L1 / L2 / L3, A |
| `0-n:24.1.0` | M-Bus device type on channel n: 3 gas, 7 water |
| `0-n:24.2.x` | That device's last reading: timestamp and value with unit (m³) |
| `0-n:96.1.1` | Equipment id, hex-encoded ASCII |

Per-phase codes are the standard DSMR ones, decoded into `Telegram.phases`. L1 matched the JSON `*L1` fields exactly; L2 and L3 follow the standard's scheme and are unverified on hardware (the test meter is single-phase).

The test installation has a gas meter on channel 1 and a **water meter on channel 2**, which only the telegram reveals. Gas appears in both the JSON API and the telegram.

The undecoded UDP field `10.12` is `{1, 999.9, 999.0}` in every capture, which matches breaker state `1`, limiter `999.9 kW` and fuse `999 A` from this telegram (inferred from the values only).

## Status

`GET /api/p1data?d=1` returns a lighter document: one-minute averages, energy totals, the Wi-Fi signal (`rssi`, dBm), whether the last telegram is fresh (`p1Fresh`) and packet statistics (`packetsTotal`, `packetsValid`, `packetsError`, `packetsErrPerc`, present when `hasStats` is true). The library exposes the last three groups as `P1Status` (`async_get_status()`). The UDP live stream carries the same signal strength (field `5.1`).

## MQTT

MyGrid's supported local MQTT Discovery interface is configured in the MyGrid
app under **Features → Home Assistant**; see
[MyGrid's MQTT integration](https://github.com/MyGrid-Energy/mygrid-homeassistant-mqtt).
The community VoltBridge integration uses HTTP and UDP and does not need MQTT
or a broker. A closed port 1883 on the captured device does not mean it cannot
publish to an external MQTT broker.

## Sources and scope

This library owns the API reference, consolidated with the expanded documentation
previously maintained in the hub. The implementation provides
additional detail: [HTTP models](../src/mygrid_voltbridge/models.py),
[telegram parser](../src/mygrid_voltbridge/telegram.py),
[UDP decoder](../src/mygrid_voltbridge/udp.py)
and [discovery parser](../src/mygrid_voltbridge/discovery.py).
The field map is reverse-engineered, not a complete protobuf schema. In
particular, the breaker/limiter UDP mapping above remains an inference; do not
use it as a verified control contract.
