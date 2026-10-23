"""Errors raised by the VoltBridge client."""


class VoltBridgeError(Exception):
    """Base class for all VoltBridge errors."""


class VoltBridgeConnectionError(VoltBridgeError):
    """The device could not be reached or did not answer in time."""


class VoltBridgeResponseError(VoltBridgeError):
    """The device answered with an unusable status or body."""


class VoltBridgeNoDataError(VoltBridgeError):
    """The device is up but has not received a P1 telegram yet."""
