"""Hát / Ngâm thơ: tones, melody rules, accompaniment and resynthesis — no TTS/ASR/GPU needed."""

import numpy as np
import pytest

from mediaforge import audio as A
from mediaforge.sing import accompany, melody, resynth, tones
from mediaforge.sing import pipeline as P

POEM = "Mình ta với cả trời sao sáng, Nâng chén sương đêm cạn hết sầu"


def test_tone_detection_and_checked_syllables():
    assert [tones.tone_of(s) for s in ["ta", "trời", "sáng", "cả", "Ngã", "cạn"]] == \
        ["ngang", "huyen", "sac", "hoi", "nga", "nang"]
    assert tones.is_checked("hết") and tones.is_checked("sách") and not tones.is_checked("sầu")
    assert tones.base_letters("Đường") == "duong"


def test_inline_poem_splits_into_lines_and_form():
    lines = tones.parse(POEM)
    assert [len(l) for l in lines] == [7, 7]
    assert tones.form(lines) == "that-ngon"
    assert lines[1][-1].text == "sầu" and lines[1][-1].last
    assert tones.form(tones.parse("a b c d e f\na b c d e f g h")) == "luc-bat"
    assert tones.caesura("that-ngon", 7) == 3


@pytest.mark.parametrize("style", ["ngam", "hat"])
def test_melody_covers_every_syllable_in_order(style):
    lines = tones.parse(POEM)
    mel = melody.plan(lines, style=style, mood="hung-trang")
    assert len(mel.notes) == 14
    t = mel.intro
    for n in mel.notes:
        assert n.start == pytest.approx(t, abs=1e-6)
        assert n.dur > 0.2
        t = n.end + n.rest_after
    assert mel.length > t


def test_ngam_tone_shapes_keep_words_recognisable():
    mel = melody.plan(tones.parse(POEM), style="ngam", mood="hung-trang")
    by = {n.syllable: n for n in mel.notes}
    rise = lambda n: n.contour[-1][1] - n.contour[0][1]
    assert rise(by["với"]) > 0          # sắc rises
    assert rise(by["trời"]) < 0         # huyền falls
    assert rise(by["cạn"]) < -1         # nặng drops steeply
    # Same line, same neighbourhood: a sắc syllable sits above a huyền one.
    assert by["với"].main_pitch > by["trời"].main_pitch
    # Line ends are held with melisma; checked syllables stay short.
    assert by["sáng"].melisma and by["sầu"].melisma and by["sầu"].dur > 2.5
    assert by["hết"].dur < 0.5


def test_hat_rhythm_is_on_the_beat_grid():
    mel = melody.plan(tones.parse(POEM), style="hat", mood="hung-trang", bpm=80)
    beat = 60 / 80
    for n in mel.notes:
        assert (n.start / (beat / 2)) == pytest.approx(round(n.start / (beat / 2)), abs=1e-6)
    # Direction follows the tone: sắc after a level tone goes up, huyền comes down.
    notes = mel.notes
    i = [n.syllable for n in notes].index("với")
    assert notes[i].main_pitch >= notes[i - 1].main_pitch


def test_unknown_mood_rejected():
    with pytest.raises(ValueError):
        melody.plan(tones.parse(POEM), mood="vui-ve")


@pytest.mark.parametrize("style", ["ngam", "hat"])
def test_accompaniment_is_finite_stereo_and_covers_the_song(style):
    mel = melody.plan(tones.parse(POEM), style=style, mood="u-hoai")
    acc = accompany.arrange(mel, 110.0)
    assert acc.shape[0] == 2 and np.isfinite(acc).all()
    assert acc.shape[1] / A.SR >= mel.length
    assert np.abs(acc).max() > 0.1


def _vowel(f0=120.0, seconds=1.2, sr=A.SR):
    """A crude voiced 'aa' (harmonics through two formant peaks) with silence around it."""
    t = np.arange(int(seconds * sr)) / sr
    y = sum((1 / k) * np.sin(2 * np.pi * f0 * k * t) * (1.6 if 5 <= k <= 7 or 9 <= k <= 10 else 0.5) for k in range(1, 30))
    y = y * np.hanning(t.size) ** 0.3
    pad = np.zeros(int(0.15 * sr))
    return np.concatenate([pad, y, pad]).astype(np.float32) * 0.2


def test_resynthesis_holds_the_note_and_hits_the_pitch():
    import pyworld as pw

    an = resynth.analyse(_vowel())
    note = melody.Note("a", "ngang", 0, 0, 0.0, 2.0, [(0.0, 12.0), (1.0, 12.0)])
    bounds = [(0, an.frames)]
    y = resynth.sing_line(an, bounds, [note], tonic_hz=100.0)
    assert len(y) / A.SR == pytest.approx(2.0, abs=0.08)
    f0, _ = pw.harvest(y.astype(np.float64), A.SR, frame_period=5.0)
    voiced = f0[f0 > 0]
    assert np.median(voiced) == pytest.approx(200.0, rel=0.04)   # tonic 100 Hz + 12 semitones


def test_clarity_scoring_ignores_case_and_separates_tones():
    c = P.clarity("Nâng chén sương", "nâng chen sương")
    assert c["syllables"] == "3/3" and c["with_tones"] == "2/3"


def test_key_is_snapped_to_equal_temperament():
    assert P.snap_hz(442.0) == pytest.approx(440.0)
    assert P.note_name(110.0) == "A2"
