"""Motion videos (docs/SDD-motion.md): brief clamping, variables, styles, the brief gate, and — when Chromium is
available — a real two-scene render plus one spreadsheet row."""

import json
from pathlib import Path

import pytest

from mediaforge import motion
from mediaforge import pipelines as P
from mediaforge.video import animate

BRIEF = {
    "title": "Cold brew {{product}}", "summary": "Giới thiệu món mới.", "music_mood": "jazz",
    "variables": [
        {"id": "headline", "label": "Tiêu đề", "type": "text", "value": "Cà phê ủ lạnh mới"},
        {"id": "Price", "label": "Giá", "type": "text", "value": "45.000đ"},
        {"id": "product", "label": "Tên món", "type": "text", "value": "Đêm"},
        {"id": "accent", "label": "Màu nhấn", "type": "color", "value": "#ff0000"},
        {"id": "bad_colour", "label": "Màu sai", "type": "color", "value": "red"},
        {"id": "headline", "label": "trùng", "type": "text", "value": "x"},
    ],
    "scenes": [
        {"kind": "hook", "seconds": 3, "headline": "{{headline}}", "sub": "Món {{product}}", "note": "Chữ bật lên"},
        {"kind": "chart", "seconds": 4, "headline": "Không số", "bars": [{"label": "A", "value": "nhiều"}]},
        {"kind": "image", "seconds": 3, "image": 3, "headline": "Ảnh"},
        {"kind": "explode", "seconds": 3, "headline": "Lạ {{unknown}}"},
        {"kind": "list", "seconds": 3, "headline": "Vì sao", "items": []},
        {"kind": "stat", "seconds": 3},
        {"kind": "cta", "seconds": 4, "headline": "Đặt ngay", "button": "Chỉ {{price}}"},
    ],
}


def test_normalise_clamps_to_what_the_renderer_draws():
    b = motion.normalise(BRIEF, seconds=20, images=1)
    assert [v["id"] for v in b["variables"]] == ["headline", "price", "product", "accent"]  # slugged, deduped, bad colour out
    kinds = [s["kind"] for s in b["scenes"]]
    # chart without numbers, image out of range, unknown kind, empty list → title; a stat with no value too; none dropped
    assert kinds == ["hook", "title", "title", "title", "title", "cta"]
    assert b["scenes"][3]["headline"] == "Lạ"  # undeclared {{unknown}} removed
    assert any("unknown" in w for w in b["warnings"])
    assert b["scenes"][-1]["button"] == "Chỉ {{price}}"
    assert b["music_mood"] is None  # not a library mood
    assert all(motion.MIN_SCENE <= s["seconds"] <= motion.MAX_SCENE for s in b["scenes"])
    assert abs(sum(s["seconds"] for s in b["scenes"]) - 20) < 0.5


def test_normalise_rejects_an_empty_brief():
    with pytest.raises(motion.BriefError):
        motion.normalise({"scenes": [{"kind": "title"}]}, seconds=10, images=0)


def test_values_by_id_or_label_and_empty_keeps_default():
    b = motion.normalise(BRIEF, seconds=20, images=0)
    vals = motion.values_of(b, {"Giá": "39.000đ", "headline": "", "accent": "blue", "nope": "x"})
    assert vals["price"] == "39.000đ" and vals["headline"] == "Cà phê ủ lạnh mới" and vals["accent"] == "#ff0000"
    sc = motion.resolve_scene(b["scenes"][-1], vals)
    assert sc["button"] == "Chỉ 39.000đ"
    assert motion.fill("{{product}} · {{missing}}", vals) == "Đêm ·"


def test_color_variable_named_after_a_role_overrides_the_style():
    b = motion.normalise(BRIEF, seconds=20, images=0)
    st = motion.style_of("giay-muc")
    cols = motion.tokens(st, motion.values_of(b, {"accent": "#00ff00"}), b)
    assert cols["accent"] == "#00ff00" and cols["bg"] == st["colors"]["bg"]


def test_styles_and_aspects():
    assert motion.style_of("nope")["id"] == motion.DEFAULT_STYLE and motion.style_of("auto")["id"] == motion.DEFAULT_STYLE
    for s in motion.styles():
        assert s["motion"] in motion.TEXT_FX and set(s["fonts"]) == {"head", "body", "label"}
        assert {"bg", "ink", "accent", "accent2", "surface", "muted"} <= set(s["colors"])
    assert all(w % 2 == 0 and h % 2 == 0 for w, h in motion.ASPECTS.values())  # videotoolbox needs even sizes


