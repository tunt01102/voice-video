"""Speech → song: re-voice the owner's cloned speech on a melody with the WORLD vocoder (pyworld, MIT/BSD).

WORLD splits speech into pitch (f0), spectral envelope (the timbre and the vowel — the owner's voice) and
aperiodicity. We keep the envelope and aperiodicity of every syllable, replace the pitch with the melody, and
re-time each syllable: consonants keep their natural length, the vowel nucleus is held for the note's duration.
Because the envelope comes from the owner's own cloned speech, the result is sung in the owner's voice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from mediaforge import audio as A
from mediaforge.sing.melody import Note

log = logging.getLogger(__name__)
FP = 5.0  # WORLD frame period (ms)


@dataclass
class Analysis:
    f0: np.ndarray
    sp: np.ndarray
    ap: np.ndarray
    sr: int
    x: np.ndarray | None = None   # the spoken waveform (unvoiced consonants are copied from it verbatim)

    @property
    def frames(self) -> int:
        return len(self.f0)


def analyse(audio: np.ndarray, sr: int = A.SR) -> Analysis:
    import pyworld as pw

    x = audio.astype(np.float64)
    f0, t = pw.harvest(x, sr, f0_floor=60.0, f0_ceil=600.0, frame_period=FP)
    f0 = pw.stonemask(x, f0, t, sr)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)
    # Harvest fills pitch into fricatives (s, x, ch); D4C's aperiodicity is the reliable voicing cue — a
    # voiced frame is periodic below 3 kHz.
    hz = np.arange(sp.shape[1]) * sr / 2 / (sp.shape[1] - 1)
    ap_low = ap[:, (hz > 100) & (hz < 3000)].mean(axis=1)
    f0 = np.where(ap_low < 0.5, f0, 0.0)
    return Analysis(f0, sp, ap, sr, audio.astype(np.float32))


def median_f0(an: Analysis) -> float:
    v = an.f0[an.f0 > 0]
    return float(np.median(v)) if v.size else 140.0


def _frame_energy(an: Analysis) -> np.ndarray:
    return np.log(an.sp.sum(axis=1) + 1e-12)


_UNVOICED_ONSET = ("ch", "tr", "th", "kh", "ph", "s", "x", "h", "t", "c", "k", "q", "p")


def unvoiced_onset(syllable: str) -> bool:
    from mediaforge.sing.tones import base_letters

    return base_letters(syllable).startswith(_UNVOICED_ONSET)


def refine_bounds(an: Analysis, spans: list[tuple[float, float]], texts: list[str] | None = None) -> list[tuple[int, int]]:
    """ASR word times → frame ranges that tile the line.

    ASR word times on fast speech are often near-uniform guesses, so each boundary is re-found from the signal
    within ±125 ms: before a syllable with an unvoiced onset (s, x, ch, tr, h, t, c…) it is the frame where
    voicing stops; before a voiced onset (m, n, ng, l, v, đ…) it is the deepest energy dip. Candidates far from
    the ASR guess pay a small distance penalty.
    """
    e = _frame_energy(an)
    v = an.f0 > 0
    n = an.frames
    fr = lambda s: int(round(s * 1000 / FP))
    loud = np.where(e > e.max() - 9.0)[0]
    first = int(loud[0]) if loud.size else 0
    last = int(loud[-1]) + 1 if loud.size else n
    cuts = [max(0, min(fr(spans[0][0]), first))]
    for k, ((s0, e0), (s1, e1)) in enumerate(zip(spans, spans[1:])):
        mid = fr((e0 + s1) / 2)
        lo, hi = max(cuts[-1] + 12, mid - 25), min(n - 12, mid + 25)
        if hi <= lo:
            cuts.append(min(n - 2, max(cuts[-1] + 4, mid)))
            continue
        cand = np.arange(lo, hi)
        # Every syllable carries a vowel: a boundary needs ≥ 60 ms of voicing since the previous one.
        vcum = np.cumsum(v)
        ok = cand[(vcum[cand - 1] - vcum[cuts[-1]]) >= 12]
        cand = ok if ok.size else cand
        dist = np.abs(cand - mid) / 25.0
        if texts and k + 1 < len(texts) and unvoiced_onset(texts[k + 1]):
            offs = cand[(v[cand - 1]) & (~v[cand])]   # voiced → unvoiced: the next consonant begins
            if offs.size:
                cuts.append(int(offs[np.argmin(np.abs(offs - mid))]))
                continue
        es = _smooth(e, 3)[cand]
        cost = (es - es.min()) / (np.ptp(es) + 1e-9) + 0.6 * dist
        cuts.append(int(cand[np.argmin(cost)]))
    cuts.append(max(cuts[-1] + 4, min(n, max(fr(spans[-1][1]) + 6, last))))
    return list(zip(cuts, cuts[1:]))


def _contour_at(note: Note, frac: np.ndarray) -> np.ndarray:
    xs = [c[0] for c in note.contour]
    ys = [c[1] for c in note.contour]
    return np.interp(frac, xs, ys)


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or x.size < k:
        return x
    pad = np.pad(x, (k // 2, k - 1 - k // 2), mode="edge")
    return np.convolve(pad, np.ones(k) / k, mode="valid")


def _flux(an: Analysis) -> np.ndarray:
    """Frame-to-frame spectral change (log envelope) — low where a vowel is steady."""
    lsp = np.log(an.sp + 1e-16)
    d = np.zeros(an.frames)
    d[1:] = np.sqrt(np.mean(np.diff(lsp, axis=0) ** 2, axis=1))
    return d


def _timeline(an: Analysis, a: int, b: int, voiced: np.ndarray, n_out: int) -> np.ndarray:
    """Source frame index for every output frame of one syllable.

    Consonants, the consonant→vowel transition and the vowel→coda glide (the 'u' of 'sầu', the 'ng' of 'sáng')
    play at natural speed; only the steadiest stretch of the vowel nucleus is held. The hold breathes gently
    around that point (±~15 ms) so the timbre keeps moving — a frozen frame sounds robotic.
    """
    if voiced.size < 6:  # (almost) unvoiced: plain stretch
        return np.linspace(a, b - 1, n_out)
    va, vb = int(voiced[0]), int(voiced[-1]) + 1
    onset = np.arange(max(a, va - 30), va, dtype=float)       # ≤150 ms initial consonant
    coda = np.arange(vb, min(b, vb + 24), dtype=float)       # unvoiced release / breath
    span = vb - va
    # Steadiest 30 ms window in the first 60 % of the voiced part (the nucleus precedes glides and codas).
    lo, hi = va + min(6, span // 4), max(va + min(6, span // 4) + 1, va + int(span * 0.6))
    fl = _smooth(_flux(an)[lo:hi], 6) if hi - lo > 6 else np.zeros(hi - lo)
    c = lo + int(np.argmin(fl)) if fl.size else (va + vb) // 2
    lead = np.arange(va, c, dtype=float)
    trail = np.arange(c, vb, dtype=float)
    natural = onset.size + lead.size + trail.size + coda.size
    n_hold = n_out - natural
    if n_hold <= 0:  # short note: compress the voiced part, keep consonants intact
        n_v = max(4, n_out - onset.size - coda.size)
        return np.concatenate([onset, np.linspace(va, vb - 1, n_v), coda])
    secs = np.arange(n_hold) * FP / 1000
    wob = 3.0 * np.sin(2 * np.pi * secs / 1.3) * np.clip(secs / 0.2, 0, 1)
    hold = np.clip(c + wob, va, vb - 1)
    return np.concatenate([onset, lead, hold, trail, coda])


def sing_line(an: Analysis, bounds: list[tuple[int, int]], notes: list[Note], tonic_hz: float,
              seed: int = 7, prosody: float = 0.0) -> np.ndarray:
    """One line: syllable frame ranges + notes → sung audio starting at notes[0].start (time 0 = that start)."""
    import pyworld as pw

    rng = np.random.default_rng(seed)
    f0_out, sp_out, ap_out, semis_out, vib_out = [], [], [], [], []
    quiet = int(np.argmin(_frame_energy(an)))
    lf0 = _log_f0(an)
    passthrough: list[tuple[int, int, int]] = []   # (output frame, source frame, frames) of natural-speed unvoiced runs
    pos = 0
    for (a, b), note in zip(bounds, notes):
        n_out = max(4, int(round(note.dur * 1000 / FP)))
        voiced = np.arange(a, b)[an.f0[a:b] > 0]
        idx = _timeline(an, a, b, voiced, n_out)
        passthrough += [(pos + o, s, n) for o, s, n in _unvoiced_runs(an, idx)]
        i0 = np.clip(np.floor(idx).astype(int), 0, an.frames - 1)
        i1 = np.clip(i0 + 1, 0, an.frames - 1)
        w = (idx - np.floor(idx))[:, None]
        sp = np.exp((1 - w) * np.log(an.sp[i0] + 1e-16) + w * np.log(an.sp[i1] + 1e-16))
        ap = (1 - w) * an.ap[i0] + w * an.ap[i1]
        src_voiced = (an.f0[i0] > 0) | (an.f0[i1] > 0)
        frac = np.linspace(0, 1, len(idx))
        if note.dur > 1.0:
            # Messa di voce on held notes: swell into the middle, ease off at the end (sp is power: gain²).
            g = (0.82 + 0.3 * np.sin(np.pi * frac ** 0.8)) * np.clip((1 - frac) / 0.1, 0.35, 1)
            sp = sp * (g ** 2)[:, None]
        semis = _contour_at(note, frac)
        if prosody > 0:
            # Keep the spoken tone's own micro-contour (nặng's drop, ngã's break, sắc's rise) on top of the note:
            # it is what Vietnamese ears use to tell the tones apart. Deviation from the syllable's median pitch.
            seg = lf0[int(a):int(b)]
            ref = float(np.median(seg)) if seg.size else 0.0
            dev = np.clip(12 * ((1 - w[:, 0]) * lf0[i0] + w[:, 0] * lf0[i1] - ref), -4, 4)
            semis = semis + prosody * dev
        # Vibrato: rises in after the first 35 % of a held note, 5.3 Hz with a little rate wobble.
        vib = np.zeros(len(idx))
        if note.vibrato > 0 and len(idx) * FP / 1000 > 0.45:
            ramp = np.clip((frac - 0.3) / 0.25, 0, 1)
            rate = 5.3 + 0.25 * np.sin(np.arange(len(idx)) * FP / 1000 * 1.3)
            phase = 2 * np.pi * np.cumsum(rate) * FP / 1000
            vib = note.vibrato * ramp * np.sin(phase)
        f0_out.append(np.where(src_voiced, 1.0, 0.0))
        sp_out.append(sp)
        ap_out.append(ap)
        semis_out.append(semis)
        vib_out.append(vib)
        pos += len(idx)
        if note.rest_after > 0 and note is not notes[-1]:
            k = int(round(note.rest_after * 1000 / FP))
            pos += k
            f0_out.append(np.zeros(k))
            sp_out.append(np.repeat(an.sp[quiet:quiet + 1] * 1e-3, k, axis=0))
            ap_out.append(np.repeat(an.ap[quiet:quiet + 1], k, axis=0))
            semis_out.append(np.zeros(k))
            vib_out.append(np.zeros(k))
    vmask = np.concatenate(f0_out) > 0
    semis = _smooth(np.concatenate(semis_out), 9)              # ~45 ms legato between notes
    drift = _smooth(rng.normal(0, 0.06, semis.size).cumsum() * 0.08, 41)  # tiny natural pitch drift
    semis = semis + np.concatenate(vib_out) + drift - drift.mean()
    f0 = np.where(vmask, tonic_hz * 2 ** (semis / 12.0), 0.0)
    sp = np.ascontiguousarray(np.concatenate(sp_out))
    ap = np.ascontiguousarray(np.concatenate(ap_out))
    # Slightly less breath noise on sung vowels: sustained notes read as singing, not stretched speech.
    ap = np.where(vmask[:, None], ap * 0.75, ap)
    y = pw.synthesize(np.ascontiguousarray(f0), sp, ap, an.sr, frame_period=FP).astype(np.float32)
    if an.x is not None:
        y = _paste_consonants(y, an, passthrough)
    return y


def _log_f0(an: Analysis) -> np.ndarray:
    """log2 f0 with unvoiced gaps interpolated (a continuous intonation curve)."""
    v = an.f0 > 0
    if not v.any():
        return np.zeros(an.frames)
    t = np.arange(an.frames)
    return np.interp(t, t[v], np.log2(an.f0[v]))


def _unvoiced_runs(an: Analysis, idx: np.ndarray) -> list[tuple[int, int, int]]:
    """Runs of output frames that play an unvoiced source stretch at natural speed (s, x, ch, h, stop bursts)."""
    runs, k, n = [], 0, len(idx)
    while k < n:
        src = idx[k]
        if src == int(src) and an.f0[int(src)] == 0:
            j = k + 1
            while j < n and idx[j] == idx[j - 1] + 1 and an.f0[int(idx[j])] == 0:
                j += 1
            if j - k >= 3:   # ≥ 15 ms
                runs.append((k, int(src), j - k))
            k = j
        else:
            k += 1
    return runs


def _paste_consonants(y: np.ndarray, an: Analysis, runs: list[tuple[int, int, int]]) -> np.ndarray:
    """WORLD renders fricatives as hiss without their spectral detail ('sương' turns into 'xương'), so unvoiced
    consonants are copied from the spoken waveform, crossfaded over 4 ms. They carry no pitch, so nothing is lost."""
    hop = int(an.sr * FP / 1000)
    fade = int(0.004 * an.sr)
    out = y.copy()
    for o, s, n in runs:
        a, b = o * hop, min(len(out), (o + n) * hop)
        src = an.x[s * hop: s * hop + (b - a)]
        if src.size < b - a or b - a <= 2 * fade:
            continue
        w = np.ones(b - a, dtype=np.float32)
        w[:fade] = np.linspace(0, 1, fade)
        w[-fade:] = np.linspace(1, 0, fade)
        out[a:b] = out[a:b] * (1 - w) + src * w
    return out


def place(lines_audio: list[tuple[float, np.ndarray]], total: float, sr: int = A.SR) -> np.ndarray:
    out = np.zeros(int(total * sr) + sr, dtype=np.float32)
    for start, y in lines_audio:
        i = int(start * sr)
        out[i:i + len(y)] += y[: len(out) - i]
    return out
