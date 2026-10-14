"""Audio I/O helpers. Internal working format: float32 mono, 48 kHz."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from mediaforge.config import settings

SR = 48_000


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-1500:]}")


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(
        [settings.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def to_wav(src: str | Path, dst: str | Path, sr: int = SR) -> Path:
    """Convert any audio/video file (webm, m4a, mp3, mov…) to mono wav."""
    run_ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)])
    return Path(dst)


def load(path: str | Path, sr: int = SR) -> np.ndarray:
    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    data = data.mean(axis=1)
    return resample(data, file_sr, sr)


def save(path: str | Path, audio: np.ndarray, sr: int = SR) -> Path:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(audio, -1, 1).astype(np.float32), sr, subtype="PCM_16" if str(path).endswith(".wav") else None)
    return Path(path)


def resample(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or audio.size == 0:
        return audio.astype(np.float32)
    from scipy.signal import resample_poly

    g = np.gcd(int(sr_in), int(sr_out))
    return resample_poly(audio, sr_out // g, sr_in // g).astype(np.float32)


def silence(seconds: float, sr: int = SR) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


def trim_silence(audio: np.ndarray, sr: int = SR, threshold_db: float = -42, pad: float = 0.06) -> np.ndarray:
    if audio.size == 0:
        return audio
    frame = int(0.02 * sr)
    n = len(audio) // frame
    if n == 0:
        return audio
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    voiced = np.where(db > max(threshold_db, db.max() - 40))[0]
    if voiced.size == 0:
        return audio
    p = int(pad * sr)
    start = max(0, voiced[0] * frame - p)
    end = min(len(audio), (voiced[-1] + 1) * frame + p)
    return audio[start:end]


def crossfade_concat(chunks: list[np.ndarray], gaps: list[float], sr: int = SR, fade: float = 0.01) -> np.ndarray:
    """Join chunks with silences; tiny fades avoid clicks at the edges."""
    f = int(fade * sr)
    out: list[np.ndarray] = []
    for i, c in enumerate(chunks):
        c = c.copy()
        if len(c) > 2 * f:
            ramp = np.linspace(0, 1, f, dtype=np.float32)
            c[:f] *= ramp
            c[-f:] *= ramp[::-1]
        out.append(c)
        if i < len(chunks) - 1:
            out.append(silence(gaps[i], sr))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)
