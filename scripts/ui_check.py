"""Every screen in a real (headless) browser against the running stack.

    make dev                                   # in another terminal
    uv run python scripts/ui_check.py [--web http://localhost:5173] [--headed]

Resets into the `waiting` demo scene so screens have data, then opens each vendor tab at phone
size and each console page. A screen fails on a JavaScript error, a 5xx response, a visible error
banner, an empty page, or (vendor app) horizontal scrolling. One interaction is driven end to end:
Maya accepts FreshCart's terms and the console shows it. Screenshots go to .vl_data/ui/.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, async_playwright

from scripts._demo import MAYA, SCENES, reset
from vl_core.settings import get_settings

VENDOR_TABS = ["talk", "deals", "products", "setup", "activity"]
LAPTOP_PAGES = ["deals", "products", "setup", "activity"]  # the assistant is docked beside each
CONSOLE_PAGES = [
    "today",
    "tasks",
    "studio",
    "suppliers",
    "queue",
    "orders",
    "observability",
    "trust",
    "outbox",
]
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1400, "height": 900}
SHOTS = Path(".vl_data/ui")


class Screen:
    """Problems seen while one page was open."""

    def __init__(self, page: Page, name: str) -> None:
        self.page, self.name = page, name
        self.problems: list[str] = []
        page.on("pageerror", lambda exc: self.problems.append(f"js error: {exc}"))
        page.on(
            "console",
            lambda msg: (
                self.problems.append(f"console: {msg.text[:160]}")
                if msg.type == "error" and "Failed to load resource" not in msg.text
                else None
            ),
        )
        page.on(
            "response",
            lambda r: (
                self.problems.append(f"{r.status} {r.request.method} {r.url}")
                if r.status >= 500
                else None
            ),
        )

    async def check(self, *, phone: bool) -> None:
        await self.page.wait_for_load_state("networkidle")
        await self.page.wait_for_timeout(300)
        for banner in await self.page.locator(".error").all():
            if await banner.is_visible():
                self.problems.append(f"error banner: {(await banner.inner_text())[:160]}")
        text = (await self.page.inner_text("body")).strip()
        if len(text) < 20:
            self.problems.append("page is (nearly) empty")
        overflow = await self.page.evaluate(
            "document.documentElement.scrollWidth - window.innerWidth"
        )
        if overflow > 1:
            self.problems.append(f"scrolls sideways by {overflow}px")
        if phone:
            rows = await self.page.evaluate(
                "new Set([...document.querySelectorAll('.tabbar button')]"
                ".map(b => b.offsetTop)).size"
            )
            if rows > 1:
                self.problems.append(f"tab bar wraps onto {rows} rows at phone width")
        await self.page.screenshot(path=SHOTS / f"{self.name}.png", full_page=True)


async def visit(browser: Browser, url: str, name: str, viewport: dict[str, int]) -> list[str]:
    page = await browser.new_page(viewport=viewport)  # type: ignore[arg-type]
    screen = Screen(page, name)
    await page.goto(url)
    await screen.check(phone=viewport is PHONE)
    await page.close()
    return screen.problems


async def switch_language(browser: Browser, web: str) -> list[str]:
    """English by default; the 🌐 picker switches her app to Telugu and back."""
    page = await browser.new_page(viewport=PHONE)  # type: ignore[arg-type]
    screen = Screen(page, "flow-language-te")
    await page.goto(f"{web}/vendor?vendor={MAYA}&tab=deals")
    await page.wait_for_load_state("networkidle")
    problems: list[str] = []
    if "Deals" not in await page.inner_text("nav.tabbar"):
        problems.append("language: the app does not open in English")
    await page.select_option(".lang select", "te")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(500)
    if "డీల్స్" not in await page.inner_text("nav.tabbar"):
        problems.append("language: switching to Telugu did not change the app")
    await screen.check(phone=True)
    await page.select_option(".lang select", "en")
    await page.wait_for_load_state("networkidle")
    await page.close()
    return problems + screen.problems


async def laptop_chat(browser: Browser, web: str) -> list[str]:
    """On a laptop: nav + page + the assistant always open; chatting there works beside Deals."""
    page = await browser.new_page(viewport=DESKTOP)  # type: ignore[arg-type]
    screen = Screen(page, "flow-laptop-chat")
    await page.goto(f"{web}/vendor?vendor={MAYA}&tab=deals")
    await page.wait_for_load_state("networkidle")
    problems: list[str] = []
    if not await page.locator(".vapp-nav").is_visible() or await page.locator(".tabbar").count():
        problems.append("laptop: expected the side navigation, not the phone tab bar")
    chat = page.locator(".vapp-chat")
    if not await chat.is_visible():
        problems.append("laptop: the assistant panel is not open beside the page")
    else:
        before = await chat.locator(".bubble.model").count()
        await chat.locator(".composer input:not([type=file])").fill("deals")
        await chat.locator(".composer input:not([type=file])").press("Enter")
        await page.wait_for_timeout(1500)
        if await chat.locator(".bubble.model").count() <= before:
            problems.append("laptop: the assistant did not answer")
    await screen.check(phone=False)
    await page.close()
    return problems + screen.problems


async def installable(browser: Browser, web: str) -> list[str]:
    """Both apps can be installed: each manifest loads and every icon it lists exists."""
    problems: list[str] = []
    context = await browser.new_context()
    for app in ("vendor", "console"):
        response = await context.request.get(f"{web}/{app}.webmanifest")
        if not response.ok:
            problems.append(f"{app}.webmanifest → {response.status}")
            continue
        manifest = await response.json()
        sizes = {icon.get("sizes") for icon in manifest.get("icons", [])}
        if not {"192x192", "512x512"} <= sizes:
            problems.append(f"{app}: install needs 192 and 512 px icons")
        for icon in manifest.get("icons", []):
            got = await context.request.get(f"{web}{icon['src']}")
            if not got.ok:
                problems.append(f"{app}: icon {icon['src']} → {got.status}")
    await context.close()
    return problems


async def accept_terms(browser: Browser, web: str) -> list[str]:
    """Maya accepts the proposed terms on her phone; the console's Suppliers page shows it."""
    page = await browser.new_page(viewport=PHONE)  # type: ignore[arg-type]
    screen = Screen(page, "flow-terms-accepted")
    await page.goto(f"{web}/vendor?vendor={MAYA}&tab=deals")
    await page.wait_for_load_state("networkidle")
    accept = page.get_by_role("button", name="Accept")
    if not await accept.count():
        return ["terms: no Accept button on Maya's Deals tab"]
    await accept.click()
    await page.wait_for_load_state("networkidle")
    await screen.check(phone=True)
    console = await browser.new_page(viewport=DESKTOP)  # type: ignore[arg-type]
    await console.goto(f"{web}/retailer/suppliers")
    await console.wait_for_load_state("networkidle")
    body = await console.inner_text("body")
    await console.close()
    await page.close()
    return screen.problems + ([] if "terms" in body else ["terms: Suppliers shows no answer"])


