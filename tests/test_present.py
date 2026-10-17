"""Thuyết trình kèm ghi hình: slides + the owner's recording → one video (docs/SDD-present-record.md).

Synthetic fixtures only (ffmpeg test pattern + sine tone, Pillow slides): recordings are biometric data."""

import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from mediaforge import asr
from mediaforge import audio as A
from mediaforge import present as PR
from mediaforge.config import settings
from mediaforge.voice.fx import VoiceFX


def _streams(path):
    out = subprocess.run([settings.ffprobe, "-v", "error", "-show_entries", "stream=codec_type,duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return {s["codec_type"]: float(s.get("duration") or 0) for s in json.loads(out)["streams"]}


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("present")
    rec = d / "rec.mp4"
    # 6.0 s of picture, 5.4 s of sound: a camera often runs past its audio
    A.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6", "-f", "lavfi",
                  "-i", "sine=frequency=220:duration=5.4", "-c:v", "h264_videotoolbox", "-b:v", "2M", "-c:a", "aac",
                  str(rec)])
    voice_only = d / "talk.m4a"
    A.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=4", "-c:a", "aac", str(voice_only)])
    pages = [Image.new("RGB", (800, 600), c) for c in ((200, 30, 30), (30, 200, 30), (30, 30, 200))]
    pdf = d / "deck.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    pics = []
    for i, im in enumerate(pages):
        pics.append(d / f"slide{i + 1}.png")
        im.save(pics[-1])
    music = d / "music.wav"
    A.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(music)])
    return {"dir": d, "rec": rec, "voice_only": voice_only, "pdf": pdf, "pics": pics, "music": music}


def _words(text, start=0.5, step=0.4):
    return [{"text": t, "start": start + i * step, "end": start + (i + 1) * step - 0.05} for i, t in enumerate(text.split())]


# ------------------------------------------------------------------ script → slides


def test_split_script_separators_and_paragraphs():
    assert PR.split_script("Xin chào.\n---\nPhần hai\nvẫn phần hai\n---\nHết") == ["Xin chào.", "Phần hai vẫn phần hai", "Hết"]
    assert PR.split_script("[slide 1]\nMở đầu\n[slide 2]\nKết") == ["Mở đầu", "Kết"]
    assert PR.split_script("Slide 1:\nA\nTrang 2\nB") == ["A", "B"]
    assert PR.split_script("Đoạn một.\n\nĐoạn hai.\n \nĐoạn ba.") == ["Đoạn một.", "Đoạn hai.", "Đoạn ba."]
    assert PR.split_script("") == []


def test_slide_starts_follow_the_first_word_of_each_part():
    parts = ["một hai ba", "bốn năm", "sáu"]
    words = _words(" ".join(parts), start=1.0, step=1.0)  # words at 1,2,3,4,5,6 s
    starts, guessed = PR.slide_starts(parts, words, total=8.0)
    assert guessed == [False, False, False]
    assert starts[0] == 0.0
    assert starts[1] == pytest.approx(4.0 - PR.LEAD)
    assert starts[2] == pytest.approx(6.0 - PR.LEAD)


def test_slide_starts_through_align_script():
    """The word count per part must match align_script's tokenisation (whitespace split)."""
    parts = ["Chào   mọi người.", "Phần — hai, ok"]
    heard = [asr.Word(t, 0.5 + i, 1.0 + i) for i, t in enumerate("chào mọi người phần hai ok".split())]
    words = [w.as_dict() for w in asr.align_script(" ".join(parts), heard, 8.0)]
    starts, guessed = PR.slide_starts(parts, words, total=8.0, heard=[w.as_dict() for w in heard])
    assert starts[1] == pytest.approx(3.5 - PR.LEAD) and not any(guessed)


def test_slide_starts_survive_an_ad_lib_and_skipped_opening_words():
    """Slide 1 gets an extra sentence, the opening words of part 2 are skipped: align_script spreads them over the
    ad-lib, but the slide must change in the pause before the part is really spoken, and be flagged."""
    parts = ["xin chào mọi người", "phát hiện đầu tiên là trang đăng nhập", "cảm ơn"]
    said = [("xin", 0.2), ("chào", 0.5), ("mọi", 0.8), ("người", 1.1),          # part 1
            ("trước", 1.6), ("khi", 1.9), ("bắt", 2.2), ("đầu", 2.5),           # ad-lib, no pause
            ("trang", 4.0), ("đăng", 4.3), ("nhập", 4.6),                       # part 2 after a 1.2 s pause
            ("ờ", 5.6), ("cảm", 6.0), ("ơn", 6.3)]                              # part 3, a filler first
    heard = [asr.Word(t, s, s + 0.28) for t, s in said]
    words = [w.as_dict() for w in asr.align_script(" ".join(parts), heard, 7.0)]
    assert words[4]["start"] < 3.0  # the unspoken opening words were spread over the ad-lib
    starts, guessed = PR.slide_starts(parts, words, total=7.0, heard=[w.as_dict() for w in heard])
    assert starts[1] == pytest.approx(4.0 - PR.LEAD)
    assert starts[2] == pytest.approx(6.0 - PR.LEAD)
    assert guessed == [False, True, False]


