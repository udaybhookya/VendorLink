from scripts.e2e_order_loop import run
from vl_core.settings import Settings


async def test_order_loop_green_red_amber(stack: Settings) -> None:
    await run(stack)
