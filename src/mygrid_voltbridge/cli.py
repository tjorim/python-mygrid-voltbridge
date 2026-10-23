"""Read-only command line helper: ``mygrid-voltbridge <host>``."""

import argparse
import asyncio
import contextlib
import dataclasses
from datetime import datetime
import json
from typing import Any

import aiohttp

from .client import HTTP_PORT, VoltBridgeClient
from .exceptions import VoltBridgeError
from .udp import P1_PORT, P1Broadcast, async_listen


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value))


async def _run(host: str, port: int, *, power_only: bool) -> str:
    async with aiohttp.ClientSession() as session:
        client = VoltBridgeClient(host, session, port=port)
        reading = await (client.async_get_power() if power_only else client.async_get_p1_data())
    return json.dumps(dataclasses.asdict(reading), default=_default, indent=2)


async def _watch(host: str, port: int) -> None:
    queue: asyncio.Queue[P1Broadcast] = asyncio.Queue()
    transport = await async_listen(queue.put_nowait, host=host, port=port)
    try:
        while True:
            broadcast = await queue.get()
            print(  # noqa: T201 - the CLI exists to print
                json.dumps(dataclasses.asdict(broadcast), default=_default), flush=True
            )
    finally:
        transport.close()


def main(argv: list[str] | None = None) -> int:
    """Run the command line helper and return the exit code."""
    parser = argparse.ArgumentParser(description="Read a MyGrid VoltBridge (read-only).")
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=HTTP_PORT)
    parser.add_argument("--watch", action="store_true", help="stream UDP broadcasts as JSON lines")
    parser.add_argument("--power", action="store_true", help="only instantaneous power")
    args = parser.parse_args(argv)
    try:
        if args.watch:
            with contextlib.suppress(KeyboardInterrupt):
                asyncio.run(_watch(args.host, args.port if args.port != HTTP_PORT else P1_PORT))
            return 0
        print(asyncio.run(_run(args.host, args.port, power_only=args.power)))  # noqa: T201
    except BrokenPipeError:  # e.g. piped into head
        return 0
    except VoltBridgeError as err:
        print(f"error: {err}")  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
