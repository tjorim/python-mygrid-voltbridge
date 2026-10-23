"""Errors raised by the VoltBridge client."""


class VoltBridgeError(Exception):
    """Base class for all VoltBridge errors."""


class VoltBridgeConnectionError(VoltBridgeError):
    """The device could not be reached, timed out or kept answering HTTP 5xx.

    Raised only after every retry; the request may succeed later.
    """


class VoltBridgeResponseError(VoltBridgeError):
    """The device answered with an unusable status or body.

    ``status`` is the HTTP status when the failure is tied to one.
    """

    def __init__(self, message: str = "Invalid VoltBridge response", *, status: int | None = None) -> None:
        """Create the error with an optional HTTP ``status``."""
        super().__init__(message)
        self.status = status


class VoltBridgeNoDataError(VoltBridgeError):
    """The device is up but has not received a P1 telegram yet."""


class VoltBridgeUnsupportedError(VoltBridgeError):
    """The firmware does not provide this endpoint (HTTP 404)."""
