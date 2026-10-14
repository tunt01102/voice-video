"""Tone-aware melody: turn a poem's syllables into notes that keep Vietnamese words intelligible.

Two styles share one rule set:
  ngam  chanting (ngâm thơ): free rhythm, register follows the tone, line-final syllables held with melisma
        and vibrato, long breaths between lines for the instrument's echo (lòng bản).
  hat   singing: a beat grid (BPM, 4/4), syllable = note, and the *direction* of each step follows the tone
        (sắc/ngã go up, huyền/nặng go down, hỏi dips) — the rule Vietnamese songwriters use so lyrics stay clear.

Pitches are expressed as fractional steps on a pentatonic ladder (0 = tonic, 5 = octave) and converted to
semitones at the end, so a mode ('bac' bright/majestic, 'oan' plaintive) is just a different scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from mediaforge.sing.tones import Syllable, caesura, form

# Ladders: scale degrees in semitones above the tonic (one octave; extended by ±12 as needed).
SCALES = {
    "bac": [0, 2, 4, 7, 9],     # hơi Bắc — bright, upright: hùng tráng, khí khái
    "oan": [0, 3, 5, 7, 10],    # hơi Oán — plaintive: u hoài, nhớ quê
    "ai": [0, 3, 5, 7, 8],      # hơi Ai — mournful (minor sixth colour)
}
MOODS = {
    # mood → scale, home step (register of a level tone), range, pace multiplier, vibrato depth (semitones)
    "hung-trang": {"scale": "bac", "home": 5.0, "arch": 1.4, "pace": 1.0, "vib": 0.42},
    "tram-mac": {"scale": "bac", "home": 4.5, "arch": 0.8, "pace": 1.18, "vib": 0.32},
    "u-hoai": {"scale": "oan", "home": 4.5, "arch": 1.0, "pace": 1.12, "vib": 0.38},
    "bi-ai": {"scale": "ai", "home": 4.0, "arch": 0.9, "pace": 1.25, "vib": 0.36},
}

# Contour of each tone over the syllable's voiced part: (fraction of the note, ladder steps relative to home).
TONE_SHAPE = {
    "ngang": [(0.0, 0.0), (1.0, 0.0)],
    "huyen": [(0.0, -1.4), (1.0, -2.0)],
    "sac": [(0.0, 0.2), (0.35, 1.0), (1.0, 1.2)],
    "hoi": [(0.0, -1.0), (0.45, -2.0), (1.0, -1.1)],
    "nga": [(0.0, 0.3), (0.3, -0.3), (0.5, 0.9), (1.0, 1.5)],
    "nang": [(0.0, -1.5), (0.55, -1.9), (1.0, -3.4)],   # short, then a steep glottal drop
}
# For singing: step direction implied by the tone (ladder steps from the previous note).
TONE_STEP = {"ngang": 0, "huyen": -1, "sac": 1, "hoi": -1, "nga": 2, "nang": -2}
# Cadence notes (ladder index) — lines resolve on the dominant (open) or tonic (closed).
OPEN, CLOSED = 3.0, 5.0   # degree V of the ladder, upper tonic


@dataclass
class Note:
    syllable: str
    tone: str
    line: int
    index: int
    start: float                     # seconds from the start of the vocal
    dur: float                       # time allotted to the syllable (consonants + vowel)
    contour: list[tuple[float, float]]  # (fraction, semitones above tonic) over the voiced part
    vibrato: float = 0.0             # depth in semitones (peak), applied to the held part only
    melisma: bool = False
    rest_after: float = 0.0

    @property
    def end(self) -> float:
        return self.start + self.dur

    @property
    def main_pitch(self) -> float:
        """Semitone the note dwells on (time-weighted contour mean) — used for accompaniment echoes."""
        c = self.contour
        if len(c) == 1:
            return c[0][1]
        tot = sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(c, c[1:]))
        span = c[-1][0] - c[0][0]
        return tot / span if span > 0 else c[0][1]

    def as_dict(self) -> dict[str, Any]:
        return {"syllable": self.syllable, "tone": self.tone, "line": self.line, "index": self.index,
                "start": round(self.start, 3), "dur": round(self.dur, 3), "rest_after": round(self.rest_after, 3),
                "contour": [(round(f, 3), round(s, 2)) for f, s in self.contour], "vibrato": self.vibrato,
                "melisma": self.melisma}


@dataclass
class Melody:
    style: str
    mood: str
    form: str
    scale: str
    bpm: float | None
    notes: list[Note] = field(default_factory=list)
    intro: float = 0.0               # seconds before the first syllable (instrumental prelude)
    outro: float = 0.0

    @property
    def length(self) -> float:
        last = self.notes[-1]
        return last.end + last.rest_after + self.outro

    def lines(self) -> list[list[Note]]:
        out: dict[int, list[Note]] = {}
        for n in self.notes:
            out.setdefault(n.line, []).append(n)
        return [out[k] for k in sorted(out)]

    def as_dict(self) -> dict[str, Any]:
        return {"style": self.style, "mood": self.mood, "form": self.form, "scale": self.scale, "bpm": self.bpm,
                "intro": self.intro, "outro": self.outro, "length": round(self.length, 3),
                "notes": [n.as_dict() for n in self.notes]}


def ladder_to_semitones(step: float, scale: str) -> float:
    """Fractional ladder step → semitones (linear between neighbouring degrees)."""
    deg = SCALES[scale]
    lo = math.floor(step)
    frac = step - lo
    octave, i = divmod(lo, len(deg))
    a = deg[i] + 12 * octave
    o2, j = divmod(lo + 1, len(deg))
    b = deg[j] + 12 * o2
    return a + (b - a) * frac


def _nearest(target: float, choices: list[float]) -> float:
    return min(choices, key=lambda c: abs(c - target))


def _cadence(tone: str, line_no: int, n_lines: int, home: float) -> float:
    """Final note of a line: closed (tonic) on even lines / the last line, open (dominant) otherwise —
    but always on the side of home that the tone points to, so the word stays recognisable."""
    closed = line_no % 2 == 1 or line_no == n_lines - 1
    base = CLOSED if closed else OPEN
    shape = TONE_SHAPE[tone]
    want = home + shape[-1][1]
    # Candidates: the cadence degree in three octaves, plus the upper tonic for rising tones.
    cands = [base - 5, base, base + 5]
    return _nearest(want, cands)


def _melisma(target: float, tone: str) -> list[tuple[float, float]]:
    """Ornamented hold on a line-final syllable: the tone's own onset, a turn around the target, then a long
    settled hold (vibrato added later) and a soft fall-off."""
    onset = TONE_SHAPE[tone]
    lift = 1.0 if tone in ("sac", "nga", "ngang") else -1.0
    head = [(0.0, target + onset[0][1] - onset[-1][1]), (0.14, target)]
    turn = [(0.26, target + 0.55 * lift), (0.38, target), (0.47, target - 0.45 * lift), (0.56, target)]
    tail = [(0.93, target), (1.0, target - 0.35)]
    return head + turn + tail


def _semis(contour: list[tuple[float, float]], scale: str) -> list[tuple[float, float]]:
    return [(f, ladder_to_semitones(s, scale)) for f, s in contour]


def plan(lines: list[list[Syllable]], *, style: str = "ngam", mood: str = "hung-trang", bpm: float | None = None,
         tempo: float = 1.0, intro: float | None = None) -> Melody:
    """Notes for every syllable. `tempo` > 1 is faster. Deterministic for the same input."""
    if style not in ("ngam", "hat"):
        raise ValueError(f"unknown style {style!r}")
    if mood not in MOODS:
        raise ValueError(f"unknown mood {mood!r}; choose from {', '.join(MOODS)}")
    m = MOODS[mood]
    f = form(lines)
    if style == "hat":
        bpm = float(bpm or (76 if mood == "hung-trang" else 66))
        mel = Melody("hat", mood, f, m["scale"], bpm, intro=intro if intro is not None else 4 * 60 / bpm * 2)
        _plan_hat(mel, lines, m, bpm * tempo)
    else:
        mel = Melody("ngam", mood, f, m["scale"], None, intro=intro if intro is not None else 4.5)
        _plan_ngam(mel, lines, m, tempo)
    return mel


def _plan_ngam(mel: Melody, lines: list[list[Syllable]], m: dict[str, Any], tempo: float) -> None:
    pace = m["pace"] / tempo
    t = mel.intro
    n_lines = len(lines)
    for li, line in enumerate(lines):
        cut = caesura(mel.form, len(line))
        # Phrase arch: the line climbs to its caesura then settles; the opening line starts proud and high.
        for s in line:
            pos = s.index / max(1, len(line) - 1)
            arch = m["arch"] * math.sin(math.pi * min(1.0, pos * 1.15))
            home = m["home"] + arch * (0.6 if li % 2 else 1.0) - (0.4 if li % 2 else 0.0)
            if s.last:
                target = _cadence(s.tone, li, n_lines, home)
                contour = _melisma(target, s.tone)
                dur = (3.6 if li == n_lines - 1 else 2.8) * pace
                rest = (1.7 if li % 2 else 1.1) * pace if li < n_lines - 1 else 0.0
                note = Note(s.text, s.tone, li, s.index, t, dur, _semis(contour, mel.scale), m["vib"], True, rest)
            else:
                contour = [(fr, home + st) for fr, st in TONE_SHAPE[s.tone]]
                if s.checked or s.tone == "nang":
                    dur = 0.34 if s.checked else 0.42
                elif s.bang:
                    dur = 0.62
                else:
                    dur = 0.5
                rest = 0.0
                if cut is not None and s.index == cut:
                    dur *= 1.9   # held caesura syllable, then a short breath
                    rest = 0.22
                dur *= pace
                rest *= pace
                vib = m["vib"] * 0.6 if dur > 0.8 else 0.0
                note = Note(s.text, s.tone, li, s.index, t, dur, _semis(contour, mel.scale), vib, False, rest)
            mel.notes.append(note)
            t = note.end + note.rest_after
    mel.outro = 4.0 * pace


def _plan_hat(mel: Melody, lines: list[list[Syllable]], m: dict[str, Any], bpm: float) -> None:
    beat = 60.0 / bpm
    bar = 4 * beat
    t = mel.intro
    n_lines = len(lines)
    prev = m["home"] - 1.0
    lo, hi = m["home"] - 3.0, m["home"] + 3.0
    for li, line in enumerate(lines):
        cut = caesura(mel.form, len(line))
        line_start = t
        # Rhythm: one beat per syllable, half a beat for checked/short trắc syllables, the caesura held 2 beats,
        # the last syllable held to fill out the bar (+ one more bar on the final line).
        beats = []
        for s in line:
            b = 0.5 if (s.checked and not s.last) else 1.0
            if cut is not None and s.index == cut and not s.last:
                b = 2.0
            beats.append(b)
        used = sum(beats[:-1])
        bars = math.ceil((used + 2.0) / 4.0)
        beats[-1] = bars * 4.0 - used - (1.0 if li < n_lines - 1 else 0.0)
        if li == n_lines - 1:
            beats[-1] += 4.0
        # Melodic motion: tone gives the direction, the phrase arch gives the aim; snap to whole steps.
        for s, b in zip(line, beats):
            pos = s.index / max(1, len(line) - 1)
            aim = m["home"] + m["arch"] * math.sin(math.pi * pos) * (1.0 if li % 2 == 0 else 0.6)
            if s.last:
                step = _cadence(s.tone, li, n_lines, aim)
            else:
                d = TONE_STEP[s.tone]
                if d == 0:  # level tone drifts toward the phrase aim
                    d = (aim > prev + 0.5) - (aim < prev - 0.5)
                step = float(round(min(hi, max(lo, prev + d))))
                # Keep the register honest: a falling tone never lands above home+1, a rising one never below home-1.
                if s.tone in ("huyen", "nang") and step > m["home"] + 1:
                    step -= 1
                if s.tone in ("sac", "nga") and step < m["home"] - 1:
                    step += 1
            dur = b * beat
            onset = TONE_SHAPE[s.tone]
            # A short tone-shaped glide into the note keeps the word recognisable; the rest is a steady pitch.
            glide = 0.12 if dur > 0.5 else 0.25
            approach = step + (onset[0][1] - onset[-1][1]) * 0.35
            contour = [(0.0, approach), (glide, step), (1.0, step)]
            if s.tone == "hoi" and not s.last:
                contour = [(0.0, step), (0.4, step - 0.6), (1.0, step)]
            if s.last:
                contour = [(0.0, approach), (0.1, step), (0.92, step), (1.0, step - 0.25)]
            vib = m["vib"] * (1.0 if dur >= 1.2 else 0.5 if dur >= 0.7 else 0.0)
            note = Note(s.text, s.tone, li, s.index, t, dur, _semis(contour, mel.scale), vib, s.last, 0.0)
            mel.notes.append(note)
            t += dur
            prev = step
        # Rest to the end of the bar (one beat before the next line's pickup).
        line_len = t - line_start
        if li < n_lines - 1:
            rest = (math.ceil(line_len / bar - 1e-6) * bar) - line_len + beat
            mel.notes[-1].rest_after = rest
            t += rest
    mel.outro = 2 * bar
