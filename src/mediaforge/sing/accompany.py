"""Accompaniment that follows the voice: đàn tranh, sáo trúc, a drone, and light percussion for songs.

Synthesised here (additive synthesis, no samples, no licence questions) so it is always in the voice's key and
locked to its timing: the instruments know every note the voice will sing. That is how traditional ngâm thơ is
accompanied — the zither and flute play a prelude, answer each line in the breath that follows it (lòng bản),
and stay sparse underneath the voice.
"""

from __future__ import annotations

import math

import numpy as np

from mediaforge import audio as A
from mediaforge.sing.melody import Melody, Note, ladder_to_semitones

SR = A.SR


def _hz(tonic: float, semis: float) -> float:
    return tonic * 2 ** (semis / 12.0)


class Track:
    """A stereo buffer with positioned, panned additions."""

    def __init__(self, seconds: float):
        self.buf = np.zeros((2, int(seconds * SR) + SR), dtype=np.float32)

    def add(self, x: np.ndarray, at: float, gain: float = 1.0, pan: float = 0.0) -> None:
        i = max(0, int(at * SR))
        if i >= self.buf.shape[1]:
            return
        x = x[: self.buf.shape[1] - i] * gain
        l, r = math.cos((pan + 1) * math.pi / 4), math.sin((pan + 1) * math.pi / 4)
        self.buf[0, i:i + len(x)] += x * l
        self.buf[1, i:i + len(x)] += x * r


def _phase(freq: np.ndarray) -> np.ndarray:
    return 2 * np.pi * np.cumsum(freq) / SR


def tranh(f: float, dur: float = 2.2, bend: float = 0.0, vib: float = 0.0, seed: int = 0) -> np.ndarray:
    """Đàn tranh pluck: bright decaying partials, slight inharmonicity, optional nhấn (bend) and rung (vibrato)."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    # Nhấn: a bend that arrives ~120 ms after the pluck; rung: a slow left-hand vibrato.
    cents = bend * 100 * np.clip((t - 0.12) / 0.18, 0, 1) + vib * 100 * np.sin(2 * np.pi * 5.5 * t) * np.clip((t - 0.3) / 0.4, 0, 1)
    inst = f * 2 ** (cents / 1200)
    y = np.zeros(n)
    for k in range(1, 13):
        if f * k > 9000:
            break
        partial = k * math.sqrt(1 + 0.0004 * k * k)       # stiff-string inharmonicity
        decay = 1.1 + 0.55 * k                           # higher partials die faster
        amp = (1.0 / k ** 1.1) * (1.0 if k % 2 else 0.8) * (1 - 0.6 * abs(math.sin(math.pi * k * 0.18)))
        y += amp * np.exp(-decay * t) * np.sin(_phase(inst * partial) + rng.uniform(0, 6.28))
    pick = rng.normal(0, 1, min(n, int(0.008 * SR))) * np.linspace(1, 0, min(n, int(0.008 * SR)))
    y[: pick.size] += 0.25 * pick
    y *= np.minimum(1.0, t / 0.002)
    y *= np.clip((dur - t) / 0.08, 0, 1)
    return (y / (np.abs(y).max() + 1e-9)).astype(np.float32)


def sao(freqs: list[tuple[float, float]], dur: float, vib: float = 0.25, seed: int = 0) -> np.ndarray:
    """Sáo trúc phrase: pitch path [(t, Hz)], breathy tone with soft attack and vibrato."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    ts, hz = zip(*freqs)
    f = np.interp(t, ts, hz)
    f = f * 2 ** (vib * np.sin(2 * np.pi * 5.0 * t) * np.clip((t - 0.25) / 0.4, 0, 1) / 12)
    ph = _phase(f)
    y = np.sin(ph) + 0.22 * np.sin(2 * ph + 0.5) + 0.07 * np.sin(3 * ph + 1.1)
    noise = rng.normal(0, 1, n)
    from scipy.signal import butter, sosfilt

    sos = butter(2, [1500, 6000], btype="band", fs=SR, output="sos")
    y = y + 0.18 * sosfilt(sos, noise)
    env = np.minimum(1, t / 0.09) * np.clip((dur - t) / 0.35, 0, 1) * (0.85 + 0.15 * np.sin(2 * np.pi * 0.6 * t))
    return (y * env / (np.abs(y).max() + 1e-9)).astype(np.float32)


