"""Parser for the raw DSMR/Fluvius P1 telegram served at ``/api/telegram``.

The JSON API only exposes the electricity values and gas. The telegram also
carries every M-Bus device (water, heat), the breaker and limiter state and
the monthly peak history. Unknown OBIS codes stay available in ``objects``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
import re

from .exceptions import VoltBridgeResponseError

_OBIS = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+)((?:\([^)]*\))*)$")
_GROUP = re.compile(r"\(([^)]*)\)")
_TIMESTAMP = re.compile(r"^(\d{12})([SW])$")
_VALUE = re.compile(r"^(-?\d+(?:\.\d+)?)\*(\S+)$")
_MBUS = re.compile(r"^0-(\d+):24\.(\d+)\.\d+$")

# DSMR M-Bus device type codes.
GAS = 3
HEAT = 4
WATER = 7


@dataclass(frozen=True)
class MBusChannel:
    """One device on the meter's M-Bus (``0-n:24.*``)."""

    channel: int
    device_type: int | None
    equipment_id: str | None
    reading_at: datetime | None
    reading: float | None
    unit: str | None
    valve_state: int | None

    @property
    def kind(self) -> str:
        """Return ``gas``, ``water``, ``heat`` or ``unknown``."""
        return {GAS: "gas", HEAT: "heat", WATER: "water"}.get(self.device_type or 0, "unknown")


@dataclass(frozen=True)
class MonthlyPeak:
    """One entry of the monthly peak-demand history (OBIS ``0-0:98.1.0``)."""

    month_end: datetime
    peak_at: datetime
    power: int


@dataclass(frozen=True)
class Telegram:
    """A decoded telegram; power in W, current in A, volumes as reported."""

    header: str
    crc_valid: bool
    timestamp: datetime | None
    breaker_state: int | None
    limiter_threshold: int | None
    fuse_threshold: int | None
    avg_demand: int | None
    peak_demand: int | None
    peak_demand_at: datetime | None
    monthly_peaks: tuple[MonthlyPeak, ...]
    channels: Mapping[int, MBusChannel]
    objects: Mapping[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)

    def channel_of(self, kind: str) -> MBusChannel | None:
        """Return the first M-Bus device of a kind (``gas``, ``water``, ``heat``)."""
        return next((c for _, c in sorted(self.channels.items()) if c.kind == kind), None)


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _timestamp(text: str) -> datetime | None:
    """``YYMMDDhhmmssS|W``: local time with a summer/winter marker (CET/CEST)."""
    match = _TIMESTAMP.match(text)
    if not match:
        return None
    digits, season = match.groups()
    zone = timezone(timedelta(hours=2 if season == "S" else 1))
    try:
        return datetime(
            2000 + int(digits[0:2]),
            int(digits[2:4]),
            int(digits[4:6]),
            int(digits[6:8]),
            int(digits[8:10]),
            int(digits[10:12]),
            tzinfo=zone,
        ).astimezone(UTC)
    except ValueError:
        return None


def _quantity(text: str, unit: str) -> float | None:
    """Return a value in the given unit, or ``None`` if absent or malformed."""
    match = _VALUE.match(text)
    return float(match.group(1)) if match and match.group(2) == unit else None


def _kilo(text: str, unit: str) -> int | None:
    value = _quantity(text, unit)
    return None if value is None else round(value * 1000)


def _first(objects: Mapping[str, tuple[tuple[str, ...], ...]], code: str) -> tuple[str, ...]:
    return objects.get(code, ((),))[0]


def _int(text: str | None) -> int | None:
    return int(text) if text is not None and text.lstrip("-").isdigit() else None


def _hex_text(text: str) -> str:
    """Equipment ids are hex-encoded ASCII; fall back to the raw hex."""
    try:
        decoded = bytes.fromhex(text).decode("ascii")
    except ValueError:
        return text
    return decoded if decoded.isprintable() else text


def _peaks(groups: tuple[str, ...]) -> tuple[MonthlyPeak, ...]:
    triples = groups[3:]
    peaks = []
    for index in range(0, len(triples) - 2, 3):
        month_end = _timestamp(triples[index])
        peak_at = _timestamp(triples[index + 1])
        power = _kilo(triples[index + 2], "kW")
        if month_end and peak_at and power is not None:
            peaks.append(MonthlyPeak(month_end, peak_at, power))
    return tuple(peaks)


def _channels(
    objects: Mapping[str, tuple[tuple[str, ...], ...]],
) -> dict[int, MBusChannel]:
    numbers = {int(m.group(1)) for code in objects if (m := _MBUS.match(code))}
    channels = {}
    for number in sorted(numbers):
        reading_groups: tuple[str, ...] = ()
        for code, values in objects.items():
            match = _MBUS.match(code)
            if match and int(match.group(1)) == number and match.group(2) == "2":
                reading_groups = values[0]
        reading_at = _timestamp(reading_groups[0]) if reading_groups else None
        quantity = reading_groups[1] if len(reading_groups) > 1 else ""
        match = _VALUE.match(quantity)
        type_group = _first(objects, f"0-{number}:24.1.0")
        id_group = _first(objects, f"0-{number}:96.1.1")
        valve_group = _first(objects, f"0-{number}:24.4.0")
        channels[number] = MBusChannel(
            channel=number,
            device_type=_int(type_group[0]) if type_group else None,
            equipment_id=_hex_text(id_group[0]) if id_group and id_group[0] else None,
            reading_at=reading_at,
            reading=float(match.group(1)) if match else None,
            unit=match.group(2) if match else None,
            valve_state=_int(valve_group[0]) if valve_group else None,
        )
    return channels


def parse_telegram(text: str) -> Telegram:
    """Decode a telegram; raises ``VoltBridgeResponseError`` if it is not one."""
    start, bang = text.find("/"), text.find("!")
    if start < 0 or bang < start:
        msg = "not a P1 telegram"
        raise VoltBridgeResponseError(msg)
    body = text[start : bang + 1]
    expected = text[bang + 1 : bang + 5]
    crc_valid = f"{_crc16(body.encode('latin-1')):04X}" == expected.upper()

    lines = body.splitlines()
    header = lines[0].strip()
    objects: dict[str, tuple[tuple[str, ...], ...]] = {}
    for line in lines[1:]:
        match = _OBIS.match(line.strip())
        if match:
            code, rest = match.groups()
            groups = tuple(_GROUP.findall(rest))
            objects[code] = (*objects.get(code, ()), groups)

    timestamp = _first(objects, "0-0:1.0.0")
    peak = _first(objects, "1-0:1.6.0")
    limiter = _first(objects, "0-0:17.0.0")
    fuse = _first(objects, "1-0:31.4.0")
    demand = _first(objects, "1-0:1.4.0")
    history = _first(objects, "0-0:98.1.0")
    return Telegram(
        header=header,
        crc_valid=crc_valid,
        timestamp=_timestamp(timestamp[0]) if timestamp else None,
        breaker_state=_int(_first(objects, "0-0:96.3.10")[0]) if "0-0:96.3.10" in objects else None,
        limiter_threshold=_kilo(limiter[0], "kW") if limiter else None,
        fuse_threshold=round(q) if fuse and (q := _quantity(fuse[0], "A")) is not None else None,
        avg_demand=_kilo(demand[0], "kW") if demand else None,
        peak_demand=_kilo(peak[1], "kW") if len(peak) > 1 else None,
        peak_demand_at=_timestamp(peak[0]) if peak else None,
        monthly_peaks=_peaks(history),
        channels=_channels(objects),
        objects=objects,
    )
