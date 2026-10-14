"""Optional AI video clips via ltx-2-mlx (image-to-video). Off by default: ~2.5–3.5 min per 5 s clip.

Install (one-off, tens of GB of weights on first run):
    git clone https://github.com/dgrauet/ltx-2-mlx ~/tools/ltx-2-mlx && cd ~/tools/ltx-2-mlx && uv sync --all-extras
Then set MEDIAFORGE_LTX_DIR=~/tools/ltx-2-mlx.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def ltx_command() -> list[str] | None:
    d = os.environ.get("MEDIAFORGE_LTX_DIR", os.path.expanduser("~/tools/ltx-2-mlx"))
    if Path(d, "pyproject.toml").exists():
        return ["uv", "run", "--project", d, "ltx-2-mlx"]
    exe = shutil.which("ltx-2-mlx")
    return [exe] if exe else None


def available() -> bool:
    return ltx_command() is not None


def image_to_video(image: str | Path, prompt: str, out: str | Path, *, seconds: float = 4.0, width: int = 1280,
                   height: int = 704, fps: int = 24) -> Path:
    cmd = ltx_command()
    if not cmd:
        raise RuntimeError("ltx-2-mlx not installed; see mediaforge/video/clips.py docstring")
    frames = int(seconds * fps) // 8 * 8 + 1  # LTX wants 8k+1 frames
    subprocess.run([*cmd, "generate", "--prompt", prompt, "--image", str(image), "--distilled",
                    "-H", str(height // 32 * 32), "-W", str(width // 32 * 32), "-f", str(frames),
                    "--frame-rate", str(fps), "-o", str(out)], check=True, capture_output=True, text=True)
    return Path(out)
