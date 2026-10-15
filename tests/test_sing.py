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


def test_ace_key_and_missing_sidecar(monkeypatch, tmp_path):
    from mediaforge.config import settings
    from mediaforge.sing import ace

    assert ace.keyscale(116.54, "bac") == "A# major"
    assert ace.keyscale(110.0, "oan") == "A minor"
    monkeypatch.setattr(settings, "ace_step_root", tmp_path)
    assert not ace.available()
    with pytest.raises(RuntimeError, match="install_ace_step"):
        ace.bed(mood="hung-trang", style="hat", seconds=10, tonic_hz=110, scale="bac", bpm=76, seed=1)


def test_every_catalogue_poem_and_version_can_be_chanted():
    from mediaforge.sing import poems

    assert len(poems.catalog()) >= 10
    for p in poems.catalog():
        assert len(p["han"]) == len(p["phien_am"]) and p["source_urls"]
        for v in poems.versions(p):
            lines = tones.parse(poems.text_for(p, v))
            assert len(lines) == len(p["phien_am"]) or v != "phien-am"
            mel = melody.plan(lines, style="ngam", mood=p["mood"])
            assert len(mel.notes) == sum(len(l) for l in lines)
    assert poems.text_for(poems.get("hoang-hac-lau")).startswith("Hạc vàng ai cưỡi đi đâu?")


def test_speech_prosody_follows_the_spoken_contour():
    import pyworld as pw

    # A spoken syllable whose pitch falls 120 → 90 Hz: with prosody the sung note falls too, around the note.
    sr, t = A.SR, np.arange(int(0.8 * A.SR)) / A.SR
    f = np.linspace(120, 90, t.size)
    ph = 2 * np.pi * np.cumsum(f) / sr
    x = (sum(np.sin(k * ph) / k for k in range(1, 25)) * 0.2).astype(np.float32)
    an = resynth.analyse(np.concatenate([np.zeros(4800, np.float32), x, np.zeros(4800, np.float32)]))
    note = melody.Note("a", "ngang", 0, 0, 0.0, 0.8, [(0.0, 12.0), (1.0, 12.0)])
    flat = resynth.sing_line(an, [(0, an.frames)], [note], 100.0)
    shaped = resynth.sing_line(an, [(0, an.frames)], [note], 100.0, prosody=1.0)
    def slope(y):
        f0, _ = pw.harvest(y.astype(np.float64), sr, frame_period=5.0)
        v = f0[f0 > 0]
        return np.median(v[: v.size // 3]) - np.median(v[-v.size // 3:])
    assert slope(shaped) > slope(flat) + 15


def test_backing_ducks_under_the_voice():
    v = np.zeros(48000 * 2, dtype=np.float32)
    v[48000:] = 0.5 * np.sin(np.arange(48000) / 10)
    g = P.duck_curve(v, v.size)
    assert g[:24000].min() > 0.95 and g[60000:90000].max() < 0.5


def test_interrupted_song_resumes_at_the_next_line(monkeypatch, tmp_path):
    """Lines already sung are kept (sung/line_NN.npy + stages); a resumed job only synthesises the rest."""
    from types import SimpleNamespace

    from mediaforge import asr
    from mediaforge.voice import speak as S

    synth = []

    def fake_synth(text, lang, voice, *, engine=None, seed=0):
        synth.append(text)
        return np.full(A.SR // 2, 0.1, dtype=np.float32), {"engine": "vieneu", "score": 0.0}

    monkeypatch.setattr(S, "synth_sentence", fake_synth)
    monkeypatch.setattr(asr, "transcribe", lambda y, lang: {"text": "x", "words": []})
    monkeypatch.setattr(asr, "align_script", lambda text, words, dur: [SimpleNamespace(start=0.0, end=dur, text="x")])
    monkeypatch.setattr(resynth, "analyse", lambda a: None)
    monkeypatch.setattr(resynth, "median_f0", lambda an: 120.0)
    monkeypatch.setattr(resynth, "refine_bounds", lambda an, b, w: b)
    monkeypatch.setattr(resynth, "sing_line", lambda an, b, notes, tonic, **kw: np.full(A.SR, 0.2, dtype=np.float32))
    monkeypatch.setattr(P, "clarity", lambda text, heard: {"score": 1.0})
    lines = tones.parse(POEM)
    mel = melody.plan(lines, style="ngam", mood="hung-trang")
    state = {}

    def crash_after_first(v):
        state.update(v)
        if len(v["lines"]) == 1:
            raise RuntimeError("server restarted")

    with pytest.raises(RuntimeError):
        P.sing_vocal(lines, mel, voice=None, engine=None, seed=1, out=tmp_path, progress=lambda *a: None,
                     checkpoint=crash_after_first)
    assert len(synth) == 1 and (tmp_path / "sung" / "line_01.npy").exists()
    vo = P.sing_vocal(lines, mel, voice=None, engine=None, seed=1, out=tmp_path, progress=lambda *a: None,
                      resume=state, checkpoint=state.update)
    assert len(synth) == 2 and [r["line"] for r in vo["lines"]] == [0, 1] and vo["tonic"] == state["tonic"]


def test_resumed_song_job_skips_reading_mix_and_drawn_shots(monkeypatch):
    from collections import Counter
    from pathlib import Path

    from mediaforge import image
    from mediaforge.video import compose

    calls = Counter()

    def interpret(text, **kw):
        calls["interpret"] += 1
        return {"mood": "tram-mac", "spirit_vi": "", "title": "T", "opening": "o", "lines": ["a", "b"], "closing": "c",
                "llm": None}

    def vocal(lines, mel, **kw):
        return {"vocal": np.zeros(A.SR, dtype=np.float32), "tonic": 110.0, "lines": []}

    def acc_mix(p, mel, vo, *, out, **kw):
        calls["mix"] += 1
        return {"audio": str(A.save(out / "song.wav", np.zeros(A.SR, dtype=np.float32))), "mp3": "", "vocal": "",
                "accompaniment": None}

    def generate(prompt, *, out_path, **kw):
        calls["image"] += 1
        if calls["image"] == 3:
            raise RuntimeError("server restarted")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"png")
        return {"image": str(out_path)}

    monkeypatch.setattr(P.profiles, "get_voice", lambda v: {})
    monkeypatch.setattr(P, "interpret", interpret)
    monkeypatch.setattr(P, "sing_vocal", vocal)
    monkeypatch.setattr(P, "_accompany_and_mix", acc_mix)
    monkeypatch.setattr(image, "generate", generate)
    monkeypatch.setattr(A, "probe_duration", lambda f: 30.0)
    monkeypatch.setattr(compose, "compose", lambda scenes, *a, **k: str(a[1]))
    monkeypatch.setattr(compose, "thumbnail", lambda f, out, **k: str(out))
    params = {"text": POEM, "voice": "v", "video": True}
    with pytest.raises(RuntimeError):
        P.run(params, lambda *a: None, "song-resume")
    res = P.run(params, lambda *a: None, "song-resume")
    assert calls["interpret"] == 1 and calls["mix"] == 1 and res["mood"] == "tram-mac"
    assert calls["image"] == 3 + 2  # shots 1–2 kept; shot 3 retried, then 4
    assert len(res["images"]) == 4
