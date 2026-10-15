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


def test_text_fx_follows_mood_unless_the_plan_says():
    assert animate.text_fx_for({"mood": "calm"}) == "blur" and animate.text_fx_for({"mood": "urgent"}) == "type"
    assert animate.text_fx_for({"mood": "playful"}) == "wave" and animate.text_fx_for({}) == "rise"
    assert animate.text_fx_for({"mood": "calm", "text_fx": "wave"}) == "wave"
    assert animate.text_fx_for({"mood": "calm", "text_fx": "explode"}) == "blur"


def test_text_is_nfc_so_splittext_keeps_diacritics_whole():
    import unicodedata
    decomposed = unicodedata.normalize("NFD", "Kiểm thử")
    assert len(animate._nfc(decomposed)) == len("Kiểm thử") < len(decomposed)


def test_markdown_directives_set_animated_fields():
    from mediaforge.video import slides
    md = """# Mở đầu
@layout bullets
@fx type
- ý một
# Doanh thu
@layout stat 87% | khách hàng quay lại
> Gần chín phần mười khách quay lại.
# Mạng lưới
@shape network
@mood calm
# Khối
@layout 3d cube
# Số
@stat 1,2 triệu
# Sai
@layout pie
@fx explode
@shape blob
@stat
"""
    s = slides.parse_markdown_slides(md)
    assert s[0]["layout"] == "bullets" and s[0]["text_fx"] == "type" and s[0]["bullets"] == ["ý một"]
    assert s[1]["layout"] == "stat" and s[1]["stat"] == {"value": "87%", "label": "khách hàng quay lại"}
    assert s[1]["narration"] == "Gần chín phần mười khách quay lại."
    assert s[2]["layout"] == "3d" and s[2]["shape"] == "network" and s[2]["mood"] == "calm"
    assert s[3]["layout"] == "3d" and s[3]["shape"] == "cube"
    assert s[4]["layout"] == "stat" and s[4]["stat"] == {"value": "1,2 triệu", "label": ""}
    assert not {"layout", "text_fx", "shape", "stat"} & set(s[5]) and len(s[5]["warnings"]) == 4
    assert "@" not in s[5]["narration"]  # directives are never read aloud
    # what the renderer then picks: @layout bullets beats the default 3D opener, @fx beats the mood
    assert animate.layout_of(s[0], 0) == "bullets" and animate.layout_of(s[5], 0) == "3d"
    assert animate.text_fx_for(s[2]) == "blur" and animate.text_fx_for(s[0]) == "type"


def test_markdown_directives_reach_the_plan():
    md = "# Một\n@layout stat 3 | bước\n> Ba bước.\n# Hai\n@layout pie\n> Hai."
    plan, lang = P.make_plan({"slides_markdown": md, "lang": "vi"}, "explainer", style="minimal", seconds=30)
    a, b = plan["scenes"]
    assert a["layout"] == "stat" and a["stat"] == {"value": "3", "label": "bước"} and "layout" not in b
    assert plan["warnings"] == ["slide 2: @layout 'pie': dùng bullets, stat, 3d"]


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
    assert [s["text_fx"] for s in scenes] == ["rise", "rise"]
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
                      layout="3d", shape="torus", text_fx="type", fps=30)
    assert abs(A.probe_duration(out) - 1.0) < 0.05


@pytest.mark.skipif(not _chromium(), reason="headless Chromium / ffmpeg not available")
def test_vertical_3d_and_text_stay_clear_of_the_subtitle_band(tmp_path):
    """9:16: subtitles sit at MarginV 520/1920 (two lines ≈ 1180–1400 px), so the 3D canvas and the text block
    must end above ~60 % of the height; long text shrinks to fit instead of running into the band."""
    W, H = 540, 960
    with animate.renderer(W, H) as r:
        r.page.goto(f"{animate.ORIGIN}/scene.html")
        r.page.wait_for_function("window.__ready === true")
        r.page.evaluate("d => window.__setup(d)", {
            "title": "Một tiêu đề rất dài để kiểm tra chữ có tự thu nhỏ khi không đủ chỗ hay không",
            "bullets": ["Ý thứ nhất khá dài, xuống hai dòng trên điện thoại", "Ý hai dài không kém, thêm vài chữ",
                        "Ý ba", "Ý bốn dài dài dài", "Ý năm"],
            "text_fx": "rise", "seconds": 2, "width": W, "height": H, "colors": animate.colors("midnight"),
            "layout": "3d", "shape": "cube", "stat": None, "background": False, "counter": "", "footer": "",
            "progress0": 0, "progress1": 0})
        r.page.evaluate("t => window.__seek(t)", 2.0)  # every text tween at rest
        box = r.page.evaluate("""() => { const c = document.querySelector('#content'), t = document.querySelector('#three');
            const last = [...c.querySelectorAll('li')].pop().getBoundingClientRect();
            return {three: t.getBoundingClientRect().bottom, top: c.getBoundingClientRect().top, last: last.bottom,
                    fits: c.scrollHeight <= c.clientHeight + 1,
                    fit: parseFloat(c.style.getPropertyValue('--u') || '0.5') / 0.5}; }""")
    band = H * 1180 / 1920
    assert box["three"] <= box["top"] and box["fits"] and box["last"] <= band, box
    assert box["fit"] >= 0.6, box  # the worst case (3-line title + 5 bullets) still reads on a phone
