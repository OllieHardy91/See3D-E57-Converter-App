"""Rebuild gui_app/assets/converting_loop.webp from the source point-cloud video.

Run from project root or gui_app/. Requires ffmpeg on PATH or at the well-known
Windows essentials-build location used during development.

The output is the wave video composited over a navy-gradient background
(white source bg keyed out, wave saturation boosted), with a 1 s
crossfade folded over the loop seam so the 14.87 s loop reads as
seamless.

Usage:
    python gui_app/scripts/build_progress_anim.py [path/to/pointcloud_hq.mp4]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"

# Source video lives outside the repo by default (kept under "00.Claude Code/
# Animation_Videos/" to avoid bloating the project tree with raw media).
DEFAULT_SOURCES = [
    Path(r"C:\Users\ollie\OneDrive\Documents\360 Virtual Tour Business\00.Claude Code\Animation_Videos\pointcloud_hq.mp4"),
]


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    well_known = Path(r"C:\Program Files\ffmpeg-2025-12-14-git-3332b2db84-essentials_build\bin\ffmpeg.exe")
    if well_known.exists():
        return str(well_known)
    sys.exit("ffmpeg not found on PATH and no fallback location matched.")


def find_source(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if p.is_file():
            return p
        sys.exit(f"Source video not found: {p}")
    for cand in DEFAULT_SOURCES:
        if cand.is_file():
            return cand
    sys.exit("No source video found; pass the path as the first argument.")


def write_gradient(path: Path, w: int = 800, h: int = 300) -> None:
    """Vertical navy gradient top #0B2470 -> bottom #001247."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    top = (0x0B, 0x24, 0x70)
    bot = (0x00, 0x12, 0x47)
    for y in range(h):
        t = y / max(1, h - 1)
        rgb = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (w, y)], fill=rgb)
    img.save(path)


def main() -> None:
    src = find_source(sys.argv[1] if len(sys.argv) > 1 else None)
    out = ASSETS / "converting_loop.webp"
    ASSETS.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        gradient_path = Path(td) / "navy_gradient.png"
        write_gradient(gradient_path, 800, 300)

        # Filter chain:
        #   1. Crop the wave-active band of the 1080-tall source and scale
        #      to the viewport size at 30 fps.
        #   2. Set alpha from inverted luma via geq: pure white -> fully
        #      transparent, mid-tones -> partially opaque, dark particles
        #      -> opaque. Multiplier 1.8 boosts mid-tones so the wave
        #      "body" stays visible. (Plain colorkey is binary and drops
        #      the volumetric haze.)
        #   3. Bump saturation 1.4x to push the wave toward cyan.
        #   4. Overlay onto the navy gradient.
        #   5. Fold a 1 s crossfade over the loop seam (last 1 s xfaded
        #      with first 1 s, then concat with the middle 13.87 s).
        filt = (
            "[0:v]crop=1920:720:0:300,scale=800:300:flags=lanczos,fps=30,"
            "format=rgba,"
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            "a='clip(1.8*(255-(r(X,Y)+g(X,Y)+b(X,Y))/3),0,255)',"
            "eq=saturation=1.4[wave];"
            "[1:v]format=rgb24[bg];"
            "[bg][wave]overlay=shortest=1[composed];"
            "[composed]split=3[a][b][c];"
            "[a]trim=0:1,setpts=PTS-STARTPTS[first];"
            "[b]trim=14.866:15.866,setpts=PTS-STARTPTS[last];"
            "[last][first]xfade=transition=fade:duration=1:offset=0[xf];"
            "[c]trim=1:14.866,setpts=PTS-STARTPTS[mid];"
            "[xf][mid]concat=n=2:v=1[out]"
        )
        cmd = [
            find_ffmpeg(), "-y",
            "-i", str(src),
            "-loop", "1", "-i", str(gradient_path),
            "-filter_complex", filt, "-map", "[out]",
            "-loop", "0", "-compression_level", "6", "-q:v", "75",
            "-an", str(out),
        ]
        print("Running ffmpeg ...")
        subprocess.run(cmd, check=True)

    print(f"\nWrote {out} ({out.stat().st_size / 1024:.1f} KiB)")


if __name__ == "__main__":
    main()
