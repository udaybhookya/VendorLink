"""Record the VendorLink demo video: scripted clicks in a real browser, timed to the narration.

    make dev                                    # in another terminal; wait for "VendorLink ready"
    uv run python scripts/record_demo.py        # ~5 min, hands-free, headless
    uv run python scripts/build_demo_video.py   # → docs/demo_video/out/VendorLink_demo.mp4

The narration (docs/demo_video/narration.json) is spoken with macOS `say` first, so every beat
knows its length; the clicks inside a beat are placed at seconds into that line. Waits on agents
(relays, the Guardian, a config switch) are marked as spans; the build squeezes each to KEEP
seconds, and the clock used for the clicks ignores that squeezed time, so voice, captions and
clicks stay together whatever the latency. Frames come from the Chrome screencast (sharp text),
not Playwright's low-bitrate video.

Options: --headed (watch it), --voice "Samantha" (or --voice system: the Spoken Content
system voice, e.g. a Siri voice), --web http://localhost:5173
"""

from __future__ import annotations

import argparse
import array
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from playwright.async_api import FrameLocator, Locator, Page, async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._demo import MAYA, SARA, call, reset, scene_sara, set_language  # noqa: E402
from vl_core.settings import get_settings  # noqa: E402

DOCS = ROOT / "docs" / "demo_video"
OUT = DOCS / "out"
FRAMES = OUT / "frames"
AUDIO = OUT / "audio"
KEEP = 0.6  # seconds each agent wait keeps in the final cut
PAD = 0.7  # silence after each narration line
VOICES = [
    "Ava (Premium)",
    "Zoe (Premium)",
    "Samantha (Enhanced)",
    "Ava (Enhanced)",
    "Samantha",
    "Daniel",
    "Alex",
]


# ---------------------------------------------------------------- voice
def pick_voice(wanted: str | None) -> str | None:
    if not shutil.which("say"):
        return None
    if (
        wanted == "system"
    ):  # the Spoken Content system voice, e.g. a Siri voice (not listed by say -v ?)
        return "system"
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    names = [
        m.group(1).strip() for m in re.finditer(r"^(.+?)\s{2,}[a-z]{2}_[A-Z]{2}", listing, re.M)
    ]
    for v in ([wanted] if wanted else []) + VOICES:
        if v in names:
            return v
    return names[0] if names else None


def make_voice(beats: list[dict[str, Any]], rate: int, voice: str | None) -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    for i, b in enumerate(beats):
        path = AUDIO / f"{i:02d}_{b['id']}.wav"
        if voice:
            pick = [] if voice == "system" else ["-v", voice]
            subprocess.run(
                [
                    "say",
                    *pick,
                    "-r",
                    str(rate),
                    "--file-format=WAVE",
                    "--data-format=LEI16@24000",
                    "-o",
                    str(path),
                    b["text"],
                ],
                check=True,
            )
            with wave.open(str(path)) as w:
                b["dur"] = w.getnframes() / w.getframerate()
                pcm = array.array("h", w.readframes(w.getnframes()))
            if b["dur"] < 0.5 or max((abs(x) for x in pcm), default=0) < 300:
                raise SystemExit(
                    f"the voice '{voice}' wrote silence for '{b['id']}': macOS may not let "
                    "this voice save to a file. Pick another voice."
                )
            b["audio"] = str(path.relative_to(OUT))
        else:  # no `say` here: time by words, add the voice later
            b["dur"] = len(b["text"].split()) / 2.7
            b["audio"] = None
        print(f"  voice {b['id']:<13} {b['dur']:5.1f}s")


