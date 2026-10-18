"""A cloud client made for the event loop that uses it.

Google's async (gRPC aio) clients bind to the loop that is current when they are created. Our
services build their signer and screener in `create_app()`, at import time, before uvicorn's loop
exists, so a client made there fails on first use ("attached to a different loop"). `LoopBound`
makes the client on first use and again if the running loop changes (tests run one per test).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class LoopBound[T]:
    def __init__(self, factory: Callable[[], T]) -> None:
        self._factory = factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._value: T | None = None

    def get(self) -> T:
        loop = asyncio.get_running_loop()
        if self._value is None or self._loop is not loop:
            self._value, self._loop = self._factory(), loop
        return self._value
