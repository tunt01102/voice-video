"""Phòng quay thuyết trình: a live take + its click timeline → a Full HD video (docs/SDD-live-studio.md).

Synthetic fixtures only (ffmpeg test pattern + sine tone, Pillow slides): recordings are biometric data."""

import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from mediaforge import asr, export
from mediaforge import audio as A
from mediaforge import present as PR
from mediaforge.config import settings

COLOURS = [(220, 30, 30), (30, 200, 30), (30, 30, 220)]


def _size(path):
    out = subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(path)], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"]


def _streams(path):
    out = subprocess.run([settings.ffprobe, "-v", "error", "-show_entries", "stream=codec_type,duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return {s["codec_type"]: float(s.get("duration") or 0) for s in json.loads(out)["streams"]}


def _colour_at(video, t, tmp):
    """The slide shown at second t (the centre pixel's nearest slide colour)."""
    png = tmp / f"f{t:.2f}.png"
    A.run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(png)])
    im = Image.open(png).convert("RGB")
    px = np.array(im.getpixel((im.width // 2, im.height // 2)))
    return int(np.argmin([np.abs(px - np.array(c)).sum() for c in COLOURS]))


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("live")
    take = d / "take.webm"  # what the studio's MediaRecorder uploads: VP8/Opus webm
    A.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8", "-f", "lavfi",
                  "-i", "sine=frequency=220:duration=8", "-c:v", "libvpx", "-deadline", "realtime", "-b:v", "2M",
                  "-c:a", "libopus", str(take)])
    pics = []
    for i, c in enumerate(COLOURS):
        pics.append(d / f"slide{i + 1}.png")
        Image.new("RGB", (1600, 900), c).save(pics[-1])
    return {"dir": d, "take": take, "pics": pics}


# ------------------------------------------------------------------ the click timeline


def test_live_segments_keep_the_clicks_and_allow_going_back():
    track = [{"slide": 0, "t": 0}, {"slide": 1, "t": 2.0}, {"slide": 0, "t": 3.5}, {"slide": 1, "t": 5.0}]
    assert PR.live_segments(track, 3, 8.0) == [(0, 0.0), (1, 2.0), (0, 3.5), (1, 5.0)]
    # the times are the owner's clicks: never pushed apart like clean_starts' MIN_SLIDE
    assert PR.live_segments([{"slide": 0, "t": 0}, {"slide": 1, "t": 0.5}], 2, 8.0) == [(0, 0.0), (1, 0.5)]


def test_live_segments_clean_up():
    # flicking through 1 → 2 → 3 in 0.3 s each: only the slide that stayed is rendered
    flick = [{"slide": 0, "t": 0}, {"slide": 1, "t": 2.0}, {"slide": 2, "t": 2.2}]
    assert PR.live_segments(flick, 3, 8.0) == [(0, 0.0), (2, 2.0)]
    # going there and straight back merges into one segment of the first slide
    back = [{"slide": 0, "t": 0}, {"slide": 1, "t": 2.0}, {"slide": 0, "t": 2.1}]
    assert PR.live_segments(back, 3, 8.0) == [(0, 0.0)]
    # unsorted, a late first event, an unknown slide, a click past the end or just before it
    messy = [{"slide": 2, "t": 6}, {"slide": 1, "t": 0.2}, {"slide": 9, "t": 3}, {"slide": 0, "t": 9},
             {"slide": 0, "t": 7.9}]
    assert PR.live_segments(messy, 3, 8.0) == [(1, 0.0), (2, 6.0)]
    assert PR.live_segments([], 3, 8.0) == [(0, 0.0)]
    assert PR.live_segments([{"slide": 1, "t": 1}], 0, 8.0) == []


# ------------------------------------------------------------------ the job


def _fake_asr(monkeypatch, text="một hai ba bốn năm sáu"):
    monkeypatch.setattr(PR.asr, "transcribe", lambda src, **kw: {
        "text": text, "language": "vi",
        "words": [asr.Word(t, 0.5 + i * 1.0, 1.3 + i * 1.0) for i, t in enumerate(text.split())]})


def test_run_live_track_renders_each_click_in_full_hd(media, monkeypatch, tmp_path):
    _fake_asr(monkeypatch)
    track = [{"slide": 0, "t": 0}, {"slide": 1, "t": 2.0}, {"slide": 0, "t": 4.0}, {"slide": 2, "t": 6.0}]
    r = PR.run({"recording": str(media["take"]), "slides": [str(p) for p in media["pics"]], "slide_track": track,
                "script": "một hai\n---\nba bốn\n---\nnăm sáu", "layout": "slides", "burn_subtitles": False},
               lambda p, m: None, "t-live")
    assert r["report"]["slide_timing"] == "live"  # the script never moves a live click
    assert [(s["slide"], s["start"]) for s in r["slides"]] == [(1, 0), (2, 2.0), (1, 4.0), (3, 6.0)]
    assert r["slides"][2]["image"] == r["slides"][0]["image"]  # going back shows the same picture
    assert r["master"] == {"recording": str(media["take"]), "slide_track": track}
    assert _size(r["video"]) == (1920, 1080)
    st = _streams(r["video"])
    assert r["duration"] == pytest.approx(8.0, abs=0.15)
    assert st["audio"] == pytest.approx(st["video"], abs=0.15)
    # the frame just before and after each click (the cross-fade into a slide starts at its click)
    for t, want in [(1.8, 0), (2.45, 1), (3.8, 1), (4.45, 0), (5.8, 0), (6.45, 2), (7.8, 2)]:
        assert _colour_at(r["video"], t, tmp_path) == want, t


def test_run_live_take_without_slides_is_the_camera_full_frame(media, monkeypatch):
    _fake_asr(monkeypatch)
    r = PR.run({"recording": str(media["take"]), "layout": "recording", "slide_track": [{"slide": 0, "t": 0}]},
               lambda p, m: None, "t-live-cam")
    assert r["slides"] == [] and r["report"]["layout"] == "recording"
    assert _size(r["video"]) == (1920, 1080)  # a 720p camera still exports 1080p


# ------------------------------------------------------------------ the studio's deck endpoint


def test_present_slides_endpoint(media, tmp_path):
    from fastapi.testclient import TestClient

    from mediaforge.api import create_app

    up = settings.sub("uploads")
    pdf = up / "deck.pdf"
    pages = [Image.new("RGB", (800, 600), c) for c in COLOURS]  # a 4:3 deck: letterboxed, not cropped
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    client = TestClient(create_app())
    r = client.post("/api/present/slides", json={"slides": [str(pdf)]})
    assert r.status_code == 200, r.text
    slides = r.json()["slides"]
    assert len(slides) == 3 and all(s["url"].startswith("/files/uploads/studio-") for s in slides)
    assert Image.open(slides[1]["path"]).size == (1920, 1080)
    assert client.get(slides[0]["url"]).status_code == 200
    # outside the uploads folder, or a PowerPoint file: refused
    assert client.post("/api/present/slides", json={"slides": [str(media["pics"][0])]}).status_code == 400
    pptx = up / "deck.pptx"
    pptx.write_bytes(b"PK")
    assert client.post("/api/present/slides", json={"slides": [str(pptx)]}).status_code == 422
    assert client.post("/api/present/slides", json={"slides": [str(pdf)], "aspect": "5:4"}).status_code == 400


# ------------------------------------------------------------------ Full HD export


def test_full_hd_export_copies_1080p_and_scales_the_rest(media, tmp_path):
    assert export.fhd_size(1280, 720) == (1920, 1080) and export.fhd_size(720, 1280) == (1080, 1920)
    hd = tmp_path / "hd.mp4"
    A.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=1", "-f", "lavfi",
                  "-i", "sine=duration=1", "-c:v", "h264_videotoolbox", "-b:v", "4M", "-c:a", "aac", str(hd)])
    assert export.fhd_video_args(hd) is None  # already Full HD: stream copy, no quality loss
    assert "copy" in export.ffmpeg_args("mp4_fhd", hd, tmp_path / "o.mp4")
    small = tmp_path / "small.mp4"
    A.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=1", "-f", "lavfi",
                  "-i", "sine=duration=1", "-c:v", "h264_videotoolbox", "-b:v", "2M", "-c:a", "aac", str(small)])
    out = tmp_path / "fhd.mp4"
    A.run_ffmpeg(export.ffmpeg_args("mp4_fhd", small, out))
    assert _size(out) == (1920, 1080)
    assert "mp4_fhd" in [f["id"] for f in export.catalog()["video"]]
