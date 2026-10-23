from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def routes() -> dict[str, tuple[int, str]]:
    """Mutable path -> (status, body) map the fake device serves."""
    return {
        "/api/power": (200, (FIXTURES / "power.json").read_text()),
        "/api/p1data": (200, (FIXTURES / "p1data.json").read_text()),
    }


@pytest.fixture
async def device(routes: dict[str, tuple[int, str]]) -> AsyncIterator[TestServer]:
    rebooted: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/reboot" and request.method == "POST":
            rebooted.append("x")
            return web.Response(text="ok")
        status, body = routes.get(request.path, (404, "Nothing matches the given URI"))
        return web.Response(status=status, text=body, content_type="application/json")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    server.rebooted = rebooted  # type: ignore[attr-defined]
    yield server
    await server.close()


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as client_session:
        yield client_session