async def main(web: str, headed: bool) -> int:
    settings = get_settings()
    SHOTS.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 (once, before any page)
    await reset(settings, "A")
    await SCENES["waiting"][1](settings)
    results: dict[str, list[str]] = {}
    async with async_playwright() as pw:
        launch: dict[str, Any] = {"headless": not headed}
        try:
            browser = await pw.chromium.launch(channel="chrome", **launch)
        except Exception:
            browser = await pw.chromium.launch(**launch)
        results["vendor-join"] = await visit(browser, f"{web}/vendor/join", "vendor-join", PHONE)
        for tab in VENDOR_TABS:
            url = f"{web}/vendor?vendor={MAYA}&tab={tab}"
            results[f"vendor-{tab}"] = await visit(browser, url, f"vendor-{tab}", PHONE)
        for page in CONSOLE_PAGES:
            url = f"{web}/retailer/{page}"
            results[f"console-{page}"] = await visit(browser, url, f"console-{page}", DESKTOP)
        for page in LAPTOP_PAGES:
            url = f"{web}/vendor?vendor={MAYA}&tab={page}"
            results[f"vendor-laptop-{page}"] = await visit(
                browser, url, f"vendor-laptop-{page}", DESKTOP
            )
        results["flow-laptop-chat"] = await laptop_chat(browser, web)
        results["installable"] = await installable(browser, web)
        results["flow-language"] = await switch_language(browser, web)
        results["flow-terms"] = await accept_terms(browser, web)
        await browser.close()
    failed = 0
    for name, problems in results.items():
        print(f"  {'✗' if problems else '✓'} {name}")
        for problem in problems:
            print(f"      {problem}")
        failed += bool(problems)
    print(
        f"\nUI CHECK {'FAILED' if failed else 'PASSED'}: {len(results)} screens, {failed} failing"
    )
    print(f"screenshots: {SHOTS}/")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--web", default="http://localhost:5173")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.web.rstrip("/"), args.headed)))
