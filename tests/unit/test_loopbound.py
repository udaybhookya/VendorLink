from __future__ import annotations

import asyncio

from vl_core.loopbound import LoopBound


def test_one_client_per_running_loop() -> None:
    """Built at import time (no loop), used under uvicorn's loop: the client is made there."""
    made: list[asyncio.AbstractEventLoop] = []

    def factory() -> asyncio.AbstractEventLoop:
        made.append(asyncio.get_running_loop())
        return made[-1]

    bound = LoopBound(factory)

    async def use() -> bool:
        return bound.get() is asyncio.get_running_loop() and bound.get() is bound.get()

    assert asyncio.run(use())
    assert asyncio.run(use())
    assert len(made) == 2 and made[0] is not made[1]
