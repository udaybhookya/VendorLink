"""Cut the recorded demo into the final video (needs ffmpeg; no other dependencies).

    uv run python scripts/build_demo_video.py      # after scripts/record_demo.py

Agent waits marked during recording are squeezed to `keep` seconds (sped up, not cut, so nothing
jumps); each narration line is placed at its beat's start on the squeezed timeline. Writes
docs/demo_video/out/VendorLink_demo.mp4 and VendorLink_demo.srt.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "demo_video" / "out"
LEAD = 0.15  # voice starts this long after its beat
MAX_TEMPO = 1.08  # a replacement voice may be sped up this much to fit; beyond, the frame holds


def ffmpeg(*args: str) -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit(
            "ffmpeg is not installed (macOS: brew install ffmpeg). The recording is saved in "
            "docs/demo_video/out/; the video can be built on any machine that has ffmpeg."
        )
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def stamp(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main(preset: str, only: str, voice: str) -> None:
    tl = json.loads((OUT / "timeline.json").read_text())
    beats, ts, keep = tl["beats"], tl["frames"], tl["keep"]
    t0, tend = beats[0]["b0"] - 0.2, beats[-1]["end"] + 0.3
    total = tend - t0

    # 1. frames → constant-rate base video (the screencast only sends a frame when the page changes)
    base = OUT / "base.mp4"
    if only in ("", "base"):
        start = max([i for i, t in enumerate(ts) if t <= t0] or [0])
        lines = []
        for i in range(start, len(ts)):
            a = max(ts[i], t0)
            b = min(ts[i + 1] if i + 1 < len(ts) else tend, tend)
            if a >= tend:
                break
            if b > a:
                lines += [f"file 'frames/{i:06d}.jpg'", f"duration {b - a:.4f}"]
        lines.append(lines[-2])  # concat demuxer: repeat the last file
        (OUT / "frames.txt").write_text("\n".join(lines) + "\n")
        ffmpeg(
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(OUT / "frames.txt"),
            "-vf",
            "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            "16",
            str(base),
        )
        print(f"base video: {total:.1f}s")
        if only == "base":
            return

    # 2. squeeze agent waits; map recorded time → final time
    spans = sorted([max(0.0, s - t0), min(total, e - t0)] for s, e in tl["spans"] if e - s > keep)
    pieces: list[list[float]] = []  # [start, end, speed, hold after]
    cur = 0.0
    for s, e in spans:
        if s > cur:
            pieces.append([cur, s, 1.0, 0.0])
        pieces.append([s, e, (e - s) / keep, 0.0])
        cur = e
    pieces.append([cur, total, 1.0, 0.0])

    # a replacement voiceover (scripts/make_voiceover.py): each line keeps its beat's start; a line
    # longer than its slot is sped up a little, and anything left holds the beat's last frame
    for b in beats:
        b["tempo"], b["wav"] = 1.0, OUT / b["audio"] if b.get("audio") else None
    if voice:
        vdir = OUT / voice
        for i, b in enumerate(beats):
            wav = vdir / f"{i:02d}_{b['id']}.wav"
            with wave.open(str(wav)) as w:
                new = w.getnframes() / w.getframerate()
            slot = b["dur"] + 0.55
            b["wav"], b["dur"] = wav, new
            if new > slot:
                b["tempo"] = min(new / slot, MAX_TEMPO)
                hold = new / b["tempo"] - slot + 0.25
                if hold > 0.05:
                    x = b["end"] - t0
                    for k, p in enumerate(pieces):
                        if p[0] < x < p[1]:
                            pieces[k : k + 1] = [[p[0], x, p[2], hold], [x, p[1], p[2], p[3]]]
                            break
                        if abs(p[1] - x) < 1e-6:
                            p[3] += hold
                            break
            b["dur"] = new / b["tempo"]

    def out(t_wall: float) -> float:
        t, acc = t_wall - t0, 0.0
        for a, b, speed, hold in pieces:
            if t <= a:
                break
            acc += (min(t, b) - a) / speed
            if t >= b - 1e-6:
                acc += hold
        return acc

    final_len = out(tend)
    voiced = [b for b in beats if b.get("wav")]
    args = ["-i", str(base)] + [x for b in voiced for x in ("-i", str(b["wav"]))]
    f = [
        f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{sp:.4f}"
        + (f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold else "")
        + f"[v{k}]"
        for k, (a, b, sp, hold) in enumerate(pieces)
    ]
    f.append(
        "".join(f"[v{k}]" for k in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0,fps=30[v]"
    )
    for k, b in enumerate(voiced, start=1):
        ms = int((out(b["b0"]) + LEAD) * 1000)
        tempo = f"atempo={b['tempo']:.4f}," if b["tempo"] > 1.0 else ""
        f.append(f"[{k}:a]aresample=48000,{tempo}adelay={ms}|{ms}[a{k}]")
    if voiced:
        f.append(
            "".join(f"[a{k}]" for k in range(1, len(voiced) + 1))
            + f"amix=inputs={len(voiced)}:normalize=0,alimiter=limit=0.89:level=false,apad[a]"
        )
    final = OUT / f"VendorLink_demo{'_' + voice if voice else ''}.mp4"
    maps = ["-map", "[v]"] + (["-map", "[a]", "-c:a", "aac", "-b:a", "160k"] if voiced else [])
    ffmpeg(
        *args,
        "-filter_complex",
        ";".join(f),
        *maps,
        "-t",
        f"{final_len:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(final),
    )

    # 3. captions file (same sentence timing as the on-screen captions)
    texts = {
        x["id"]: x["text"] for x in json.loads((OUT.parent / "narration.json").read_text())["beats"]
    }
    srt, n = [], 1
    for b in beats:
        parts = [x for x in re.split(r"(?<=[.!?])\s+", texts[b["id"]]) if x]
        tot, at = sum(map(len, parts)), out(b["b0"]) + LEAD
        for part in parts:
            d = b["dur"] * len(part) / tot
            srt.append(f"{n}\n{stamp(at)} --> {stamp(at + d)}\n{part}\n")
            n, at = n + 1, at + d
    (final.with_suffix(".srt")).write_text("\n".join(srt))
    print(
        f"{final.name}: {final_len // 60:.0f}:{final_len % 60:04.1f} · {len(spans)} waits squeezed "
        f"({sum(e - s for s, e in spans):.0f}s → {len(spans) * keep:.0f}s) · "
        f"voice: {voice or 'recorded'}"
    )
    if tl.get("errors"):
        print("recording problems:", *tl["errors"], sep="\n  - ")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="medium", help="x264 preset (veryfast for a quick look)")
    ap.add_argument("--only", default="", choices=["", "base", "final"])
    ap.add_argument("--voice", default="", help="folder in out/ from make_voiceover.py, e.g. voice")
    a = ap.parse_args()
    main(a.preset, a.only, a.voice)
