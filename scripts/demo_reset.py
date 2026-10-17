"""Reset all running services and reseed (configs A or B), optionally into a demo scene.

uv run python scripts/demo_reset.py [--preset B]
uv run python scripts/demo_reset.py --scene waiting   # A: run paused on the buyer + Maya
uv run python scripts/demo_reset.py --scene sara      # B: Sara's label flagged (no P-IRT)
"""

from __future__ import annotations

import argparse
import asyncio

from scripts._demo import SCENES, reset
from vl_core.settings import Settings, get_settings


async def run(settings: Settings, preset: str, scene: str | None) -> None:
    await reset(settings, preset)
    print(f"reset done; active config {preset}")
    if scene:
        result = await SCENES[scene][1](settings)
        status = result.get("status") or result.get("result", {}).get("status", "")
        print(f"scene {scene!r} ready ({status})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", default=None, help="A or B (default: ACTIVE_CONFIG)")
    parser.add_argument("--scene", choices=sorted(SCENES), default=None)
    args = parser.parse_args()
    settings = get_settings()
    preset = args.preset or (SCENES[args.scene][0] if args.scene else settings.active_config)
    if args.scene and preset != SCENES[args.scene][0]:
        parser.error(f"scene {args.scene!r} runs on config {SCENES[args.scene][0]}")
    asyncio.run(run(settings, preset, args.scene))


if __name__ == "__main__":
    main()
