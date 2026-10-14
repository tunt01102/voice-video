"""Voice shaping: pitch ("độ trầm"), pace, warmth, clarity, loudness.

pitch   semitones, -6..+6 (negative = deeper), formants preserved so it stays natural
pace    0.7..1.4 speaking-rate multiplier
warmth  0..1 low-shelf boost around 180 Hz — fuller, "trầm ấm" timbre
clarity 0..1 high-pass, presence boost, compression, de-essing
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from mediaforge import audio as A


@dataclass
class VoiceFX:
    pitch: float = 0.0
    pace: float = 1.0
    warmth: float = 0.0
    clarity: float = 0.5
    loudness: float = -16.0  # LUFS target

    @classmethod
    def from_dict(cls, d: dict | None) -> "VoiceFX":
        d = d or {}
        fx = cls(**{k: float(v) for k, v in d.items() if k in cls.__dataclass_fields__})
        fx.pitch = float(np.clip(fx.pitch, -6, 6))
        fx.pace = float(np.clip(fx.pace, 0.7, 1.4))
        fx.warmth = float(np.clip(fx.warmth, 0, 1))
        fx.clarity = float(np.clip(fx.clarity, 0, 1))
        return fx

    def as_dict(self) -> dict:
        return asdict(self)


def shape(audio: np.ndarray, fx: VoiceFX, sr: int = A.SR) -> np.ndarray:
    """Pitch/pace/EQ/dynamics in-memory (pedalboard). Loudness is applied at file level."""
    from pedalboard import Compressor, HighpassFilter, Limiter, LowShelfFilter, Pedalboard, PeakFilter, time_stretch

    x = audio.astype(np.float32)
    if abs(fx.pitch) > 0.01 or abs(fx.pace - 1) > 0.01:
        x = time_stretch(
            x[np.newaxis, :], sr, stretch_factor=fx.pace, pitch_shift_in_semitones=fx.pitch,
            high_quality=True, preserve_formants=True,
        )[0]
    chain = [HighpassFilter(cutoff_frequency_hz=70 + 30 * fx.clarity)]
    if fx.warmth > 0:
        chain.append(LowShelfFilter(cutoff_frequency_hz=180, gain_db=4.5 * fx.warmth, q=0.7))
    if fx.clarity > 0:
        chain += [
            PeakFilter(cutoff_frequency_hz=350, gain_db=-2.0 * fx.clarity, q=1.0),   # remove boxiness
            PeakFilter(cutoff_frequency_hz=3500, gain_db=3.5 * fx.clarity, q=0.9),   # presence / intelligibility
            Compressor(threshold_db=-20, ratio=1.5 + 2 * fx.clarity, attack_ms=8, release_ms=120),
        ]
    chain.append(Limiter(threshold_db=-1.0))
    return Pedalboard(chain)(x[np.newaxis, :], sr)[0].astype(np.float32)


def finalise(audio: np.ndarray, fx: VoiceFX, out_path: str | Path, sr: int = A.SR) -> Path:
    """De-ess + EBU R128 loudness normalisation with FFmpeg, write wav."""
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory() as td:
        tmp = A.save(Path(td) / "pre.wav", audio, sr)
        filters = []
        if fx.clarity > 0.2:
            filters.append(f"deesser=i={0.3 + 0.4 * fx.clarity:.2f}")
        filters.append(f"loudnorm=I={fx.loudness}:TP=-1.5:LRA=11")
        A.run_ffmpeg(["-i", str(tmp), "-af", ",".join(filters), "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", str(out_path)])
    return out_path


def to_mp3(wav: str | Path, mp3: str | Path) -> Path:
    A.run_ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)])
    return Path(mp3)