def test_clean_starts_keeps_order_and_minimum():
    s = PR.clean_starts([3.0, 1.0, 1.1, 9.9], total=10.0)
    assert s[0] == 0.0
    assert all(b - a >= PR.MIN_SLIDE - 1e-9 for a, b in zip(s, s[1:]))
    assert s[-1] <= 10.0 - PR.MIN_SLIDE + 1e-9


# ------------------------------------------------------------------ slides, voice, bubble


def test_load_slides_pdf_pictures_markdown_and_pptx(media, tmp_path):
    frames, script = PR.load_slides({"slides": str(media["pdf"])}, tmp_path / "a", 1920, 1080)
    assert len(frames) == 3 and script == []
    im = Image.open(frames[1])
    assert im.size == (1920, 1080)
    assert im.getpixel((960, 540))[1] > 150 and im.getpixel((10, 540)) == (0, 0, 0)  # 4:3 page letterboxed
    frames, _ = PR.load_slides({"slides": [str(x) for x in media["pics"]]}, tmp_path / "b", 1280, 720)
    assert len(frames) == 3
    frames, _ = PR.load_slides({"slides": str(media["dir"])}, tmp_path / "c", 1280, 720)
    assert len(frames) == 6  # the folder holds the PDF (3 pages) and the 3 pictures
    md = "# Mở đầu\n- ý một\n> Xin chào mọi người\n# Kết\n> Cảm ơn"
    frames, script = PR.load_slides({"slides_markdown": md}, tmp_path / "d", 1280, 720)
    assert len(frames) == 2 and script == ["Xin chào mọi người", "Cảm ơn"]
    _, script = PR.load_slides({"slides_markdown": "# A\n- x\n# B"}, tmp_path / "e", 1280, 720)
    assert script == []  # narration invented from bullets is not what the owner said
    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(b"x")
    with pytest.raises(ValueError, match="PDF"):
        PR.load_slides({"slides": str(pptx)}, tmp_path / "f", 1280, 720)


def test_present_fx_never_changes_pace_and_ignores_profile():
    fx = PR.present_fx({"pace": 1.3, "pitch": -2})
    assert fx.pace == 1.0 and fx.pitch == -2
    assert PR.present_fx(None).pitch == 0.0


def test_process_voice_keeps_the_recording_length(media, tmp_path):
    out = PR.process_voice(media["rec"], tmp_path / "voice.wav", fx=VoiceFX(pitch=-2, warmth=0.5), denoise=True,
                           seconds=6.0)
    x = A.load(out)
    assert len(x) == 6 * A.SR
    assert np.abs(x[: int(5 * A.SR)]).max() > 0.05  # the tone survived denoise + shaping


def test_bubble_sits_above_the_subtitles():
    x, y, size = PR.bubble_box(1920, 1080, corner="bottom-right", size_frac=0.3, subtitles_on=True)
    assert x + size < 1920 and y + size <= 1080 - 70 - 2 * 58  # above two subtitle lines
    x2, y2, _ = PR.bubble_box(1920, 1080, corner="top-left", size_frac=0.3, subtitles_on=True)
    assert x2 < 100 and y2 < 100


# ------------------------------------------------------------------ the job


def _fake_asr(monkeypatch, text, start=0.4, step=0.35):
    monkeypatch.setattr(PR.asr, "transcribe", lambda src, **kw: {
        "text": text, "language": "vi", "words": [asr.Word(w["text"], w["start"], w["end"]) for w in _words(text, start, step)]})


