"""Immutable readings in watts, watt-hours, volts and amperes.

The device reports power in kW and energy in kWh; both are converted to
integer W and Wh so values compare and sum exactly.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from .exceptions import VoltBridgeNoDataError, VoltBridgeResponseError


def _number(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{key} is not a number: {value!r}"
        raise VoltBridgeResponseError(msg)
    return float(value)


def _milli(payload: Mapping[str, Any], key: str) -> int | None:
    """Convert a kW/kWh field to W/Wh."""
    value = _number(payload, key)
    return None if value is None else round(value * 1000)


def _require_data(payload: object) -> Mapping[str, Any]:
    if not isinstance(payload, dict):
        msg = "response is not a JSON object"
        raise VoltBridgeResponseError(msg)
    if not payload.get("hasData"):
        msg = "no P1 telegram received yet"
        raise VoltBridgeNoDataError(msg)
    return cast("Mapping[str, Any]", payload)


@dataclass(frozen=True)
class PowerReading:
    """Instantaneous power from ``/api/power``; positive net power is import."""

    net_power: int
    import_power: int
    export_power: int
    tariff: int | None

    @classmethod
    def from_payload(cls, payload: object) -> "PowerReading":
        """Build from a ``/api/power`` payload."""
        data = _require_data(payload)
        net = _milli(data, "netPowerW")
        imported = _milli(data, "importPowerW")
        exported = _milli(data, "exportPowerW")
        if net is None or imported is None or exported is None:
            msg = "power response is missing fields"
            raise VoltBridgeResponseError(msg)
        tariff = _number(data, "tariff")
        return cls(
            net_power=net,
            import_power=imported,
            export_power=exported,
            tariff=None if tariff is None else int(tariff),
        )


@dataclass(frozen=True)
class PhaseData:
    """Per-phase values; any field the meter does not report is ``None``."""

    import_power: int | None
    export_power: int | None
    voltage: float | None
    current: float | None


@dataclass(frozen=True)
class P1Data:
    """A full P1 snapshot from ``/api/p1data``; unreported fields are ``None``."""

    meter_serial: str | None
    meter_version: str | None
    import_power: int | None
    export_power: int | None
    net_power: int | None
    avg_import_1min: int | None
    avg_export_1min: int | None
    avg_demand: int | None
    peak_demand: int | None
    peak_demand_at: datetime | None
    tariff: int | None
    energy_import_t1: int | None
    energy_import_t2: int | None
    energy_export_t1: int | None
    energy_export_t2: int | None
    gas: float | None
    phases: Mapping[int, PhaseData] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: object) -> "P1Data":
        """Build from a ``/api/p1data`` payload."""
        data = _require_data(payload)
        tariff = _number(data, "tariff")
        return cls(
            meter_serial=_text(data, "meterSerial"),
            meter_version=_text(data, "meterVersion"),
            import_power=_milli(data, "importPower"),
            export_power=_milli(data, "exportPower"),
            net_power=_milli(data, "activePowerNet"),
            avg_import_1min=_milli(data, "avgImport1min"),
            avg_export_1min=_milli(data, "avgExport1min"),
            avg_demand=_milli(data, "avgDemand"),
            peak_demand=_milli(data, "peakDemand"),
            peak_demand_at=_timestamp(data, "peakDemandAt"),
            tariff=None if tariff is None else int(tariff),
            energy_import_t1=_milli(data, "energyImportT1"),
            energy_import_t2=_milli(data, "energyImportT2"),
            energy_export_t1=_milli(data, "energyExportT1"),
            energy_export_t2=_milli(data, "energyExportT2"),
            gas=_number(data, "gasM3"),
            phases=_phases(data),
        )


def _text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return None if value is None else str(value)


def _timestamp(payload: Mapping[str, Any], key: str) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as err:
        msg = f"{key} is not a timestamp: {value!r}"
        raise VoltBridgeResponseError(msg) from err


def _phases(payload: Mapping[str, Any]) -> dict[int, PhaseData]:
    """Collect ``<field>L<n>`` keys. Only L1 is verified on real hardware."""
    phases: dict[int, PhaseData] = {}
    for number in (1, 2, 3):
        suffix = f"L{number}"
        keys = ("importPower", "exportPower", "voltage", "current")
        if not any(f"{key}{suffix}" in payload for key in keys):
            continue
        phases[number] = PhaseData(
            import_power=_milli(payload, f"importPower{suffix}"),
            export_power=_milli(payload, f"exportPower{suffix}"),
            voltage=_number(payload, f"voltage{suffix}"),
            current=_number(payload, f"current{suffix}"),
        )
    return phases
