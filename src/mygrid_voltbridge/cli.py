"""Read-only command line helper: ``mygrid-voltbridge <host>``."""

import argparse
import asyncio
import dataclasses
import json
from datetime import datetime
from typing import Any

import aiohttp

from .client import VoltBridgeClient
from .exceptions import VoltBridgeError


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value))


async def _run(host: str, port: int, power_only: bool) -> str:
    async with aiohttp.ClientSession() as session:
        client = VoltBridgeClient(host, session, port=port)
        reading = await (
            client.async_get_power() if power_only else client.async_get_p1_data()
        )
    return json.dumps(dataclasses.asdict(reading), default=_default, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read a MyGrid VoltBridge (read-only)."
    )
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--power", action="store_true", help="only instantaneous power")
    args = parser.parse_args(argv)
    try:
        print(asyncio.run(_run(args.host, args.port, args.power)))
    except VoltBridgeError as err:
        print(f"error: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
