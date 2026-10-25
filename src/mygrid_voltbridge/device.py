"""The adapter's own identity and health, scraped from its HTML status page.

The device has no JSON endpoint for this: MAC address, firmware, uptime and
memory exist only as rows of the ``/`` page's System table, so the page is
parsed by row label. Rows that are missing or in an unexpected format become
``None``; a page without any of them raises ``VoltBridgeResponseError``.
"""

from dataclasses import dataclass
from datetime import timedelta
import html
import re

from .exceptions import VoltBridgeResponseError

_ROW = re.compile(r"<tr\b[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_MAC = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_HOSTNAME = re.compile(r"\(([^)\s]+)\)")
_FIRMWARE = re.compile(r"^(\S+),\s*(\d+),\s*(\d+)$")
_UPTIME = re.compile(r"^(?:(\d+)\s*d(?:ays?)?,?\s*)?(\d+):(\d{2}):(\d{2})$")
_BYTES = re.compile(r"^(\d+)\s*bytes?$")


@dataclass(frozen=True)
class DeviceInfo:
    """The VoltBridge adapter itself, not the meter behind it.

    ``build_info`` is the device's own string, for example
    ``app:build-15.6, idf:v6.0.1, date:202609272035``.
    """

    mac_address: str | None
    hostname: str | None
    firmware_type: str | None
    firmware_version: int | None
    firmware_variant: int | None
    build_info: str | None
    uptime: timedelta | None
    boot_reason: str | None
    last_reboot_reason: str | None
    free_heap: int | None
    min_free_heap: int | None
    usb_power_connected: bool | None
    p1_out_connected: bool | None

    @classmethod
    def from_html(cls, page: str) -> "DeviceInfo":
        """Build from the status page; raises if it has no device rows."""
        rows = {_clean(label): _clean(value) for label, value in _ROW.findall(page)}
        mac = rows.get("MAC address")
        firmware = _FIRMWARE.match(rows.get("Firmware type, version, variant", ""))
        has_mac = mac is not None and _MAC.match(mac) is not None
        if not has_mac and firmware is None:
            msg = "not a VoltBridge status page"
            raise VoltBridgeResponseError(msg)
        hostname = _HOSTNAME.search(rows.get("IP address", ""))
        return cls(
            mac_address=mac.lower() if mac is not None and has_mac else None,
            hostname=hostname.group(1) if hostname else None,
            firmware_type=firmware.group(1) if firmware else None,
            firmware_version=int(firmware.group(2)) if firmware else None,
            firmware_variant=int(firmware.group(3)) if firmware else None,
            build_info=rows.get("Firmware build info"),
            uptime=_uptime(rows.get("Uptime", "")),
            boot_reason=rows.get("Current boot reason"),
            last_reboot_reason=rows.get("Last reboot reason"),
            free_heap=_bytes(rows.get("Free heap", "")),
            min_free_heap=_bytes(rows.get("Min free heap", "")),
            usb_power_connected=_connected(rows.get("USB-C power", "")),
            p1_out_connected=_connected(rows.get("P1-OUT port", "")),
        )


def _clean(text: str) -> str:
    return html.unescape(_TAG.sub("", text)).strip()


def _uptime(text: str) -> timedelta | None:
    match = _UPTIME.match(text)
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _bytes(text: str) -> int | None:
    match = _BYTES.match(text)
    return int(match.group(1)) if match else None


def _connected(text: str) -> bool | None:
    return {"connected": True, "not connected": False}.get(text.lower())
