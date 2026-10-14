"""ACE-Step 1.5 instrumental bed (MIT code + weights), run as a sidecar process in its own venv.

The bed is generated from a caption in the song's key and tempo, then layered under the synthesised
đàn tranh / sáo, which stay locked to the voice. ACE-Step's bpm/key are text hints, not hard constraints, so
the bed is kept low and the key-locked instruments carry the harmony the voice relies on.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from mediaforge import audio as A
from mediaforge.config import settings

log = logging.getLogger(__name__)
SCRIPT = Path(__file__).with_name("ace_generate.py")
CAPTIONS = {
    "hung-trang": "majestic Vietnamese traditional orchestra, dan tranh zither, bamboo flute, deep drums, epic, heroic, slow build, instrumental",
    "tram-mac": "calm Vietnamese traditional music, dan tranh zither, bamboo flute, soft pad, meditative, spacious, instrumental",
    "u-hoai": "nostalgic Vietnamese traditional music, dan bau monochord, dan tranh, bamboo flute, longing, gentle, instrumental",
    "bi-ai": "mournful Vietnamese traditional music, dan nhi fiddle, dan tranh, slow, sorrowful, sparse, instrumental",
}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def python() -> Path:
    return settings.ace_step_root / ".venv" / "bin" / "python"


def available() -> bool:
    return python().exists() and (settings.ace_step_root / "acestep").is_dir()


def keyscale(tonic_hz: float, scale: str) -> str:
    """Pentatonic ladder → the nearest diatonic key ACE-Step understands ('bac' is major, the others minor)."""
    midi = round(69 + 12 * np.log2(tonic_hz / 440.0))
    return f"{NOTE_NAMES[midi % 12]} {'major' if scale == 'bac' else 'minor'}"


def bed(*, mood: str, style: str, seconds: float, tonic_hz: float, scale: str, bpm: float | None, seed: int,
        timeout: int = 900) -> np.ndarray:
    """Stereo (2, n) bed of exactly `seconds`, faded in/out. Raises if the sidecar is missing or fails."""
    if not available():
        raise RuntimeError("ACE-Step sidecar is not installed — run scripts/install_ace_step.sh")
    caption = CAPTIONS.get(mood, CAPTIONS["tram-mac"])
    if style == "ngam":
        caption += ", free rhythm, no percussion, ambient, rubato"
    with tempfile.TemporaryDirectory(prefix="mf-ace-") as td:
        out = Path(td) / "bed.wav"
        # ACE-Step tends to end early with silence: ask for ~15 % more and trim.
        cmd = [str(python()), "-P", str(SCRIPT), "--prompt", caption, "--duration", f"{min(600, seconds * 1.15 + 2):.1f}",
               "--key", keyscale(tonic_hz, scale), "--seed", str(seed), "--out", str(out)]
        if bpm:
            cmd += ["--bpm", str(int(round(bpm))), "--timesig", "4"]
        env = {"ACE_STEP_ROOT": str(settings.ace_step_root), "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
               "HOME": str(Path.home())}
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(settings.ace_step_root), env=env)
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"ACE-Step failed: {(proc.stderr or proc.stdout)[-800:]}")
        log.info("ace-step: %s", proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "ok")
        import soundfile as sf

        y, sr = sf.read(str(out), dtype="float32", always_2d=True)
    y = np.stack([A.resample(y[:, c], sr, A.SR) for c in range(min(2, y.shape[1]))])
    if y.shape[0] == 1:
        y = np.vstack([y, y])
    n = int(seconds * A.SR)
    if y.shape[1] < n:
        y = np.pad(y, ((0, 0), (0, n - y.shape[1])))
    y = y[:, :n]
    fade_in, fade_out = int(1.5 * A.SR), int(3.0 * A.SR)
    y[:, :fade_in] *= np.linspace(0, 1, fade_in)
    y[:, -fade_out:] *= np.linspace(1, 0, fade_out)
    return (y / (np.abs(y).max() + 1e-9) * 0.8).astype(np.float32)
