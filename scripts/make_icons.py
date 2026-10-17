"""Render the app icons (SVG → PNG sizes browsers need to offer "Install app").

uv run python scripts/make_icons.py      # writes web/public/icons/*.png from the SVGs below
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parents[1] / "web" / "public" / "icons"

# A "V" (VendorLink) with a leaf: teal for the vendor app, dark slate for the retailer console.
MARK = (
    '<path d="M150 170 L256 370 L362 170" fill="none" stroke="#fff" stroke-width="52" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<path d="M318 118c42-10 76-2 92 14-4 40-34 72-86 74-12-32-14-62-6-88z" fill="#99f6e4"/>'
)


def svg(background: str, *, maskable: bool = False) -> str:
    # maskable icons get cropped to a circle by some launchers: full bleed, mark kept to the centre
    shape = (
        f'<rect width="512" height="512" fill="{background}"/>'
        if maskable
        else f'<rect width="512" height="512" rx="112" fill="{background}"/>'
    )
    mark = f'<g transform="translate(77 77) scale(0.7)">{MARK}</g>' if maskable else MARK
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{shape}{mark}</svg>'


ICONS = {"vendor": "#0f766e", "console": "#1e293b"}


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(channel="chrome")
        except Exception:
            browser = await pw.chromium.launch()
        for name, colour in ICONS.items():
            (OUT / f"{name}.svg").write_text(svg(colour) + "\n")
            variants = {f"{name}-{n}": (svg(colour), n) for n in (192, 512)}
            variants[f"{name}-maskable-512"] = (svg(colour, maskable=True), 512)
            for file, (source, size) in variants.items():
                page = await browser.new_page(viewport={"width": size, "height": size})
                await page.set_content(
                    "<style>html,body{margin:0;background:transparent}svg{display:block;"
                    f"width:{size}px;height:{size}px}}</style>{source}"
                )
                await page.screenshot(path=OUT / f"{file}.png", omit_background=True)
                await page.close()
        await browser.close()
    print(f"icons written to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