@pytest.mark.parametrize("layout", ["bubble", "slides", "recording"])
def test_run_renders_every_layout(media, monkeypatch, layout):
    script = "Chào mọi người\n---\nphần hai của bài\n---\ncảm ơn đã nghe"
    _fake_asr(monkeypatch, "chào mọi người phần hai của bài cảm ơn đã nghe")
    r = PR.run({"recording": str(media["rec"]), "slides": str(media["pdf"]), "script": script, "layout": layout,
                "music": str(media["music"]), "fx": {"pitch": -1}}, lambda p, m: None, f"t-{layout}")
    st = _streams(r["video"])
    assert set(st) >= {"video", "audio"}
    assert r["duration"] == pytest.approx(6.0, abs=0.15)
    assert st["audio"] == pytest.approx(st["video"], abs=0.15)  # the voice/music does not stop before the picture
    assert r["report"]["layout"] == layout
    if layout == "recording":
        assert r["slides"] == []
    else:
        assert r["report"]["slide_timing"] == "script"
        assert [s["first_words"].split()[0] for s in r["slides"]] == ["Chào", "phần", "cảm"]
        assert r["slides"][1]["start"] == pytest.approx(0.4 + 3 * 0.35 - PR.LEAD, abs=0.01)
    assert r["title"] == "rec"
    assert "plan" not in r  # restage / revoice / resubtitle need a plan: not offered for presentations


def test_run_audio_only_recording_falls_back_to_slides(media, monkeypatch):
    _fake_asr(monkeypatch, "một hai ba bốn")
    r = PR.run({"recording": str(media["voice_only"]), "slides": [str(x) for x in media["pics"]]},
               lambda p, m: None, "t-audio")
    assert r["report"]["layout"] == "slides"
    assert r["report"]["slide_timing"] == "even"
    assert any("chỉ có tiếng" in w for w in r["report"]["warnings"])
    assert [s["start"] for s in r["slides"]] == pytest.approx([0, 4 / 3, 8 / 3], abs=0.01)


def test_run_manual_times_and_mismatch_warning(media, monkeypatch):
    _fake_asr(monkeypatch, "một hai")
    r = PR.run({"recording": str(media["rec"]), "slides": str(media["pdf"]), "slide_times": [0, 2.5, 4],
                "burn_subtitles": False}, lambda p, m: None, "t-manual")
    assert r["report"]["slide_timing"] == "manual"
    assert [s["start"] for s in r["slides"]] == [0, 2.5, 4]
    r = PR.run({"recording": str(media["rec"]), "slides": str(media["pdf"]), "script": "chỉ một đoạn"},
               lambda p, m: None, "t-mismatch")
    assert r["report"]["slide_timing"] == "even"
    assert any("1 đoạn" in w for w in r["report"]["warnings"])


def test_run_rejects_bad_input(media, tmp_path):
    with pytest.raises(ValueError, match="recording"):
        PR.run({"slides": str(media["pdf"])}, lambda p, m: None, "t-bad1")
    with pytest.raises(ValueError, match="layout"):
        PR.run({"recording": str(media["rec"]), "slides": str(media["pdf"]), "layout": "pip"}, lambda p, m: None, "t-bad2")
    with pytest.raises(ValueError, match="slides"):
        PR.run({"recording": str(media["rec"])}, lambda p, m: None, "t-bad3")


def test_late_audio_stays_on_the_lips(media, tmp_path):
    """A file whose sound starts after its picture: decoding the audio alone drops that gap; the voice must not
    run ahead of the camera."""
    x = np.zeros(A.SR * 3, dtype=np.float32)
    x[A.SR: A.SR + 600] = 0.8  # one click at 1.0 s of the audio
    wav = A.save(tmp_path / "click.wav", x)
    late = tmp_path / "late.mp4"
    A.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=3", "-itsoffset", "0.2", "-i", str(wav),
                  "-map", "0:v", "-map", "1:a", "-c:v", "h264_videotoolbox", "-b:v", "1M", "-c:a", "aac", str(late)])
    info = PR.probe(late)
    assert info["audio_offset"] > 0.1
    y = A.load(PR.process_voice(late, tmp_path / "v.wav", fx=VoiceFX(), denoise=False, seconds=info["duration"],
                                offset=info["audio_offset"]))
    assert abs(int(np.argmax(np.abs(y))) / A.SR - 1.2) < 0.03  # the click 0.2 s into the picture's timeline


def test_browser_webm_without_duration(media, monkeypatch, tmp_path):
    webm = tmp_path / "browser.webm"
    with webm.open("wb") as f:  # piped output: no duration in the header, like MediaRecorder
        subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x240:rate=30:duration=3", "-f", "lavfi", "-i", "sine=f=220:duration=3",
                        "-c:v", "libvpx", "-deadline", "realtime", "-c:a", "libopus", "-f", "webm", "pipe:1"],
                       stdout=f, check=True)
    assert PR.probe(webm)["duration"] == 0
    _fake_asr(monkeypatch, "một hai")
    r = PR.run({"recording": str(webm), "slides": str(media["pdf"])}, lambda p, m: None, "t-webm")
    assert r["duration"] == pytest.approx(3.0, abs=0.15) and r["title"] == "browser"
