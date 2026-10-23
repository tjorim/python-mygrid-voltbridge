"""Identify a VoltBridge from its mDNS record.

The device advertises ``_mygrid._tcp`` with TXT properties such as
``model=Voltbridge P1 Meter`` and ``device_id=907069D02D80`` (its MAC address).
Resolving the service is left to the caller (for example Home Assistant's
zeroconf integration), so this module needs no extra dependency.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import re

from .exceptions import VoltBridgeResponseError

SERVICE_TYPE = "_mygrid._tcp.local."
_DEVICE_ID = re.compile(r"[0-9A-Fa-f]{12}")


@dataclass(frozen=True)
class VoltBridgeInfo:
    """Identity advertised over mDNS."""

    device_id: str
    model: str | None
    firmware_version: str | None
    hardware_version: str | None
    api_path: str | None

    @property
    def mac_address(self) -> str:
        """The device id formatted as a lower-case MAC address."""
        return ":".join(self.device_id[i : i + 2] for i in range(0, 12, 2)).lower()

    @classmethod
    def from_properties(cls, properties: Mapping[str | bytes, str | bytes | None]) -> "VoltBridgeInfo":
        """Build from mDNS TXT properties; raises if this is not a VoltBridge."""
        text = {_str(key): _str(value) for key, value in properties.items()}
        device_id = text.get("device_id") or ""
        model = text.get("model")
        if text.get("manufacturer") != "MyGrid" or not _DEVICE_ID.fullmatch(device_id):
            msg = "not a MyGrid device advertisement"
            raise VoltBridgeResponseError(msg)
        if model is None or "voltbridge" not in model.lower():
            msg = f"not a VoltBridge: {model!r}"
            raise VoltBridgeResponseError(msg)
        return cls(
            device_id=device_id.upper(),
            model=model,
            firmware_version=text.get("firmware_version"),
            hardware_version=text.get("hardware_version"),
            api_path=text.get("api_path"),
        )


def _str(value: str | bytes | None) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