def drone(f: float, dur: float) -> np.ndarray:
    """Soft low pad on the tonic and fifth, slowly breathing."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for mult, amp in ((1.0, 1.0), (1.5, 0.5), (2.0, 0.35), (3.0, 0.12)):
        y += amp * np.sin(2 * np.pi * f * mult * t + mult) * (0.8 + 0.2 * np.sin(2 * np.pi * (0.07 * mult) * t))
    env = np.minimum(1, t / 2.5) * np.clip((dur - t) / 3.0, 0, 1)
    return (y * env / (np.abs(y).max() + 1e-9)).astype(np.float32)


def mo(dur: float = 0.25, seed: int = 0) -> np.ndarray:
    """Mõ (wood block) tick."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    y = np.sin(2 * np.pi * 880 * t) * np.exp(-28 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t) * np.exp(-40 * t)
    y += 0.15 * rng.normal(0, 1, t.size) * np.exp(-120 * t)
    return (y / np.abs(y).max()).astype(np.float32)


def trong(dur: float = 0.6) -> np.ndarray:
    """Low frame-drum hit (trống)."""
    t = np.arange(int(dur * SR)) / SR
    f = 52 + 60 * np.exp(-18 * t)
    y = np.sin(_phase(f)) * np.exp(-6 * t)
    return (y / np.abs(y).max()).astype(np.float32)


def _scale_hz(mel: Melody, tonic: float, lo_step: float, hi_step: float) -> list[float]:
    return [_hz(tonic, ladder_to_semitones(s, mel.scale)) for s in np.arange(lo_step, hi_step + 1)]


def _gliss(tr: Track, mel: Melody, tonic: float, at: float, down: bool, steps: tuple[float, float],
           spacing: float = 0.07, gain: float = 0.5, pan: float = -0.35) -> float:
    notes = _scale_hz(mel, tonic, *steps)
    if down:
        notes = notes[::-1]
    for i, f in enumerate(notes):
        tr.add(tranh(f, 2.4, seed=i), at + i * spacing, gain * (0.7 + 0.3 * i / len(notes)), pan)
    return at + len(notes) * spacing


def arrange(mel: Melody, tonic: float, *, seed: int = 3) -> np.ndarray:
    """Full accompaniment for a melody whose ladder tonic is `tonic` Hz. Returns stereo (2, n) float32."""
    tr = Track(mel.length + 2)
    up = tonic * 4          # instruments sit two octaves above the voice's ladder tonic
    lines = mel.lines()
    tr.add(drone(tonic * 2, mel.length + 1.5), 0.0, 0.10, 0.0)
    if mel.style == "ngam":
        _arrange_ngam(tr, mel, up, lines, seed)
    else:
        _arrange_hat(tr, mel, up, lines, seed)
    out = tr.buf
    return (out / (np.abs(out).max() + 1e-9) * 0.8).astype(np.float32)


def _echo_path(line: list[Note], up: float, start: float) -> tuple[list[tuple[float, float]], float]:
    """Sáo answer: the line's last three notes, an octave up, slower, ending on the line's cadence."""
    tail = line[-3:]
    t, path = 0.0, []
    for i, n in enumerate(tail):
        d = 0.55 if i < len(tail) - 1 else 1.4
        f = _hz(up, n.main_pitch)
        path += [(t, f), (t + d * 0.85, f)]
        t += d
    return path + [(t + 0.3, path[-1][1])], t + 0.3


def _arrange_ngam(tr: Track, mel: Melody, up: float, lines: list[list[Note]], seed: int) -> None:
    # Prelude: a falling glissando across two octaves, then the flute states the opening line's cadence.
    end = _gliss(tr, mel, up / 2, 0.15, True, (3, 12), gain=0.45)
    first = lines[0]
    f_cad = _hz(up, first[-1].main_pitch)
    tr.add(sao([(0, f_cad * 2 ** (-2 / 12)), (0.25, f_cad), (2.2, f_cad)], 2.6, seed=seed), end + 0.2, 0.22, 0.35)
    for li, line in enumerate(lines):
        # Under the voice: a soft pluck on the line's first syllable and its caesura.
        tr.add(tranh(_hz(up / 2, line[0].main_pitch), 2.0, seed=li), line[0].start, 0.32, -0.3)
        held = [n for n in line if n.rest_after > 0 or n.melisma]
        for n in held[:1]:
            tr.add(tranh(_hz(up / 2, n.main_pitch - 12), 2.6, vib=0.25, seed=li + 10), n.start + 0.15, 0.28, -0.3)
        last = line[-1]
        # Under the held final syllable: a slow tremolo (vê) on the cadence note.
        f = _hz(up / 2, last.main_pitch)
        k = int(last.dur / 0.16)
        for j in range(k):
            tr.add(tranh(f, 0.5, seed=100 + j), last.start + 0.5 + j * 0.16, 0.10 * (1 - j / max(1, k)), -0.3)
        # The answer (lòng bản) in the breath after the line.
        if last.rest_after > 0.6 or li == len(lines) - 1:
            path, d = _echo_path(line, up, last.end)
            tr.add(sao(path, d, seed=seed + li), last.end - 0.2, 0.24, 0.35)
            tr.add(tranh(_hz(up / 2, line[-1].main_pitch), 2.4, bend=0.5, seed=li + 30), last.end + d * 0.5, 0.26, -0.3)
    # Postlude: rising glissando and a final low tonic with a long ring.
    t_end = mel.notes[-1].end + 0.2
    end = _gliss(tr, mel, up / 2, t_end, False, (0, 7), spacing=0.09, gain=0.35)
    tr.add(tranh(up / 2, 4.0, vib=0.2, seed=99), end + 0.3, 0.5, -0.2)


