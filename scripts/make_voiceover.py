"""Make a new voiceover for the recorded demo, without recording again (macOS).

    uv run python scripts/make_voiceover.py                 # the Spoken Content system voice
    uv run python scripts/make_voiceover.py --voice "Ava (Premium)"
    uv run python scripts/build_demo_video.py --only final --voice voice

To use a Siri voice: System Settings → Accessibility → Spoken Content → System voice → pick the
Siri voice (download it if asked), then run without --voice. Each line is written to
docs/demo_video/out/voice/ and checked: some macOS versions refuse to save Siri voices to a file
and write silence, which this script reports.
"""

from __future__ import annotations

import argparse
import array
import json
import subprocess
import sys
import wave
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs" / "demo_video"


def main(voice: str | None, rate: int | None, folder: str) -> int:
    spec = json.loads((DOCS / "narration.json").read_text())
    rate = rate or spec.get("voice_rate", 178)
    out = DOCS / "out" / folder
    out.mkdir(parents=True, exist_ok=True)
    silent = []
    for i, b in enumerate(spec["beats"]):
        path = out / f"{i:02d}_{b['id']}.wav"
        pick = ["-v", voice] if voice else []
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
            dur = w.getnframes() / w.getframerate()
            peak = max((abs(x) for x in array.array("h", w.readframes(w.getnframes()))), default=0)
        print(f"  {b['id']:<13} {dur:5.1f}s{'   ← SILENT' if peak < 300 else ''}")
        if peak < 300:
            silent.append(b["id"])
    if silent:
        print(
            "\nThis voice wrote silence: macOS did not let it save to a file. Pick another voice."
        )
        return 1
    print(f"\nvoice lines in {out.relative_to(DOCS.parents[1])}/")
    print(f"next: uv run python scripts/build_demo_video.py --only final --voice {folder}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=None, help="a `say -v ?` voice; default: the system voice")
    ap.add_argument(
        "--rate", type=int, default=None, help="words per minute (default from narration.json)"
    )
    ap.add_argument("--out", default="voice", help="folder under docs/demo_video/out/")
    a = ap.parse_args()
    sys.exit(main(a.voice, a.rate, a.out))
