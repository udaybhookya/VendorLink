"""Run the demo product photos through the whole listing flow and compare with the test plan.

    uv run python scripts/listing_photos_check.py            # every photo in seed/photos/demo/
    uv run python scripts/listing_photos_check.py p1 p4      # only these
    uv run python scripts/listing_photos_check.py --live     # against `make dev` instead

In-process by default (the four services in this process, with the models from .env). Each photo
goes in with the same words; the script prints the flags the retailer's agents raised, whether her
Guardian approved on her behalf or asked her, and whether that matches the plan (docs/DEMO.md).
Uses real model calls: about five per photo.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PHOTOS = ROOT / "seed" / "photos" / "demo"
SAID = "Lavender candle, 250 gram jar, burns about 30 hours. 40 pieces at 220 rupees each."
# photo name prefix → (flags of which at least one must appear, should her Guardian approve alone)
PLAN: dict[str, tuple[set[str], bool]] = {
    "p1": (set(), True),
    "p2": ({"IMAGE_CLUTTERED_BACKGROUND"}, False),
    "p3": ({"IMAGE_TOO_DARK", "IMAGE_POOR_LIGHTING"}, False),
    "p4": ({"IMAGE_BLURRY"}, False),
    "p5": ({"IMAGE_PRODUCT_TOO_SMALL"}, False),
    "p6": ({"DEFECT_CRACKS", "DEFECT_OFF_CENTRE_WICK", "BELOW_MIN_GRADE"}, True),
    "p7": ({"UNSUPPORTED_CLAIM_SOY_WAX", "UNSUPPORTED_CLAIM_NATURAL"}, True),
    "p8": ({"NOT_A_PRODUCT_PHOTO"}, True),
}
SIZE_FLAG = "IMAGE_LOW_RESOLUTION"  # reported, but a property of the file, not of the scene


async def run(names: list[str], live: bool) -> int:
    from scripts._demo import MAYA, call, reset
    from vl_core.settings import get_settings

    if live:
        settings = get_settings()
    else:
        from scripts._stack import build_stack

        settings = build_stack(models=True)
    if not settings.flash_model:
        print("FLASH_MODEL is empty: the agents would run without a model. Set it in .env.")
        return 1
    image = settings.image_model or "none (retake tips)"
    print(f"models: flash={settings.flash_model} image={image}")
    await reset(settings, "A")
    photos = sorted(p for p in PHOTOS.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if names:
        photos = [p for p in photos if any(p.name.startswith(n) for n in names)]
    if not photos:
        print(f"no photos in {PHOTOS}")
        return 1
    matched = 0
    for photo in photos:
        key = photo.name[:2]
        view: dict[str, Any] = await call(
            settings.vendor_svc_url,
            "POST",
            f"/vendors/{MAYA}/listings/draft",
            json={
                "vendor_id": MAYA,
                "language": "en",
                "transcript": SAID,
                "photo_b64": base64.b64encode(photo.read_bytes()).decode(),
                "photo_mime": "image/png" if photo.suffix.lower() == ".png" else "image/jpeg",
            },
        )
        listing = view["result"]["listing"] or {}
        codes = sorted(f["code"] for f in view["result"]["flags"])
        approval = view.get("approval") or {}
        versions = [c for c in listing.get("images", []) if c["kind"] == "generated"]
        want, alone = PLAN.get(key, (set(), False))
        scene = [c for c in codes if c != SIZE_FLAG]
        photo_flags = any(c.startswith("IMAGE_") for c in scene)
        flags_ok = (not want and not photo_flags) or bool(want & set(codes))
        # an approval verdict is only expected when the photo itself is fine
        approve_ok = approval.get("auto_approve") == alone if not photo_flags else True
        ok = flags_ok and approve_ok
        matched += ok
        print(f"\n{'✓' if ok else '✗'} {photo.name}")
        print(f"  flags:    {', '.join(codes) or 'none'}")
        if want:
            print(f"  expected: one of {', '.join(sorted(want))}")
        print(
            "  guardian: "
            + (
                "approved on her behalf"
                if approval.get("auto_approve")
                else f"asked her ({', '.join(approval.get('reasons', [])) or 'needs info'})"
            )
        )
        if versions:
            rendered = sum(1 for c in versions if c.get("uri"))
            print(f"  photos:   {rendered} AI version(s) rendered, {len(versions)} prompt(s)")
        if listing.get("title"):
            print(f"  title:    {listing['title']}")
            sources = listing.get("sources", {})
            print(f"  sources:  {', '.join(f'{k}={v}' for k, v in sources.items())}")
        if SIZE_FLAG in codes:
            print(f"  note:     {SIZE_FLAG}: file under 600 px on one side; use a larger one")
    print(f"\n{matched}/{len(photos)} photos match the plan")
    return 0 if matched == len(photos) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("names", nargs="*", help="photo name prefixes, e.g. p1 p4")
    parser.add_argument("--live", action="store_true", help="use the running make dev stack")
    args = parser.parse_args()
    if not args.live:
        from vl_core.settings import get_settings

        get_settings()  # load .env into the environment before the in-process stack is built
    return asyncio.run(run(args.names, args.live))


if __name__ == "__main__":
    sys.exit(main())