# Diatonic triads (semitones above the key tonic) that sit well under a pentatonic tune.
_CHORDS = {"I": (0, 4, 7), "IV": (5, 9, 12), "V": (7, 11, 14), "vi": (9, 12, 16), "ii": (2, 5, 9)}
_MINOR_CHORDS = {"i": (0, 3, 7), "iv": (5, 8, 12), "v": (7, 10, 14), "VI": (8, 12, 15), "VII": (10, 14, 17)}


def _chord_for(notes: list[tuple[float, float]], minor: bool) -> tuple[int, ...]:
    table = _MINOR_CHORDS if minor else _CHORDS
    best, score = None, -1.0
    for name, tri in table.items():
        pcs = {p % 12 for p in tri}
        s = sum(d for p, d in notes if round(p) % 12 in pcs) + (0.3 if name in ("I", "i") else 0)
        if s > score:
            best, score = tri, s
    return best  # type: ignore[return-value]


def _arrange_hat(tr: Track, mel: Melody, up: float, lines: list[list[Note]], seed: int) -> None:
    beat = 60.0 / float(mel.bpm or 72)
    bar = 4 * beat
    minor = mel.scale != "bac"
    end_t = mel.notes[-1].end + bar
    n_bars = int(math.ceil(end_t / bar))
    voice = [(n.start, n.end, n.main_pitch) for n in mel.notes]
    for b in range(n_bars):
        t0 = b * bar
        inside = [(p, min(e, t0 + bar) - max(s, t0)) for s, e, p in voice if s < t0 + bar and e > t0]
        tri = _chord_for(inside, minor) if inside else ((0, 3, 7) if minor else (0, 4, 7))
        root = up / 4 * 2 ** (tri[0] / 12)
        # Bass pluck on 1 and 3, broken-chord tranh in eighths, mõ on 2 and 4, trống on 1.
        tr.add(tranh(root, 1.6, seed=b), t0, 0.42, 0.0)
        tr.add(tranh(root * 1.5, 1.2, seed=b + 1), t0 + 2 * beat, 0.25, 0.0)
        pattern = [0, 1, 2, 1, 2, 1, 0, 2] if b % 2 == 0 else [0, 2, 1, 2, 0, 1, 2, 1]
        for i, k in enumerate(pattern):
            f = up / 2 * 2 ** (tri[k] / 12)
            tr.add(tranh(f, 1.1, seed=b * 8 + i), t0 + i * beat / 2, 0.16 if i % 2 else 0.22, -0.4)
        if t0 >= mel.intro - 1e-6 or b >= 1:
            tr.add(mo(seed=b), t0 + beat, 0.08, 0.5)
            tr.add(mo(seed=b + 7), t0 + 3 * beat, 0.08, 0.5)
            tr.add(trong(), t0, 0.22, 0.0)
    # Flute: lead-in over the intro, then answers in the gaps between lines.
    first = lines[0]
    intro_path = [(0.0, _hz(up, first[0].main_pitch)), (bar * 0.9, _hz(up, first[1].main_pitch if len(first) > 1 else first[0].main_pitch)),
                  (bar * 1.6, _hz(up, first[-1].main_pitch))]
    tr.add(sao(intro_path, min(mel.intro, bar * 2) - 0.2, seed=seed), 0.1, 0.2, 0.35)
    for li, line in enumerate(lines):
        last = line[-1]
        if last.rest_after >= beat * 1.5 or li == len(lines) - 1:
            path, d = _echo_path(line, up, last.end)
            tr.add(sao(path, d, seed=seed + li), last.end - 0.3 * last.dur, 0.18, 0.35)