def test_brief_prompt_offers_styles_only_on_auto():
    st = motion.style_of(None)
    auto = motion.brief_prompt({"prompt": "x", "style": "auto"}, st, [], False)
    fixed = motion.brief_prompt({"prompt": "x", "style": "khoi-mau"}, motion.style_of("khoi-mau"), [], False)
    assert "khoi-mau (" in auto and '"style"' in auto
    assert "already fixed" in fixed and "giay-muc (" not in fixed


def test_brief_gate_waits_then_owner_edits_apply(tmp_path, monkeypatch):
    p = {"brief": BRIEF, "seconds": 20, "review": ["brief"], "style": "khoi-mau", "music": "none"}
    with pytest.raises(P.AwaitApproval) as w:
        motion.run(p, lambda *_: None, job_id="gate-test")
    res = w.value.result
    assert res["stage"] == "brief" and res["style"] == "khoi-mau" and Path(res["out"], "brief.json").exists()
    edited = {**res["brief_data"], "title": "Mới", "scenes": res["brief_data"]["scenes"][:2]}
    b = motion.apply_brief_edits(Path(res["out"]), edited)
    assert b["title"] == "Mới" and len(b["scenes"]) == 2 and b["style"] == "khoi-mau"
    assert json.loads(Path(res["out"], "brief.json").read_text())["title"] == "Mới"


@pytest.mark.skipif(not animate.available(), reason="Playwright/Chromium not installed")
def test_real_render_with_a_spreadsheet_row():
    brief = {"title": "T", "variables": [{"id": "price", "label": "Giá", "type": "text", "value": "45.000đ"}],
             "scenes": [{"kind": "stat", "seconds": 2, "value": "1.200+", "label": "khách"},
                        {"kind": "cta", "seconds": 2, "headline": "Đặt ngay", "button": "Chỉ {{price}}"}]}
    r = motion.run({"brief": brief, "aspect": "4:5", "music": "none", "style": "keo-ngot",
                    "rows": [{"name": "Giảm giá", "Giá": "39.000đ"}]}, lambda *_: None, job_id="render-test")
    assert Path(r["video"]).exists() and abs(r["duration"] - 4) < 0.7
    assert r["versions"][0]["name"] == "giam_gia" and r["versions"][0]["values"]["price"] == "39.000đ"
    assert Path(r["versions"][0]["video"]).exists()
    # the stat scene does not read the variable: drawn once, reused by the row (3 clips, not 4)
    assert len(list(Path(r["video"]).parent.glob("scenes/*.mp4"))) == 3
    from mediaforge import audio as A
    import subprocess
    from mediaforge.config import settings
    wh = subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", r["video"]], capture_output=True, text=True).stdout.strip()
    assert wh.startswith("1080,1350")
    assert A.probe_duration(r["versions"][0]["video"]) > 3


def test_auto_style_comes_from_the_llm_brief(monkeypatch):
    seen = {}

    def fake(prompt, system=None, **kw):
        seen["prompt"] = prompt
        return {**BRIEF, "style": "den-vang"}, "fake"

    monkeypatch.setattr(motion.llm, "complete_json", fake)
    with pytest.raises(P.AwaitApproval) as w:
        motion.run({"prompt": "quảng cáo nước hoa", "style": "auto", "seconds": 15, "review": ["brief"]},
                   lambda *_: None, job_id="auto-style")
    assert w.value.result["style"] == "den-vang" and w.value.result["brief_data"]["llm"] == "fake"
    assert "quảng cáo nước hoa" in seen["prompt"]


def test_a_value_may_name_another_variable():
    b = motion.normalise({"variables": [{"id": "brand", "label": "Quán", "value": "Mộc"},
                                        {"id": "button", "label": "Nút", "value": "Ghé {{brand}} {{button}}"}],
                          "scenes": [{"kind": "cta", "headline": "x", "button": "{{button}}"}]}, seconds=5, images=0)
    assert motion.values_of(b)["button"] == "Ghé Mộc"
    assert motion.values_of(b, {"Quán": "Lá"})["button"] == "Ghé Lá"


