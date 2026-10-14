"""Pure-logic tests for guided sentence repair and export (no models are loaded)."""

import json

import numpy as np
import pytest

from mediaforge import audio as A
from mediaforge import export, pipelines, takes as T
from mediaforge.asr import Word
from mediaforge.config import settings
from mediaforge.voice import profiles
from mediaforge.voice.speak import punct_phrases

SR = A.SR


def tone(seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# ---------------------------------------------------------------- phrasing

def test_punct_phrases_splits_long_sentence():
    s = "Mỗi ngày tiến bộ một chút, bạn sẽ không còn là nút thắt, mà trở thành người dẫn đường cho cả đội."
    phrases, pauses = punct_phrases(s)
    assert phrases == ["Mỗi ngày tiến bộ một chút,", "bạn sẽ không còn là nút thắt,", "mà trở thành người dẫn đường cho cả đội."]
    assert pauses == [0.16, 0.16]
    assert " ".join(phrases) == s


def test_punct_phrases_keeps_short_pieces_together():
    assert punct_phrases("Vâng, tôi hiểu.") == (["Vâng, tôi hiểu."], [])
    assert punct_phrases("Không có dấu phẩy nào") == (["Không có dấu phẩy nào"], [])


def test_guide_pauses_follow_the_speaker():
    script = "mỗi ngày tiến bộ một chút bạn sẽ không còn là nút thắt"
    toks = script.split()
    words, t = [], 0.0
    for k, w in enumerate(toks):
        if k == 6:
            t += 0.4  # the owner paused before "bạn"
        words.append(Word(w, t, t + 0.2))
        t += 0.22
    phrases, pauses = profiles.guide_pauses(script, words)
    assert phrases == ["mỗi ngày tiến bộ một chút", "bạn sẽ không còn là nút thắt"]
    assert pauses == [pytest.approx(0.42, abs=0.01)]


def test_guide_pauses_without_pauses_is_one_phrase():
    script = "một hai ba bốn"
    words = [Word(w, k * 0.2, k * 0.2 + 0.19) for k, w in enumerate(script.split())]
    assert profiles.guide_pauses(script, words) == ([script], [])


# ---------------------------------------------------------------- spans, slots, splice

def test_sentence_spans_from_words():
    words = [{"text": w, "start": i * 1.0, "end": i * 1.0 + 0.5} for i, w in enumerate("a b c d e".split())]
    assert pipelines.sentence_spans(["a b.", "c d e."], words) == [[0.0, 1.5], [2.0, 4.5]]
    assert pipelines.sentence_spans(["a b"], words) is None  # token mismatch → recompute


def test_regions_cover_gaps_between_sentences():
    regs = T.regions([[0.1, 1.0], [1.4, 2.0], [2.5, 3.0]], 3.4)
    assert regs[0] == (0.0, pytest.approx(1.36))
    assert regs[1] == (pytest.approx(1.04), pytest.approx(2.46))
    assert regs[2] == (pytest.approx(2.04), 3.4)


def test_fit_modes():
    out, mode = T.fit(tone(0.5), slot=1.0, lead=0.1)
    assert mode == "pad" and len(out) == SR
    out, mode = T.fit(tone(0.85), slot=1.0, lead=0.3)
    assert mode == "borrow" and len(out) == SR
    out, mode = T.fit(tone(0.95), slot=1.0, lead=0.1)
    assert mode == "stretch" and len(out) == SR
    assert T.fit(tone(1.5), slot=1.0, lead=0.1) is None


def test_splice_preserves_length_and_neighbours():
    track = np.concatenate([tone(1.0), np.zeros(SR // 2, np.float32), tone(1.0, 0.5), np.zeros(SR // 2, np.float32), tone(1.0)])
    spans = [[0.0, 1.0], [1.5, 2.5], [3.0, 4.0]]
    new, modes = T.splice(track, spans, {1: tone(0.6, 0.1)})
    assert len(new) == len(track) and modes == {1: "pad"}
    assert np.array_equal(new[: SR], track[: SR])            # sentence 0 untouched
    assert np.array_equal(new[-SR:], track[-SR:])            # sentence 2 untouched
    assert np.max(np.abs(new[int(1.6 * SR): int(2.0 * SR)])) < 0.11  # the take is in the slot


def test_splice_overflow_and_grow():
    track = np.concatenate([tone(1.0), np.zeros(SR // 4, np.float32), tone(1.0)])
    spans = [[0.0, 1.0], [1.25, 2.25]]
    with pytest.raises(T.Overflow):
        T.splice(track, spans, {0: tone(2.0)})
    new, modes = T.splice(track, spans, {0: tone(2.0)}, grow=True)
    assert modes == {0: "grow"} and len(new) > len(track)
    # audio-only jobs grow instead of time-stretching a take that is only slightly too long
    new, modes = T.splice(track, spans, {0: tone(1.12)}, grow=True)
    assert modes == {0: "grow"}


def test_level_match_targets_voiced_rms():
    y = T.level_match(tone(1.0, 0.05), tone(1.0, 0.3))
    assert T.active_rms(y) == pytest.approx(T.active_rms(tone(1.0, 0.3)), rel=0.02)


# ---------------------------------------------------------------- formats

def test_formats_default_and_catalog():
    assert export.VIDEO_JOB_FORMATS[0] == "mp4"
    assert export.formats_for({"video": "x.mp4"})[0] == "mp4"
    assert export.formats_for({"audio": "x.wav"}) == ["mp3", "wav", "m4a"]
    assert [f["id"] for f in export.catalog()["video"]] == export.VIDEO_JOB_FORMATS


def test_ffmpeg_args_remux_copies_video_and_remixes_voice():
    args = export.ffmpeg_args("mov", "final.mp4", "out.mov", voice="v.wav", music_file="m.mp3")
    assert args[:4] == ["-i", "final.mp4", "-i", "v.wav"]
    assert "-stream_loop" in args and "sidechaincompress" in args[args.index("-filter_complex") + 1]
    assert args[args.index("-c:v") + 1] == "copy" and "[aout]" in args and "-shortest" in args
    assert args[-1] == "out.mov"


def test_ffmpeg_args_original_and_audio_only():
    args = export.ffmpeg_args("mkv", "final.mp4", "o.mkv", subs=True)
    assert "0:a" in args and "-filter_complex" not in args and args[args.index("-c:s") + 1] == "srt"
    args = export.ffmpeg_args("mp3", "final.mp4", "o.mp3", voice="v.wav")
    assert "-vn" in args and "0:v" not in args and "libmp3lame" in args
    args = export.ffmpeg_args("wav", "voice.wav", "o.wav", voice="new.wav", base_is_video=False)
    assert args[:2] == ["-i", "new.wav"] and "pcm_s16le" in args


def test_webm_reencodes():
    args = export.ffmpeg_args("webm", "final.mp4", "o.webm")
    assert "libvpx-vp9" in args and "libopus" in args


# ---------------------------------------------------------------- guide index

def test_correction_index_roundtrip(monkeypatch):
    vid = "test-voice"
    try:
        profiles.create_voice("Test", vid)
    except ValueError:
        pass
    d = settings.data_dir / "voices" / vid / "corrections"
    d.mkdir(parents=True, exist_ok=True)
    (d / "c1.wav").write_bytes(b"x")
    entry = {"id": "c1", "text": "Xin chào bạn.", "file": f"voices/{vid}/corrections/c1.wav", "passed": None}
    profiles._save_corrections(vid, {profiles.sentence_key("Xin chào bạn."): [entry]})
    assert profiles.guide_for(vid, "  xin   CHÀO bạn. ")["id"] == "c1"
    profiles.update_correction(vid, "Xin chào bạn.", "c1", strategy="guide-phrases", passed=True)
    assert profiles.corrections(vid)[profiles.sentence_key("Xin chào bạn.")][0]["strategy"] == "guide-phrases"
    assert profiles.guide_for(vid, "Câu khác.") is None


def test_correction_samples_rank_below_enrolment():
    base = {"duration": 8.0, "snr_db": 30, "lang": "vi"}
    refs = profiles.pick_refs([{**base, "id": "corr", "source": "correction", "snr_db": 32}, {**base, "id": "enrol"}])
    assert refs["vi"] == "enrol"
