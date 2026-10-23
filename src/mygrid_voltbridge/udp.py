"""Decoder and listener for the VoltBridge's UDP broadcasts (push updates).

Every datagram is ``P1`` + version + stream + flags + payload length + CRC-16
(ARC, big endian) + a protobuf payload. Stream 0 (port 49491) carries the live
telegram about once a second; stream 1 (port 49492) carries one-minute averages
less often. The schema was reverse-engineered, so it is decoded by hand against
a fixed field map instead of with generated protobuf code.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
import logging
import struct
from typing import Any, override

from .exceptions import VoltBridgeResponseError
from .models import P1Data, PhaseData

_LOGGER = logging.getLogger(__name__)

P1_PORT = 49491
AGGREGATED_PORT = 49492
_MAGIC = b"P1"
_HEADER = 8
_MAX_VARINT_SHIFT = 63
_LENGTH_DELIMITED = 2  # protobuf wire type
_PEAK_FIELD = 2  # field 2 of the demand message

_Fields = dict[int, list[Any]]


class BroadcastStream(IntEnum):
    """The stream a datagram belongs to (header byte 3)."""

    P1 = 0
    AGGREGATED = 1


@dataclass(frozen=True)
class P1Broadcast:
    """One decoded datagram.

    ``data`` uses the same units as the HTTP client. On the aggregated stream
    the import/export fields hold one-minute averages in ``avg_*`` and the
    instantaneous fields are ``None``.
    """

    stream: BroadcastStream
    timestamp: datetime
    rssi: int | None
    country: str | None
    software_version: str | None
    data: P1Data


def _crc16_arc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf) or shift > _MAX_VARINT_SHIFT:
            msg = "truncated varint"
            raise VoltBridgeResponseError(msg)
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def _read(buf: bytes) -> _Fields:
    """Split a protobuf message into ``{field: [value, ...]}`` (wire types 0, 1, 2, 5)."""
    fields: _Fields = {}
    pos = 0
    value: Any
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if pos + size > len(buf):
                msg = "truncated fixed-width field"
                raise VoltBridgeResponseError(msg)
            fmt = "<d" if wire == 1 else "<f"
            (value,) = struct.unpack_from(fmt, buf, pos)
            pos += size
        elif wire == _LENGTH_DELIMITED:
            size, pos = _varint(buf, pos)
            if pos + size > len(buf):
                msg = "truncated length-delimited field"
                raise VoltBridgeResponseError(msg)
            value = buf[pos : pos + size]
            pos += size
        else:
            msg = f"unsupported wire type {wire}"
            raise VoltBridgeResponseError(msg)
        fields.setdefault(number, []).append(value)
    return fields


def _msg(fields: _Fields, number: int) -> _Fields | None:
    values = fields.get(number)
    if values is None or not isinstance(values[-1], bytes):
        return None
    return _read(values[-1])


def _num(fields: _Fields | None, number: int) -> float:
    """Proto3 omits zero, so a missing scalar inside a present message is 0."""
    if fields is None:
        return 0.0
    value = fields.get(number, [0.0])[-1]
    if isinstance(value, bytes):
        msg = f"field {number} is not a number"
        raise VoltBridgeResponseError(msg)
    return float(value)


def _text(fields: _Fields | None, number: int) -> str | None:
    values = ({} if fields is None else fields).get(number)
    if values is None or not isinstance(values[-1], bytes):
        return None
    return values[-1].decode("utf-8", errors="replace") or None


def _milli(value: float) -> int:
    return round(value * 1000)


def _phase(fields: _Fields | None) -> PhaseData | None:
    if fields is None or len(fields) == 0:
        return None
    return PhaseData(
        import_power=_milli(_num(fields, 1)),
        export_power=_milli(_num(fields, 2)),
        voltage=round(_num(fields, 3), 2),
        current=round(_num(fields, 4), 2),
    )


def parse_datagram(datagram: bytes) -> P1Broadcast:
    """Decode one UDP datagram, raising ``VoltBridgeResponseError`` if invalid."""
    if len(datagram) < _HEADER or datagram[:2] != _MAGIC or datagram[2] != 1:
        msg = "not a VoltBridge datagram"
        raise VoltBridgeResponseError(msg)
    try:
        stream = BroadcastStream(datagram[3])
    except ValueError as err:
        msg = f"unknown stream {datagram[3]}"
        raise VoltBridgeResponseError(msg) from err
    payload = datagram[_HEADER:]
    if datagram[5] != len(payload):
        msg = "payload length mismatch"
        raise VoltBridgeResponseError(msg)
    if int.from_bytes(datagram[6:8], "big") != _crc16_arc(payload):
        msg = "checksum mismatch"
        raise VoltBridgeResponseError(msg)

    envelope = _read(payload)
    seconds = _num(_msg(envelope, 1), 1)
    if not seconds:
        msg = "missing timestamp"
        raise VoltBridgeResponseError(msg)
    rssi_raw = _msg(envelope, 5)
    rssi = None
    if rssi_raw is not None:
        raw = rssi_raw.get(1, [0])[-1]
        if not isinstance(raw, int):
            msg = "rssi is not an integer"
            raise VoltBridgeResponseError(msg)
        rssi = raw - (1 << 64) if raw >= 1 << 63 else raw
    meter = _msg(envelope, 6)
    body = _msg(envelope, 10 if stream is BroadcastStream.P1 else 11) or {}

    totals = _msg(body, 10)
    demand = _msg(body, 11)
    peak_at = _msg(demand, 3) if demand is not None else None
    power = _msg(body, 1)
    imported, exported = _milli(_num(power, 1)), _milli(_num(power, 2))
    live = stream is BroadcastStream.P1
    phases = {n: p for n in (1, 2, 3) if live and (p := _phase(_msg(body, n + 1)))}

    data = P1Data(
        meter_serial=_text(meter, 1),
        meter_version=_text(meter, 2),
        import_power=imported if live else None,
        export_power=exported if live else None,
        net_power=imported - exported if live else None,
        avg_import_1min=None if live else imported,
        avg_export_1min=None if live else exported,
        avg_demand=_milli(_num(demand, 1)) if demand is not None else None,
        peak_demand=_milli(_num(demand, 2)) if demand is not None and _PEAK_FIELD in demand else None,
        peak_demand_at=(datetime.fromtimestamp(int(_num(peak_at, 1)), UTC) if peak_at is not None else None),
        tariff=int(_num(totals, 5)) if totals is not None else None,
        energy_import_t1=_milli(_num(totals, 1)) if totals is not None else None,
        energy_import_t2=_milli(_num(totals, 2)) if totals is not None else None,
        energy_export_t1=_milli(_num(totals, 3)) if totals is not None else None,
        energy_export_t2=_milli(_num(totals, 4)) if totals is not None else None,
        gas=None,
        phases=phases,
    )
    return P1Broadcast(
        stream=stream,
        timestamp=datetime.fromtimestamp(int(seconds), UTC),
        rssi=rssi,
        country=_text(envelope, 4),
        software_version=_text(envelope, 2),
        data=data,
    )


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, callback: Callable[[P1Broadcast], None], host: str | None) -> None:
        self._callback = callback
        self._host = host

    @override
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._host is not None and addr[0] != self._host:
            return
        try:
            broadcast = parse_datagram(data)
        except VoltBridgeResponseError as err:
            _LOGGER.debug("Dropping datagram from %s: %s", addr[0], err)
            return
        self._callback(broadcast)


async def async_listen(
    callback: Callable[[P1Broadcast], None],
    *,
    host: str | None = None,
    port: int = P1_PORT,
    bind: str = "0.0.0.0",  # noqa: S104 - broadcasts arrive on any interface
) -> asyncio.DatagramTransport:
    """Listen for broadcasts; ``host`` filters by the device's IP.

    Call ``close()`` on the returned transport to stop. Invalid datagrams are
    dropped. ``callback`` runs in the event loop and must not block.
    """
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Protocol(callback, host), local_addr=(bind, port), reuse_port=True
    )
    return transport


__all__ = [
    "AGGREGATED_PORT",
    "P1_PORT",
    "BroadcastStream",
    "P1Broadcast",
    "async_listen",
    "parse_datagram",
]