@pytest.mark.skipif(not animate.available(), reason="Playwright/Chromium not installed")
def test_picture_scene_keeps_its_type_size_in_portrait(tmp_path):
    from PIL import Image
    pic = tmp_path / "p.png"
    Image.new("RGB", (1600, 1000), (200, 80, 40)).save(pic)
    files = {"img1": str(pic), "logo": str(pic)}
    st = motion.style_of("neon-dem")
    W, H = motion.ASPECTS["9:16"]
    with animate.renderer(W, H) as r:
        r.files = files
        for sc in ({"kind": "image", "image": 1, "headline": "Ly cold brew trên quầy gỗ"},
                   {"kind": "cta", "headline": "Đặt ngay", "button": "Mua", "sub": "x.vn"}):
            d = motion.scene_data(sc, st, st["colors"], index=0, count=1, width=W, height=H, seconds=3, files=files)
            r.page.goto(f"{animate.ORIGIN}/motion.html")
            r.page.wait_for_function("window.__ready === true")
            r.page.evaluate("d => window.__setup(d)", d)
            u = r.page.evaluate("() => document.querySelector('#c').style.getPropertyValue('--u')")
            assert u == "", f"{sc['kind']}: content shrunk to --u={u}"  # fits at full size: the fit loop never ran
            assert r.page.evaluate("() => [...document.images].every(i => i.complete && i.naturalWidth > 0)")


# ------------------------------------------------------------------ phase 2: own looks, palette from a picture

def test_own_look_is_clamped_saved_and_usable(monkeypatch, tmp_path):
    monkeypatch.setattr(motion, "_own_file", lambda: tmp_path / "styles.json")
    s = motion.save_style({"name": "Quán Mộc", "base": "giay-muc", "texture": "lasers", "radius": 999,
                           "fonts": {"head": "Comic Sans MS", "body": "Georgia"},
                           "colors": {"bg": "#101010", "ink": "#151515", "accent": "#ff8800", "surface": "nope"}})
    assert s["id"] == "rieng-quan-moc" and s["own"]
    assert s["texture"] == motion.STYLES["giay-muc"]["texture"] and s["radius"] == 40  # unknown value → base, clamped
    assert s["fonts"] == {"head": "Baskerville", "body": "Georgia", "label": "Courier New"}  # unchecked font refused
    assert s["colors"]["surface"] == motion.STYLES["giay-muc"]["colors"]["surface"]
    assert motion.contrast(s["colors"]["ink"], s["colors"]["bg"]) >= 4.5  # near-black ink on black bg made readable
    assert motion.style_of("rieng-quan-moc")["name"] == "Quán Mộc"
    assert any(x["id"] == "rieng-quan-moc" and x["own"] for x in motion.styles())
    # a second save with the same name gets its own id; saving with the id updates in place
    assert motion.save_style({"name": "Quán Mộc"})["id"] == "rieng-quan-moc-2"
    assert motion.save_style({"id": "rieng-quan-moc", "name": "Mộc 2"})["id"] == "rieng-quan-moc"
    assert motion.style_of("rieng-quan-moc")["name"] == "Mộc 2"
    # the LLM may pick it, and a brief naming it keeps it
    assert "rieng-quan-moc (" in motion.brief_prompt({"prompt": "x", "style": "auto"}, motion.style_of(None), [], False)
    assert motion.normalise({**BRIEF, "style": "rieng-quan-moc"}, seconds=10, images=0)["style"] == "rieng-quan-moc"
    # built-ins can't be deleted or overwritten; a deleted look falls back to the default
    assert not motion.delete_style("neon-dem")
    assert motion.save_style({"id": "neon-dem", "name": "x"})["id"].startswith(motion.OWN_PREFIX)
    assert motion.delete_style("rieng-quan-moc") and motion.style_of("rieng-quan-moc")["id"] == motion.DEFAULT_STYLE


