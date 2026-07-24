"""Render a Playwright WebM capture into a compact GIF and 2x3 still grid."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

FFMPEG = "/opt/homebrew/bin/ffmpeg"


def run(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", *args], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("webm", type=Path)
    parser.add_argument("--gif", type=Path, required=True)
    parser.add_argument("--grid", type=Path, required=True)
    args = parser.parse_args()

    args.gif.parent.mkdir(parents=True, exist_ok=True)
    palette = args.gif.with_suffix(".palette.png")
    filters = "fps=10,scale=1000:-1:flags=lanczos"
    run("-y", "-i", str(args.webm), "-vf", f"{filters},palettegen", str(palette))
    run(
        "-y",
        "-i",
        str(args.webm),
        "-i",
        str(palette),
        "-lavfi",
        f"{filters}[x];[x][1:v]paletteuse",
        str(args.gif),
    )
    palette.unlink()

    run(
        "-y",
        "-i",
        str(args.webm),
        "-vf",
        "fps=1,scale=680:-1:flags=lanczos,tile=3x2",
        "-frames:v",
        "1",
        str(args.grid),
    )


if __name__ == "__main__":
    main()
