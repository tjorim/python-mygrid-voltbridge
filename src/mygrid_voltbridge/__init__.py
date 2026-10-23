"""Typed async client for the MyGrid VoltBridge P1 meter."""

from importlib.metadata import PackageNotFoundError, version
import logging

from .client import VoltBridgeClient
from .discovery import SERVICE_TYPE, VoltBridgeInfo
from .exceptions import (
    VoltBridgeConnectionError,
    VoltBridgeError,
    VoltBridgeNoDataError,
    VoltBridgeResponseError,
    VoltBridgeUnsupportedError,
)
from .models import P1Data, PhaseData, PowerReading
from .telegram import GAS, HEAT, WATER, MBusChannel, MonthlyPeak, Telegram, parse_telegram
from .udp import BroadcastStream, P1Broadcast, async_listen, parse_datagram

logging.getLogger(__name__).addHandler(logging.NullHandler())

try:
    __version__ = version("mygrid-voltbridge")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "GAS",
    "HEAT",
    "SERVICE_TYPE",
    "WATER",
    "BroadcastStream",
    "MBusChannel",
    "MonthlyPeak",
    "P1Broadcast",
    "P1Data",
    "PhaseData",
    "PowerReading",
    "Telegram",
    "VoltBridgeClient",
    "VoltBridgeConnectionError",
    "VoltBridgeError",
    "VoltBridgeInfo",
    "VoltBridgeNoDataError",
    "VoltBridgeResponseError",
    "VoltBridgeUnsupportedError",
    "__version__",
    "async_listen",
    "parse_datagram",
    "parse_telegram",
]