# ---------------------------------------------------------------- director
class Director:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.V: FrameLocator = page.frame_locator("#vendor")
        self.C: FrameLocator = page.frame_locator("#console-frame")
        self.spans: list[list[float]] = []
        self.log: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.b0 = time.time()
        self.paused = 0.0
        self.cur: dict[str, Any] = {}
        self._cap: asyncio.Task[None] | None = None

    async def js(self, expr: str, *args: Any) -> Any:
        return await self.page.evaluate(f"(a) => stage.{expr}(...a)", list(args))

    def net(self) -> float:
        return time.time() - self.b0 - self.paused

    async def at(self, sec: float) -> None:
        while self.net() < sec:  # noqa: ASYNC110 - waiting on the clock, not on an event
            await asyncio.sleep(0.03)

    def frac(self, f: float) -> float:
        return float(self.cur["dur"]) * f

    async def beat(self, b: dict[str, Any]) -> None:
        self.cur, self.b0, self.paused = b, time.time(), 0.0
        await self.js("chip", b["chip"])
        self._cap = asyncio.create_task(self._captions(b))

    async def _captions(self, b: dict[str, Any]) -> None:
        parts = [p for p in re.split(r"(?<=[.!?])\s+", b["text"]) if p]
        total = sum(len(p) for p in parts)
        start = 0.0
        for p in parts:
            await self.at(start)
            await self.js("caption", p)
            start += b["dur"] * len(p) / total

    async def end(self) -> None:
        await self.at(self.cur["dur"] + PAD)
        if self._cap:
            await self._cap
        self.log.append(
            {
                "id": self.cur["id"],
                "b0": self.b0,
                "end": time.time(),
                "dur": self.cur["dur"],
                "audio": self.cur.get("audio"),
            }
        )

    @asynccontextmanager
    async def slow(self, label: str = "") -> AsyncIterator[None]:
        """An agent wait: shown with a tag, squeezed to KEEP seconds in the edit."""
        start = time.time()
        await self.js("busy", True)
        try:
            yield
        finally:
            await self.js("busy", False)
            end = time.time()
            if end - start > KEEP:
                self.spans.append([start, end])
                self.paused += end - start - KEEP

    def err(self, what: str, e: BaseException) -> None:
        msg = f"[{self.cur.get('id')}] {what}: {str(e).splitlines()[0][:200]}"
        self.errors.append(msg)
        print("  ✗", msg)

    # actions -------------------------------------------------------
    async def point(self, loc: Locator, *, ring: bool = False) -> tuple[float, float] | None:
        await loc.wait_for(state="visible", timeout=20000)
        await loc.scroll_into_view_if_needed(timeout=5000)
        box = await loc.bounding_box()
        if not box:
            return None
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await self.js("move", x, y)
        await asyncio.sleep(0.75)
        if ring:
            await self.js("ring", x, y)
        return x, y

    async def click(self, loc: Locator, what: str, hold: float = 0.4) -> bool:
        try:
            await self.point(loc, ring=True)
            await loc.click(timeout=10000)
            await asyncio.sleep(hold)
            return True
        except Exception as e:  # keep recording; the log says what to fix
            self.err(f"click {what}", e)
            return False

    async def select(self, loc: Locator, value: str, what: str) -> None:
        try:
            await self.point(loc, ring=True)
            await loc.select_option(value)
            await asyncio.sleep(0.4)
        except Exception as e:
            self.err(f"select {what}", e)

    async def hl(self, loc: Locator, what: str, ms: int = 2200, cursor: bool = True) -> None:
        try:
            await loc.wait_for(state="visible", timeout=15000)
            await loc.scroll_into_view_if_needed(timeout=5000)
            box = await loc.bounding_box()
            if box:
                if cursor:
                    await self.js(
                        "move", box["x"] + min(box["width"] - 10, 60), box["y"] + box["height"] / 2
                    )
                await self.js("hl", box, ms)
        except Exception as e:
            self.err(f"highlight {what}", e)

    async def wait(self, loc: Locator, what: str, timeout: float = 180) -> bool:  # noqa: ASYNC109
        try:
            async with self.slow(what):
                await loc.wait_for(state="visible", timeout=timeout * 1000)
            return True
        except Exception as e:
            self.err(f"wait {what}", e)
            return False

    async def poll(
        self,
        loc: Locator,
        frame: str,
        url: str,
        what: str,
        timeout: float = 240,  # noqa: ASYNC109
    ) -> bool:
        """Wait for something that arrives from another agent, reloading that app every few seconds
        (the reload is part of the squeezed wait, so it never shows at full length)."""
        try:
            async with self.slow(what):
                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        await loc.wait_for(state="visible", timeout=3500)
                        return True
                    except Exception:
                        await self.js("load", frame, url)
                        await asyncio.sleep(1.5)
            raise TimeoutError(f"not seen in {timeout:.0f}s")
        except Exception as e:
            self.err(f"wait {what}", e)
            return False

    def nav(self, label: str) -> Locator:
        return self.C.locator(".sidebar nav a", has_text=re.compile(rf"^\s*{re.escape(label)}"))

    def tab(self, label: str) -> Locator:
        return self.V.locator(".tabbar button", has_text=label)

    async def run(self, title: str, cmd: list[str], fresh: bool = True) -> None:
        if fresh:
            await self.js("term", True, title)
        else:
            await self.js("line", "")
            await self.js("line", title)
        env = {**os.environ, "PYTHONPATH": f"{ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=ROOT,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                await self.js("line", line[:90])
                await asyncio.sleep(0.12)
        await proc.wait()
        if proc.returncode:
            self.errors.append(f"[{self.cur.get('id')}] {cmd[-2]} exited {proc.returncode}")


# ---------------------------------------------------------------- the demo
async def demo(d: Director, beats: dict[str, dict[str, Any]], web: str) -> None:
    s = get_settings()
    V, C = d.V, d.C
    maya = f"{web}/vendor?vendor={MAYA}"
    photo = ROOT / "seed" / "photos" / "demo" / "p2_cluttered.jpeg"

    # ---- intro: problem statement (from the deck), problem, idea
    await d.beat(beats["title"])
    await d.js("slide", "title")
    await d.js("load", "vendor", f"{maya}&tab=talk")
    await d.js("load", "console-frame", f"{web}/retailer/inbox")
    await d.end()
    await d.beat(beats["problem"])
    await d.js("slide", "problem")
    await d.end()
    await d.beat(beats["architecture"])
    await d.js("slide", "arch")
    await d.end()

    # ---- 1 · photo to listing, enrichment, a person approves
    await d.beat(beats["listing"])
    await d.js("unslide")
    await d.at(1.2)
    await d.click(V.locator(".quick .chip-btn.primary").first, "Add a product")
    await d.at(d.frac(0.45))
    try:
        await d.point(V.locator(".composer label.camera"), ring=True)
        await V.locator(".composer input[type=file]").set_input_files(str(photo))
    except Exception as e:
        d.err("attach photo", e)
    await d.at(d.frac(0.62))
    box = V.locator(".composer input:not([type=file])")
    try:
        await d.point(box, ring=True)
        await box.press_sequentially(
            "40 lavender candles at 220 rupees each, 250 gram jar", delay=28
        )
        await box.press("Enter")
    except Exception as e:
        d.err("describe product", e)
    skip = V.locator(".quick .chip-btn", has_text="Skip")
    try:
        await skip.wait_for(state="visible", timeout=2500)
        await d.click(skip, "Skip label")
    except Exception:
        pass
    await d.end()

    await d.beat(beats["enrich"])
    result = V.locator(".listing-result").last
    await d.wait(result.locator(".photo-choices, .approved").first, "agents build the listing", 240)
    await d.at(1.0)
    await d.hl(result.locator(".reasons, .check").first, "photo flags", 3000)
    await d.at(d.frac(0.2))
    await d.hl(result.locator(".photo-choices").first, "enhanced + AI photos", 5000)
    await d.at(d.frac(0.55))
    await d.hl(result.locator(".listing-copy").first, "title & description", 3500)
    await d.at(d.frac(0.85))
    pick = result.locator(".photo-choice button.yes")
    if not await pick.count():
        pick = result.locator(".photo-choice button").nth(1)
    await d.click(pick.first, "pick AI photo")
    await d.end()

    await d.beat(beats["publish"])
    item = C.locator(".inbox-list button", has_text="Review product").first
    await d.poll(item, "console-frame", f"{web}/retailer/inbox", "listing reaches the Inbox", 120)
    await d.click(item, "listing in Inbox")
    await d.at(d.frac(0.3))
    await d.hl(C.locator(".inbox-detail").first, "her photo vs AI photo", 4500)
    await d.at(d.frac(0.8))
    await d.click(C.locator(".decision-bar button.yes").first, "Approve and publish")
    await d.end()

    # ---- 2 · her rules
    await d.beat(beats["rules"])
    await d.at(0.6)
    await d.click(d.tab("My shop"), "tab My shop")
    await d.hl(V.locator(".rules .rule-grid .rule-card").first, "price & quantity", 3200)
    await d.at(d.frac(0.45))
    await d.hl(V.locator(".rules .rule-grid .rule-card").last, "pickup & payment", 3200)
    await d.at(d.frac(0.8))
    await d.hl(V.locator(".rules .sig"), "signed mandate", 2200)
    await d.end()

    # ---- 3 · an order, the Guardian counters
    await d.beat(beats["place"])
    await d.at(0.4)
    await d.click(d.nav("Orders"), "nav Orders")
    await d.select(C.locator(".form-row select").nth(1), "store:hub-4", "store")
    await d.at(d.frac(0.8))
    await d.click(C.get_by_role("button", name="Place order"), "Place order")
    await d.end()

    await d.beat(beats["counter"])
    card = C.locator("article.card").first
    await d.wait(card.locator(".pill.red"), "Guardian decision")
    await d.at(1.0)
    await d.hl(card.locator("tr").first, "v1 red + reasons", 4500)
    await d.at(d.frac(0.6))
    await d.hl(card.locator("tr").nth(1), "v2 counter", 3500)
    await d.end()

    # ---- 4 · people in the loop (Inbox + Slack)
    await d.beat(beats["inbox"])
    await d.at(0.5)
    await d.click(d.nav("Inbox"), "nav Inbox")
    item = C.locator(".inbox-list button", has_text="Supplier countered").first
    if await d.wait(item, "counter in Inbox", 180):
        await d.click(item, "counter item")
    await d.at(d.frac(0.3))
    await d.hl(C.locator(".why").first, "why this needs you", 3000)
    await d.at(d.frac(0.5))
    await d.hl(C.locator(".why p", has_text="Also asked").first, "also asked on Slack", 4000)
    await d.at(d.frac(0.9))
    await d.click(C.locator(".decision-bar button.yes").first, "Accept the counter")
    await d.end()

    # ---- 5 · her yes, signed
    await d.beat(beats["consent"])
    await d.at(0.4)
    await d.click(d.tab("Deals"), "tab Deals")
    yes = V.locator(
        "section.deals .deal button.yes:visible"
    ).first  # Needs you (hidden) has the same card

    async def nudge() -> None:  # run the Liaison now instead of waiting for its next tick
        while True:
            for base in (s.retailer_svc_url, s.vendor_svc_url):
                with suppress(Exception):
                    await call(base, "POST", "/dev/tick", expect=None)
            await asyncio.sleep(4)

    poke = asyncio.create_task(nudge())
    deals_url = f"{maya}&tab=deals"
    await d.poll(yes, "vendor", deals_url, "deal waits for her")
    deal = V.locator("section.deals .deal:has(button.yes)").first
    await d.hl(deal, "amber deal", 2500)
    await d.at(d.frac(0.3))
    await d.hl(deal.locator(".readback"), "read-back", 3000)
    await d.at(d.frac(0.5))
    await d.hl(V.locator(".lang select"), "language picker (kept in English)", 1800)
    await d.at(d.frac(0.78))
    await d.click(yes, "Yes, sign")
    await d.end()

    await d.beat(beats["signed"])
    await d.poll(
        V.locator("section.deals .deal .sig").first, "vendor", deals_url, "Guardian signs", 180
    )
    poke.cancel()
    await d.hl(V.locator("section.deals .deal:has(.sig)").first, "signed deal", 2500)
    await d.at(d.frac(0.4))
    await d.click(d.nav("Orders"), "nav Orders")
    await d.hl(C.locator("article.card header").first, "order mandate hash", 3000)
    await d.end()

    # ---- 6 · observability
    await d.beat(beats["observe"])
    await d.at(0.3)
    await d.click(d.nav("Observability"), "nav Observability")
    await d.hl(C.locator(".table-scroll").first, "who talks to whom", 2500)
    await d.at(d.frac(0.22))
    if await d.click(C.locator("button.link", has_text="vendor-agent:maya").first, "Maya's agent"):
        await d.hl(C.locator(".drawer").first, "trust score, keys, permissions", 2500, cursor=False)
        try:
            await d.point(C.locator(".drawer button", has_text="Suspend").first)
        except Exception as e:
            d.err("point Suspend", e)
    await d.at(d.frac(0.5))
    close = C.locator(".drawer").get_by_role("button", name="Close")
    if await close.count():
        await d.click(close, "close drawer", hold=0.2)
    await d.click(C.locator("nav.subtabs button", has_text="Activities"), "Activities tab")
    await d.hl(C.locator(".page table").first, "time & tokens per flow", 3500)
    await d.end()

    # ---- 7 · under attack
    await d.beat(beats["attack"])
    await d.click(C.locator("nav.subtabs button", has_text="Overview"), "Overview tab", hold=0.1)
    py = [sys.executable]
    await d.run(
        "$ python scripts/attack_impostor.py --live", [*py, "scripts/attack_impostor.py", "--live"]
    )
    await d.run(
        "$ python scripts/attack_injected_doc.py --live",
        [*py, "scripts/attack_injected_doc.py", "--live"],
        fresh=False,
    )
    await d.at(d.frac(0.75))
    await d.js("term", False)
    await d.click(C.locator("nav.subtabs button", has_text="Safety"), "Safety tab")
    await d.end()

    # ---- 8 · new market, no code
    await d.beat(beats["config_b"])
    await d.at(0.3)
    await d.click(d.nav("Configuration"), "nav Configuration")
    await d.hl(C.locator(".chips").first, "specialists (A)", 1800)
    await d.at(d.frac(0.3))
    tile_b = C.locator(".tile.preset", has_text="Packaged food").first
    await d.click(tile_b.locator("button"), "Activate B")
    await d.wait(tile_b.locator("button", has_text="Active"), "config B active", 60)

    async def prepare_sara() -> None:
        await set_language(s, "en", SARA)  # on screen everything stays in English
        await scene_sara(s)

    sara_task = asyncio.create_task(prepare_sara())
    await d.at(d.frac(0.6))
    await d.hl(C.locator(".chips").first, "specialists (B)", 3000)
    await d.at(d.frac(0.88))
    await d.hl(C.locator("p", has_text="Config hash").first, "config hash", 2000)
    await d.end()

    await d.beat(beats["sara"])
    try:
        async with d.slow("Sara's listing"):
            await sara_task
    except Exception as e:
        d.err("scene sara", e)
    await d.js("who", "Supplier app · Sara's phone")
    await d.js("load", "vendor", f"{web}/vendor?vendor={SARA}&tab=products")
    await d.click(d.nav("Inbox"), "nav Inbox")
    item = C.locator(".inbox-list button", has_text=re.compile("cassava", re.I)).first
    if await d.poll(item, "console-frame", f"{web}/retailer/inbox", "Sara's listing in Inbox", 60):
        await d.click(item, "Sara listing")
    await d.at(d.frac(0.4))
    await d.hl(C.locator(".why").first, "P-IRT flag", 3500)
    await d.end()

    # ---- 9 · provable
    await d.beat(beats["audit"])
    await d.at(0.3)
    await d.click(d.nav("Trust"), "nav Trust")
    await d.at(d.frac(0.18))
    await d.click(C.get_by_role("button", name="Verify log"), "Verify")
    await d.hl(C.locator(".form-row strong.ok"), "chain intact", 2200)
    await d.at(d.frac(0.55))
    await d.click(C.get_by_role("button", name=re.compile("Tamper")), "Tamper")
    await d.click(C.get_by_role("button", name="Verify log"), "Verify again")
    await d.hl(C.locator(".form-row strong.bad"), "broken at", 2200)
    await d.at(d.frac(0.85))
    await d.hl(C.locator("tr.broken"), "broken row", 2200, cursor=False)
    await d.end()

    await d.beat(beats["close"])
    await d.js("slide", "close")
    await d.end()


async def main(web: str, headed: bool, voice_name: str | None) -> int:
    spec = json.loads((DOCS / "narration.json").read_text())
    beats = spec["beats"]
    if OUT.exists():
        shutil.rmtree(OUT)
    FRAMES.mkdir(parents=True)
    voice = pick_voice(voice_name)
    print(f"voice: {voice or 'none (no macOS say) — timing by word count'}")
    make_voice(beats, spec.get("voice_rate", 178), voice)

    s = get_settings()
    print("resetting the demo (config A)…")
    await reset(s, "A")
    await set_language(s, "en", MAYA)

    stage_html = (DOCS / "stage.html").read_text()
    frames: list[float] = []
    async with async_playwright() as pw:
        launch: dict[str, Any] = {"headless": not headed}
        try:
            browser = await pw.chromium.launch(channel="chrome", **launch)
        except Exception:
            browser = await pw.chromium.launch(**launch)
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080}, device_scale_factor=1
        )
        page = await ctx.new_page()
        page.on("dialog", lambda dlg: asyncio.ensure_future(dlg.accept("demo")))
        # served on the app's own origin, so both iframes are same-origin and scriptable
        await page.route(
            f"{web}/__demo_stage", lambda r: r.fulfill(body=stage_html, content_type="text/html")
        )
        await page.goto(f"{web}/__demo_stage")
        await page.wait_for_function("window.stage !== undefined")

        cdp = await ctx.new_cdp_session(page)

        acks: set[asyncio.Future[Any]] = set()

        def on_frame(p: dict[str, Any]) -> None:
            (FRAMES / f"{len(frames):06d}.jpg").write_bytes(base64.b64decode(p["data"]))
            frames.append(float(p["metadata"]["timestamp"]))
            ack = asyncio.ensure_future(
                cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
            )
            acks.add(ack)
            ack.add_done_callback(acks.discard)

        cdp.on("Page.screencastFrame", on_frame)
        await cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 92,
                "maxWidth": 1920,
                "maxHeight": 1080,
                "everyNthFrame": 1,
            },
        )
        t_start = time.time()
        d = Director(page)
        try:
            await demo(d, {b["id"]: b for b in beats}, web)
        finally:
            await asyncio.sleep(0.5)
            await cdp.send("Page.stopScreencast")
            await ctx.close()
            await browser.close()

    timeline = {
        "t_start": t_start,
        "keep": KEEP,
        "frames": frames,
        "beats": d.log,
        "spans": d.spans,
        "errors": d.errors,
        "voice": voice,
    }
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=1))
    print(f"\n{len(frames)} frames, {len(d.spans)} agent waits, {len(d.log)} beats")
    if d.errors:
        print(f"RECORDING FINISHED WITH {len(d.errors)} PROBLEMS:")
        for e in d.errors:
            print("  -", e)
    else:
        print("RECORDING OK")
    print("next: uv run python scripts/build_demo_video.py")
    return 1 if d.errors else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", default="http://localhost:5173")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--voice", default=None)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.web.rstrip("/"), a.headed, a.voice)))
