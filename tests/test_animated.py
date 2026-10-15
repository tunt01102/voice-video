"""Animated slides (docs/SKILLS-REVIEW.md, SDD §7.2): layout choice, the build_visuals wiring with a fake renderer,
and — when Chromium is available — one real frame-exact render."""

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from mediaforge import audio as A
from mediaforge import pipelines as P
from mediaforge.config import settings
from mediaforge.video import animate, compose


def test_layout_of():
    assert animate.layout_of({}, 0) == "3d" and animate.layout_of({}, 1) == "bullets"
    assert animate.layout_of({"stat": {"value": "87%"}}, 2) == "stat"
    assert animate.layout_of({"layout": "stat"}, 2) == "bullets"  # a stat slide without a number
    assert animate.layout_of({"layout": "3d"}, 4) == "3d" and animate.layout_of({"layout": "weird"}, 3) == "bullets"


def test_colors_follow_theme():
    c = animate.colors("block8")
    assert c["accent"] == "#00c88c" and c["bg1"] == "#0a0c10"
    assert animate.colors("nope") == animate.colors("midnight")


def test_fit_clips_allows_the_crossfade_overlap(monkeypatch):
    monkeypatch.setattr(A, "probe_duration", lambda f: 4.5)
    vis = [compose.Scene("a.mp4", 4.0, kind="video", fill="hold")]
    report = {}
    P.fit_clips(vis, report)
    assert report["clip_fit"] == [] and "warnings" not in report


@pytest.fixture
def fake_renderer(monkeypatch):
    calls = []

    class R:
        def scene(self, out, **kw):
            calls.append({"out": out, **kw})
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(str(kw["seconds"]))  # the fake clip records its length for probe_duration
            return out

    @contextmanager
    def renderer(w, h):
        calls.append(("open", w, h))
        yield R()

    monkeypatch.setattr(animate, "renderer", renderer)
    monkeypatch.setattr(animate, "available", lambda: True)
    monkeypatch.setattr(A, "probe_duration", lambda f: float(Path(f).read_text()))
    return calls


def plan():
    return {"title": "Deck", "scenes": [
        {"narration": "a", "slide_title": "One", "bullets": ["x"], "visual_prompt": ""},
        {"narration": "b", "slide_title": "Two", "bullets": ["y", "z"], "visual_prompt": "", "layout": "stat",
         "stat": {"value": "42", "label": "answers"}}]}


def test_build_visuals_animated(tmp_path, fake_renderer):
    report = {}
    vis = P.build_visuals({"slides_engine": "animated", "cards": False, "theme": "ocean"}, plan(), tmp_path, [3.0, 5.0],
                          kind="explainer", aspect="16:9", style="minimal", report=report, progress=lambda *a: None)
    opened = [c for c in fake_renderer if isinstance(c, tuple)]
    scenes = [c for c in fake_renderer if isinstance(c, dict)]
    assert opened == [("open", 1920, 1080)]  # one browser for the whole deck
    assert [s["layout"] for s in scenes] == ["3d", "stat"] and scenes[1]["stat"]["value"] == "42"
    assert [s["seconds"] for s in scenes] == [3.5, 5.5] and scenes[0]["theme"] == "ocean"
    assert scenes[0]["counter"] == "01 / 02" and scenes[1]["progress"] == (3 / 8, 1.0)
    assert [(v.kind, v.fill, v.duration) for v in vis] == [("video", "hold", 3.0), ("video", "hold", 5.0)]


def test_build_visuals_reuses_fitting_clips(tmp_path, fake_renderer, monkeypatch):
    (tmp_path / "slides").mkdir()
    (tmp_path / "slides" / "slide_01.mp4").write_text("3.5")
    (tmp_path / "slides" / "slide_02.mp4").write_text("9.0")
    P.build_visuals({"slides_engine": "animated", "cards": False}, plan(), tmp_path, [3.0, 5.0], kind="explainer",
                    aspect="16:9", style="minimal", report={}, progress=lambda *a: None, reuse=True)
    assert [Path(c["out"]).name for c in fake_renderer if isinstance(c, dict)] == ["slide_02.mp4"]  # slot changed


def test_static_is_the_default(tmp_path, fake_renderer):
    vis = P.build_visuals({"cards": False}, plan(), tmp_path, [3.0, 5.0], kind="explainer", aspect="16:9",
                          style="minimal", report={}, progress=lambda *a: None)
    assert not fake_renderer and all(v.kind == "image" for v in vis)


def test_unavailable_falls_back_with_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(animate, "available", lambda: False)
    report = {}
    vis = P.build_visuals({"slides_engine": "animated", "cards": False}, plan(), tmp_path, [3.0, 5.0], kind="explainer",
                          aspect="16:9", style="minimal", report=report, progress=lambda *a: None)
    assert all(v.kind == "image" for v in vis) and "playwright" in report["warnings"][0]


def _chromium() -> bool:
    if not animate.available() or not Path(settings.ffmpeg).exists() and not shutil.which(settings.ffmpeg):
        return False
    try:
        with animate.renderer(64, 64):
            return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _chromium(), reason="headless Chromium / ffmpeg not available")
def test_real_render_is_exact_length(tmp_path):
    with animate.renderer(320, 180) as r:
        out = r.scene(tmp_path / "s.mp4", title="Xin chào thế giới", bullets=["một", "hai"], seconds=1.0,
                      layout="3d", shape="torus", fps=30)
    assert abs(A.probe_duration(out) - 1.0) < 0.05
