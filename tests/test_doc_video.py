"""Imported-document pictures on video scenes, chibi prompting, figure frames (LLM stubbed, no models)."""

from PIL import Image

from mediaforge import image, llm, pipelines as P
from mediaforge.video import slides


def _imgs(tmp_path, n):
    out = []
    for i in range(n):
        p = tmp_path / f"im{i + 1}.png"
        Image.new("RGB", (400, 300), (40 * i, 100, 200)).save(p)
        out.append(str(p))
    return out


def test_script_pictures_land_on_the_scene_after_their_marker():
    script = "Một. Hai.\n\n[ảnh 1]\n\nBa. Bốn.\n\n[ảnh 2]\n[ảnh 3]\n\nNăm.\n\n[ảnh 4]"
    got = P.script_scenes(script, target_scene_seconds=0.1, lang="vi")  # one sentence per scene
    assert [t for t, _ in got] == ["Một.", "Hai.", "Ba.", "Bốn.", "Năm."] and got[0] == ("Một.", None)
    by_text = dict(got)
    assert by_text["Ba."] == 1 and by_text["Năm."] == 3
    # [ảnh 2] had no text before [ảnh 3]: it goes on the previous picture-less scene; [ảnh 4] (trailing) likewise.
    assert by_text["Bốn."] == 2
    assert "[ảnh" not in " ".join(t for t, _ in got)
    assert sorted(n for _, n in got if n) == [1, 2, 3]  # 4 is dropped: no picture-less scene left before it


def test_make_plan_script_maps_images_and_never_speaks_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "visual_plan_for", lambda parts, style, kind="narrated": {"prompts": ["vp"] * len(parts), "moods": [None] * len(parts), "art_direction": {}})
    imgs = _imgs(tmp_path, 2)
    p = {"script": "Mở đầu.\n\n[ảnh 2]\n\nThân bài.", "doc_images": imgs, "lang": "vi"}
    plan, lang = P.make_plan(p, "narrated", style="chibi", seconds=30)
    assert lang == "vi"
    assert [(s["narration"], s["image"]) for s in plan["scenes"]] == [("Mở đầu.", None), ("Thân bài.", imgs[1])]


def test_brief_images_are_validated(tmp_path, monkeypatch):
    imgs = _imgs(tmp_path, 2)
    seen = {}

    def fake(prompt, timeout=0):
        seen.setdefault("prompt", prompt)  # the planner's prompt (a self-critique call may follow)
        return {"title": "T", "scenes": [
            {"narration": "A [ảnh 1] mở đầu.", "visual_prompt": "x", "image": 1},
            {"narration": "B.", "visual_prompt": "x", "image": 1},      # duplicate → dropped
            {"narration": "C.", "visual_prompt": "x", "image": 9},      # out of range → dropped
            {"narration": "D.", "visual_prompt": "x", "image": "2"}]}, "stub"

    monkeypatch.setattr(llm, "complete_json", fake)
    plan, _ = P.make_plan({"brief": "Tài liệu.\n[ảnh 1]\nTiếp.\n[ảnh 2]", "doc_images": imgs, "lang": "vi"},
                          "narrated", style="chibi", seconds=30)
    assert [s["image"] for s in plan["scenes"]] == [imgs[0], None, None, imgs[1]]
    assert plan["scenes"][0]["narration"] == "A mở đầu."
    assert "2 pictures" in seen["prompt"] and "chibi cartoon characters" in seen["prompt"]


def test_slides_marker_sets_slide_image(tmp_path):
    md = "# Một\n- ý\n[ảnh 1]\n> Lời một.\n# Hai\n> Lời hai."
    s = slides.parse_markdown_slides(md)
    assert s[0]["image"] == 1 and "image" not in s[1]


def test_figure_frame_fits_without_cropping(tmp_path):
    src = tmp_path / "tall.png"
    Image.new("RGB", (300, 900), (255, 0, 0)).save(src)
    out = slides.figure_frame(src, tmp_path / "f.png", width=1920, height=1080, title="Sơ đồ")
    im = Image.open(out).convert("RGB")
    assert im.size == (1920, 1080)
    red = [y for y in range(1080) if im.getpixel((960, y))[0] > 240 and im.getpixel((960, y))[1] < 20]
    assert red and red[0] > 60 and red[-1] < 1080 - 50  # whole figure inside the frame, below the title


def test_chibi_style_preset():
    assert "chibi" in image.STYLE_PRESETS and "chibi" in P.style_hint("chibi")
    assert "chibi" not in P.style_hint("cinematic")
