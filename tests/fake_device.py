"""A fake VoltBridge HTTP server for the offline tests."""

from aiohttp.test_utils import TestServer


class FakeDevice(TestServer):
    """A test server that remembers reboot requests."""

    rebooted: list[str]
    requests: list[str]