def test_palette_from_a_picture_assigns_readable_roles(tmp_path):
    from PIL import Image, ImageDraw
    pic = tmp_path / "brand.png"
    im = Image.new("RGB", (400, 300), (250, 246, 238))  # cream page, mostly
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 400, 40), fill=(20, 40, 90))      # navy header
    d.rectangle((20, 200, 160, 260), fill=(230, 60, 40))  # red-orange button
    d.rectangle((200, 200, 300, 260), fill=(30, 170, 120))  # green badge
    im.save(pic)
    s = motion.palette_from_image(pic)
    c = s["colors"]
    assert motion.contrast(c["bg"], "#faf6ee") < 1.1  # bg = the dominant cream
    assert motion.contrast(c["ink"], c["bg"]) >= 4.5
    assert motion.contrast(c["bg"], c["accent"]) >= 3  # button text (bg colour) readable on the accent
    r, g, b = motion._rgb(c["accent"])
    assert r > g and r > b  # the most saturated swatch: the red-orange
    r2, g2, b2 = motion._rgb(c["accent2"])
    assert g2 > r2  # then the green
    assert s["base"] == "doanh-nghiep" and len(s["palette"]) >= 4 and "id" not in s  # a draft, not saved


def test_dark_picture_drafts_a_dark_look(tmp_path):
    from PIL import Image
    pic = tmp_path / "dark.png"
    Image.new("RGB", (100, 100), (12, 14, 30)).save(pic)
    s = motion.palette_from_image(pic, "Tối")
    assert s["name"] == "Tối" and s["base"] == "neon-dem" and motion.contrast(s["colors"]["ink"], s["colors"]["bg"]) >= 4.5


def test_gif_export_is_small_silent_and_looping(tmp_path):
    import subprocess
    from mediaforge import audio as A
    from mediaforge import export
    from mediaforge.config import settings
    src = tmp_path / "v.mp4"
    A.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1080x1350:rate=30:duration=1.5", "-f", "lavfi", "-i",
                  "anullsrc=r=48000:cl=mono", "-shortest", "-c:v", "h264_videotoolbox", "-pix_fmt", "yuv420p", str(src)])
    out = tmp_path / "v.gif"
    A.run_ffmpeg(export.ffmpeg_args("gif", src, out))
    st = json.loads(subprocess.run([settings.ffprobe, "-v", "error", "-show_entries", "stream=codec_name,width,height,codec_type",
                                    "-of", "json", str(out)], capture_output=True, text=True).stdout)["streams"]
    assert [s["codec_type"] for s in st] == ["video"] and st[0]["codec_name"] == "gif"
    assert (st[0]["width"], st[0]["height"]) == (576, 720)  # portrait: the long side capped at 720
    assert "gif" in export.formats_for({"video": str(src)})


@pytest.mark.skipif(not animate.available(), reason="Playwright/Chromium not installed")
def test_alpha_mov_keeps_transparency():
    import subprocess

    import numpy as np

    from mediaforge.config import settings
    brief = {"title": "T", "variables": [],
             "scenes": [{"kind": "title", "seconds": 1.6, "headline": "Trong suốt"},
                        {"kind": "cta", "seconds": 1.6, "headline": "Đặt ngay", "button": "Mua"}]}
    r = motion.run({"brief": brief, "aspect": "1:1", "music": "none", "style": "khoi-mau", "alpha": True},
                   lambda *_: None, job_id="alpha-test")
    mov = Path(r["alpha"])
    st = json.loads(subprocess.run([settings.ffprobe, "-v", "error", "-show_entries", "stream=codec_name,pix_fmt,width",
                                    "-of", "json", str(mov)], capture_output=True, text=True).stdout)["streams"]
    assert len(st) == 1 and st[0]["codec_name"] == "prores" and st[0]["pix_fmt"].startswith("yuva")  # no sound
    # one frame mid-scene: the corners are see-through, the words are not
    raw = subprocess.run([settings.ffmpeg, "-v", "error", "-ss", "1.0", "-i", str(mov), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(1080, 1080, 4)[:, :, 3]
    assert a[5, 5] == 0 and a.max() == 255 and 0.002 < (a > 0).mean() < 0.6
    assert Path(r["video"]).exists() and abs(motion.A.probe_duration(mov) - 3.2) < 0.4
    assert not list(mov.parent.glob("scenes/*.mov"))  # the big ProRes intermediates are gone once joined
    assert mov.stat().st_size < 60e6 * 3.2  # qscale 9, not the encoder's ~90 MB/s default
