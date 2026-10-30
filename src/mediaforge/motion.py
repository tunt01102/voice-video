"""Motion videos, style first (docs/SDD-motion.md): pick a look, describe the video, confirm a brief, render.

A native take on the Motion Studio skill (scripts/motion-studio, built on HyperFrames): the same process — style →
description → brief the owner confirms → build → retype words in variables → export, in 16:9, 9:16, 1:1 or 4:5 —
on MediaForge's own renderer (video/scene_web/motion.html, anime.js + GSAP, stepped frame by frame in Chromium by
video/animate.py) and composer, with no voice: text, numbers, charts, logos and pictures that move, plus music.

Every word, number or colour the owner may change is a brief variable; scene text refers to it as {{id}}. A re-render
with other values (or one MP4 per spreadsheet row) only re-fills the brief; the LLM is never asked again.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from mediaforge import audio as A
from mediaforge import llm, music
from mediaforge.config import settings

Progress = Callable[[float, str], None]

ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
KINDS = ("hook", "title", "stat", "list", "chart", "quote", "image", "cta")
VAR_TYPES = ("text", "number", "color")
TRANSITION = 0.5  # xfade between scenes (compose.py timeline contract: each clip is its slot + T long)
MIN_SCENE, MAX_SCENE = 1.6, 8.0
LANGS = {"vi": "Vietnamese", "en": "English"}

# Our own looks (not HyperFrames' presets). Fonts are installed macOS faces checked to cover every Vietnamese
# letter (SDD-motion §4: Chromium renders offline, so web fonts cannot load). Token roles: bg, surface, ink, muted,
# accent, accent2; head/body/label fonts; case of headlines; radius (px at 1080); border width; shadow; texture and
# decoration drawn behind the content; motion character; transition between scenes.
STYLES: dict[str, dict[str, Any]] = {
    "neon-dem": {
        "name": "Đêm neon", "mood": "Hiện đại, công nghệ, năng động",
        "when": "Hợp với app, SaaS, sản phẩm công nghệ, thông báo ra mắt.",
        "look": "Nền xanh đêm, lưới mảnh, quầng sáng xanh ngọc và tím, chữ Avenir Next rất đậm, góc bo vừa.",
        "colors": {"bg": "#0b1020", "surface": "#141b33", "ink": "#f4f7ff", "muted": "#8b96b8", "accent": "#3cf0c5", "accent2": "#7b5cff"},
        "fonts": {"head": "Avenir Next", "body": "Avenir Next", "label": "Monaco"}, "head_weight": 800, "case": "none",
        "radius": 18, "border": 0, "shadow": "glow", "texture": "grid", "deco": "blobs", "motion": "snappy", "transition": "fade",
    },
    "giay-muc": {
        "name": "Giấy & mực", "mood": "Chỉn chu, sâu lắng, kiểu tạp chí",
        "when": "Hợp với thương hiệu muốn vẻ tinh tế: sách, cà phê, studio, báo cáo có chiều sâu.",
        "look": "Nền giấy kem, mực nâu đen, điểm nhấn đỏ đất, tiêu đề serif Baskerville, đường kẻ mảnh, không đổ bóng.",
        "colors": {"bg": "#f4efe6", "surface": "#fffaf1", "ink": "#1d1b18", "muted": "#6f675c", "accent": "#c4512b", "accent2": "#2f4b3a"},
        "fonts": {"head": "Baskerville", "body": "Avenir Next", "label": "Courier New"}, "head_weight": 700, "case": "none",
        "radius": 0, "border": 1, "shadow": "none", "texture": "rules", "deco": "lines", "motion": "smooth", "transition": "dissolve",
    },
    "khoi-mau": {
        "name": "Khối màu", "mood": "Táo bạo, dứt khoát, nhiều đồ hoạ",
        "when": "Hợp với quảng cáo khuyến mãi, sự kiện, video mạng xã hội cần gây chú ý ngay.",
        "look": "Mảng màu cam đỏ và đen trên nền kem, sọc chéo, chữ DIN Condensed viết hoa cỡ lớn, góc vuông.",
        "colors": {"bg": "#f6f0e4", "surface": "#121212", "ink": "#121212", "muted": "#5c574f", "accent": "#ff5a36", "accent2": "#121212",
                   "surface_ink": "#f6f0e4"},
        "fonts": {"head": "DIN Condensed", "body": "Helvetica Neue", "label": "Helvetica Neue"}, "head_weight": 700, "case": "uppercase",
        "radius": 0, "border": 0, "shadow": "none", "texture": "stripes", "deco": "blocks", "motion": "snappy", "transition": "smoothleft",
    },
    "keo-ngot": {
        "name": "Kẹo ngọt", "mood": "Vui tươi, thân thiện, dễ thương",
        "when": "Hợp với đồ ăn vặt, đồ chơi, lớp học cho trẻ, quán trà sữa, sản phẩm muốn gần gũi.",
        "look": "Nền kem hồng, màu pastel hồng, xanh và vàng, viền đen dày, bóng đổ cứng, chữ Chalkboard SE, bo góc lớn.",
        "colors": {"bg": "#fff4e8", "surface": "#ffffff", "ink": "#2b2340", "muted": "#6d6383", "accent": "#ff7eb6", "accent2": "#7bdff2"},
        "fonts": {"head": "Chalkboard SE", "body": "Avenir Next", "label": "Avenir Next"}, "head_weight": 700, "case": "none",
        "radius": 28, "border": 4, "shadow": "hard", "texture": "dots", "deco": "bubbles", "motion": "bouncy", "transition": "slideup",
    },
    "doanh-nghiep": {
        "name": "Doanh nghiệp", "mood": "Điềm đạm, rõ ràng, đáng tin",
        "when": "Hợp với biểu đồ cho buổi họp, báo cáo KPI, giới thiệu dịch vụ B2B, tuyển dụng.",
        "look": "Nền trắng xám, một màu xanh cobalt và xanh ngọc làm điểm nhấn, chữ Helvetica Neue, thẻ trắng bóng đổ nhẹ.",
        "colors": {"bg": "#f5f7fb", "surface": "#ffffff", "ink": "#0f1a2e", "muted": "#5b6475", "accent": "#2453ff", "accent2": "#00b3a4"},
        "fonts": {"head": "Helvetica Neue", "body": "Helvetica Neue", "label": "Monaco"}, "head_weight": 700, "case": "none",
        "radius": 14, "border": 1, "shadow": "soft", "texture": "none", "deco": "rings", "motion": "smooth", "transition": "fade",
    },
    "den-vang": {
        "name": "Đen & vàng", "mood": "Sang trọng, tiết chế, cao cấp",
        "when": "Hợp với trang sức, nước hoa, nhà hàng, bất động sản, lời mời sự kiện.",
        "look": "Nền đen ấm, chữ ngà, điểm nhấn vàng kim, tiêu đề serif Big Caslon, nhãn viết hoa giãn chữ, khung viền mảnh.",
        "colors": {"bg": "#0c0b0a", "surface": "#17140f", "ink": "#f3ecdf", "muted": "#a39a88", "accent": "#d4af6a", "accent2": "#8a6d3b"},
        "fonts": {"head": "Big Caslon", "body": "Avenir Next", "label": "Avenir Next"}, "head_weight": 500, "case": "none",
        "radius": 0, "border": 1, "shadow": "none", "texture": "grain", "deco": "frame", "motion": "smooth", "transition": "fadeblack",
    },
    "rung-xanh": {
        "name": "Rừng xanh", "mood": "Bình yên, tự nhiên, ấm áp",
        "when": "Hợp với sản phẩm hữu cơ, sức khoẻ, du lịch, giáo dục, cộng đồng.",
        "look": "Nền kem, xanh rừng đậm và hồng phấn, tiêu đề serif Georgia, nhãn Courier New viết hoa, vòng tròn như con dấu.",
        "colors": {"bg": "#f1efe7", "surface": "#e4e8dc", "ink": "#173a2c", "muted": "#5d6d63", "accent": "#2f6b4f", "accent2": "#e8a0a8"},
        "fonts": {"head": "Georgia", "body": "Avenir Next", "label": "Courier New"}, "head_weight": 700, "case": "none",
        "radius": 10, "border": 0, "shadow": "none", "texture": "none", "deco": "rings", "motion": "smooth", "transition": "dissolve",
    },
    "lap-trinh": {
        "name": "Lập trình", "mood": "Kỹ thuật, gọn gàng, dành cho developer",
        "when": "Hợp với bản cập nhật phần mềm, ra mắt API, video hướng dẫn công nghệ, bảo mật.",
        "look": "Nền tối kiểu editor, chữ Monaco gõ từng ký tự, xanh lá và xanh dương làm điểm nhấn, khung viền mảnh.",
        "colors": {"bg": "#0d1117", "surface": "#161b22", "ink": "#e6edf3", "muted": "#7d8590", "accent": "#3fb950", "accent2": "#58a6ff"},
        "fonts": {"head": "Monaco", "body": "Helvetica Neue", "label": "Monaco"}, "head_weight": 400, "case": "none",
        "radius": 8, "border": 1, "shadow": "none", "texture": "grid", "deco": "none", "motion": "typing", "transition": "fade",
    },
}
DEFAULT_STYLE = "neon-dem"
TEXT_FX = {"snappy": "rise", "smooth": "blur", "bouncy": "wave", "typing": "type"}
# library moods (music_catalog.json) a brief may ask for
MUSIC_MOODS = ("acoustic", "ambient", "classical", "lofi", "piano")


class BriefError(ValueError):
    pass


def styles() -> list[dict[str, Any]]:
    return [{"id": k, **v} for k, v in STYLES.items()]


def style_of(style_id: str | None) -> dict[str, Any]:
    return {"id": style_id if style_id in STYLES else DEFAULT_STYLE, **STYLES.get(style_id or "", STYLES[DEFAULT_STYLE])}


def _nfc(text: Any) -> str:
    return unicodedata.normalize("NFC", str(text if text is not None else "")).strip()


def _slug(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text)).replace("đ", "d").replace("Đ", "D")
    s = re.sub(r"[̀-ͯ]", "", s).lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40]


# ------------------------------------------------------------------ brief

SYSTEM = """You are a motion designer writing the brief for a short animated video (motion graphics: words, numbers,
charts, logos and pictures that move; no people, no filmed footage, no voice-over). You plan the scenes; a renderer
draws them in a fixed visual style, so describe content, not colours or fonts."""

SCENE_DOC = """Scene kinds and their fields (all text in the on-screen language):
- hook: "headline" (2-7 punchy words), optional "sub" (a short kicker line). Opens the video.
- title: "headline", "sub".
- stat: "value" (a number with unit or symbol, e.g. "87%", "1.200+", "3x", "45.000đ"), "label" (what it measures).
- list: "headline", "items" (2-4 short lines).
- chart: "headline", "bars" ([{"label": "...", "value": "number with unit"}], 2-6 bars, same unit).
- quote: "quote" (one or two sentences), "author".
- image: "image" (1-based number of one of the owner's pictures), "headline" (caption).
- cta: "headline" (the ask), "button" (2-4 words), optional "sub" (web address, phone, date or offer).
Every scene also has "seconds" (1.6-8) and "note": one sentence in Vietnamese saying what the viewer sees."""


def brief_prompt(p: dict[str, Any], st: dict[str, Any], images: list[str], has_logo: bool) -> str:
    lang = p.get("lang") or "vi"
    seconds = int(p.get("seconds") or 20)
    lines = [
        f"Write the brief for this video: {p.get('prompt') or ''}",
        f"The one message it must land: {p['message']}" if p.get("message") else "",
        f"Offer / numbers, exactly as given: {p['offer']}" if p.get("offer") else "",
        f"Who watches and where: {p['audience']}" if p.get("audience") else "",
        f"Frame: {p.get('aspect') or '16:9'}. Length: about {seconds} seconds in total.",
        f"Words on screen in: {LANGS.get(lang, lang)}.",
        (f"Visual style (already fixed): {st['name']}: {st['mood']}." if p.get("style") in STYLES else
         'Pick the visual style that fits best and return its id as "style": ' + "; ".join(
             f"{k} ({v['mood']}; {v['when']})" for k, v in STYLES.items()) + "."),
        (f"The owner's pictures: {len(images)} (" + ", ".join(f"{i + 1} = {Path(x).name}" for i, x in enumerate(images))
         + "). Use each at most once in an image scene.") if images else "No pictures: do not use image scenes.",
        "A logo is provided; the cta scene shows it." if has_logo else "",
        SCENE_DOC,
        f"Use {max(3, min(9, round(seconds / 3.2)))} scenes or so: open with a hook, end with a cta, never two scenes "
        "of the same kind in a row. Use only numbers the owner gave; invent none (no stat or chart without them).",
        "Variables: every word, number or colour the owner might change (headline, product name, price, numbers, web "
        "address, button text, the accent colour) is a variable, and scene fields refer to it as {{id}}, alone or "
        "inside a sentence. Ids: lowercase snake_case. Labels: short Vietnamese (e.g. \"Tiêu đề\", \"Giá\"). Types: "
        "text | number | color (a color variable with id \"accent\" replaces the style's accent colour; only add it "
        "when the owner named a brand colour).",
        'Return JSON: {"title": "...", "summary": "2-3 sentences in Vietnamese: what the video says and how it '
        'flows", "music_mood": "one of ' + " | ".join(MUSIC_MOODS) + '", "variables": [{"id": "...", "label": "...", '
        '"type": "text", "value": "..."}], "scenes": [{"kind": "hook", "seconds": 3, "headline": "{{headline}}", '
        '"note": "..."}]}',
    ]
    return "\n".join(x for x in lines if x)


def make_brief(p: dict[str, Any], st: dict[str, Any], images: list[str], has_logo: bool) -> dict[str, Any]:
    data, provider = llm.complete_json(brief_prompt(p, st, images, has_logo), SYSTEM, timeout=300)
    if not isinstance(data, dict):
        raise BriefError("the brief is not a JSON object")
    data["llm"] = provider
    return data


def _num_ok(v: Any) -> bool:
    return bool(re.search(r"\d", str(v)))


def normalise(brief: dict[str, Any], *, seconds: float, images: int) -> dict[str, Any]:
    """Clamp an LLM (or owner-edited) brief to what the renderer draws; scene times sum to `seconds`."""
    warnings: list[str] = []
    variables, seen = [], set()
    for v in brief.get("variables") or []:
        if not isinstance(v, dict):
            continue
        vid = _slug(v.get("id") or v.get("label") or "")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        typ = v.get("type") if v.get("type") in VAR_TYPES else "text"
        val = _nfc(v.get("value"))
        if typ == "color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", val):
            continue
        variables.append({"id": vid, "label": _nfc(v.get("label")) or vid, "type": typ, "value": val})
    ids = {v["id"] for v in variables}

    def text(x: Any, limit: int = 140) -> str:
        s = _nfc(x)[:limit]
        for ref in re.findall(r"\{\{\s*([^}]+?)\s*\}\}", s):
            if _slug(ref) not in ids:
                warnings.append(f"bỏ biến không khai báo: {ref}")
                s = re.sub(r"\{\{\s*" + re.escape(ref) + r"\s*\}\}", "", s)
        return re.sub(r"\{\{\s*([^}]+?)\s*\}\}", lambda m: "{{" + _slug(m.group(1)) + "}}", s).strip()

    scenes = []
    for sc in brief.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        kind = sc.get("kind") if sc.get("kind") in KINDS else "title"
        out: dict[str, Any] = {"kind": kind, "note": _nfc(sc.get("note"))[:240]}
        for k in ("headline", "sub", "value", "label", "quote", "author", "button"):
            if sc.get(k):
                out[k] = text(sc[k], 220 if k == "quote" else 140)
        if kind == "list":
            out["items"] = [text(i, 90) for i in (sc.get("items") or []) if _nfc(i)][:4]
            if not out["items"]:
                out["kind"] = "title"
        if kind == "chart":
            out["bars"] = [{"label": text(b.get("label"), 40), "value": text(b.get("value"), 30)}
                           for b in (sc.get("bars") or []) if isinstance(b, dict) and _num_ok(b.get("value"))][:6]
            if len(out["bars"]) < 2:
                out["kind"] = "title"
                warnings.append("biểu đồ thiếu số liệu: đổi thành cảnh tiêu đề")
        if kind == "stat" and not out.get("value"):
            out["kind"] = "title"
        if kind == "image":
            try:
                k = int(sc.get("image"))
            except (TypeError, ValueError):
                k = 0
            if 1 <= k <= images:
                out["image"] = k
            else:
                out["kind"] = "title"
        if kind == "quote" and not out.get("quote"):
            out["kind"] = "title"
        if not any(out.get(k) for k in ("headline", "sub", "value", "quote", "items", "bars", "image")):
            continue
        try:
            out["seconds"] = float(sc.get("seconds") or 3.5)
        except (TypeError, ValueError):
            out["seconds"] = 3.5
        scenes.append(out)
    scenes = scenes[:12]
    if not scenes:
        raise BriefError("the brief has no scene the renderer can draw")
    # scene lengths: keep their proportions, fit the total, clamp each
    total = sum(s["seconds"] for s in scenes)
    k = float(seconds) / total if total > 0 else 1.0
    for s in scenes:
        s["seconds"] = round(min(MAX_SCENE, max(MIN_SCENE, s["seconds"] * k)), 2)
    mood = brief.get("music_mood") if brief.get("music_mood") in MUSIC_MOODS else None
    return {**({"style": brief["style"]} if brief.get("style") in STYLES else {}),
            "title": _nfc(brief.get("title"))[:120] or "Motion video", "summary": _nfc(brief.get("summary"))[:600],
            "music_mood": mood, "variables": variables, "scenes": scenes,
            "warnings": list(dict.fromkeys(warnings)), **({"llm": brief["llm"]} if brief.get("llm") else {})}


# ------------------------------------------------------------------ variables

def values_of(brief: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, str]:
    """The brief's defaults with overrides (by id, or by label as the dashboard or a CSV header names it)."""
    vals = {v["id"]: v["value"] for v in brief.get("variables") or []}
    by_label = {_nfc(v["label"]).lower(): v["id"] for v in brief.get("variables") or []}
    types = {v["id"]: v["type"] for v in brief.get("variables") or []}
    for key, val in (overrides or {}).items():
        vid = key if key in vals else by_label.get(_nfc(key).lower()) or _slug(key)
        if vid not in vals or val is None or str(val).strip() == "":
            continue  # an empty cell keeps the default
        val = _nfc(val)
        if types[vid] == "color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", val):
            continue
        vals[vid] = val
    # a value may name another variable ("Ghé quán {{brand}}"): one level, never itself
    return {k: re.sub(r"\{\{\s*([a-z0-9_]+)\s*\}\}", lambda m, k=k: vals.get(m.group(1), "") if m.group(1) != k else "", v).strip()
            for k, v in vals.items()}


def fill(text: str, vals: dict[str, str]) -> str:
    return re.sub(r"\{\{\s*([a-z0-9_]+)\s*\}\}", lambda m: vals.get(m.group(1), ""), text or "").strip()


def resolve_scene(sc: dict[str, Any], vals: dict[str, str]) -> dict[str, Any]:
    out = {k: (fill(v, vals) if isinstance(v, str) and k != "note" else v) for k, v in sc.items()}
    if "items" in sc:
        out["items"] = [fill(i, vals) for i in sc["items"]]
    if "bars" in sc:
        out["bars"] = [{"label": fill(b["label"], vals), "value": fill(b["value"], vals)} for b in sc["bars"]]
    return out


def tokens(st: dict[str, Any], vals: dict[str, str], brief: dict[str, Any]) -> dict[str, str]:
    """The style's colours, with any color variable named after a role (accent, bg, ink…) on top."""
    cols = dict(st["colors"])
    for v in brief.get("variables") or []:
        if v["type"] == "color" and v["id"] in cols:
            cols[v["id"]] = vals.get(v["id"], v["value"])
    return cols


# ------------------------------------------------------------------ render

def scene_data(sc: dict[str, Any], st: dict[str, Any], cols: dict[str, str], *, index: int, count: int,
               width: int, height: int, seconds: float, files: dict[str, str]) -> dict[str, Any]:
    d = {k: v for k, v in sc.items() if k not in ("note", "seconds", "image")}
    if sc.get("image"):
        d["image"] = f"/file/img{sc['image']}"
    if sc["kind"] in ("cta", "hook") and "logo" in files:
        d["logo"] = "/file/logo"
    return {"scene": d, "index": index, "count": count, "seconds": seconds, "width": width, "height": height,
            "colors": cols, "fonts": st["fonts"], "head_weight": st["head_weight"], "case": st["case"],
            "radius": st["radius"], "border": st["border"], "shadow": st["shadow"], "texture": st["texture"],
            "deco": st["deco"], "motion": st["motion"], "text_fx": TEXT_FX[st["motion"]]}


def render_version(brief: dict[str, Any], st: dict[str, Any], vals: dict[str, str], out_file: Path, *, aspect: str,
                   files: dict[str, str], cache: Path, music_file: str | None, progress: Progress) -> Path:
    """One MP4 from the brief filled with `vals`. Scene clips are cached by content, so a batch row (or a scene a
    variable does not touch) is drawn once."""
    from mediaforge.video import animate, compose

    W, H = ASPECTS[aspect]
    cols = tokens(st, vals, brief)
    scenes = brief["scenes"]
    n = len(scenes)
    T = TRANSITION if n > 1 else 0.0
    cache.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[Path, dict[str, Any], float]] = []
    for i, sc in enumerate(scenes):
        length = sc["seconds"] + (T if i < n - 1 else 0)
        data = scene_data(resolve_scene(sc, vals), st, cols, index=i, count=n, width=W, height=H, seconds=length,
                          files=files)
        key = hashlib.sha1(json.dumps([data, {k: files.get(k) for k in sorted(files)}], sort_keys=True,
                                      ensure_ascii=False).encode()).hexdigest()[:16]
        jobs.append((cache / f"{key}.mp4", data, length))
    todo = [j for j in jobs if not j[0].exists()]
    if todo:
        with animate.renderer(W, H) as r:
            r.files = dict(files)
            for k, (clip, data, length) in enumerate(todo):
                progress(0.85 * k / len(todo), f"đang dựng cảnh {k + 1}/{len(todo)}")
                tmp = clip.with_suffix(".part.mp4")
                r.render("motion.html", data, tmp, seconds=length)
                tmp.replace(clip)
    progress(0.88, "đang ghép cảnh và nhạc")
    total = sum(sc["seconds"] for sc in scenes)
    silent = out_file.with_name(out_file.stem + ".silence.wav")
    A.run_ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{max(total - 0.5, 0.5):.3f}", str(silent)])
    try:
        clips = [compose.Scene(str(c), sc["seconds"], kind="video", fill="hold", transition=st["transition"])
                 for (c, _, _), sc in zip(jobs, scenes)]
        compose.compose(clips, silent, out_file, aspect=aspect, music=music_file, music_volume=0.5, transition=T)
    finally:
        silent.unlink(missing_ok=True)
    return out_file


def materials(p: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    images = [str(x) for x in (p.get("images") or []) if x and Path(x).is_file()][:8]
    files = {f"img{i + 1}": x for i, x in enumerate(images)}
    if p.get("logo") and Path(p["logo"]).is_file():
        files["logo"] = str(p["logo"])
    return images, files


def apply_brief_edits(out: Path, edited: dict[str, Any]) -> dict[str, Any]:
    """The owner's edits at the brief gate (title, scene texts and lengths, variable defaults, dropped scenes)."""
    f = out / "brief.json"
    cur = json.loads(f.read_text("utf-8"))
    merged = {**cur, **{k: edited[k] for k in ("title", "summary", "variables", "scenes", "music_mood", "style") if k in edited}}
    seconds = sum(float(s.get("seconds") or 0) for s in merged["scenes"] if isinstance(s, dict)) or 20
    brief = normalise(merged, seconds=seconds, images=int(cur.get("images") or 0))
    brief["images"] = cur.get("images") or 0
    f.write_text(json.dumps(brief, ensure_ascii=False, indent=2))
    return brief


def run(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """params: style, prompt, message, offer, audience, aspect, seconds, lang, logo, images, music ("auto" | id |
    "none"), review (["brief"] pauses for the owner), brief (a ready brief: no LLM), variables ({id|label: value}),
    rows ([{name, id|label: value}]: one MP4 per row)."""
    from mediaforge import pipelines
    from mediaforge.video import compose

    out = settings.sub("outputs", "motion", job_id or time.strftime("%Y%m%d-%H%M%S"))
    aspect = p.get("aspect") if p.get("aspect") in ASPECTS else "16:9"
    seconds = max(5, min(90, int(p.get("seconds") or 20)))
    images, files = materials(p)
    review, approved = set(p.get("review") or []), set(p.get("approved") or [])
    t0 = time.time()

    # ---- 1. brief (kept across the approval re-run)
    bf = out / "brief.json"
    if bf.exists():
        brief = json.loads(bf.read_text("utf-8"))
    else:
        progress(0.02, "đang viết brief")
        raw = p["brief"] if isinstance(p.get("brief"), dict) else make_brief(p, style_of(p.get("style")), images, "logo" in files)
        brief = normalise(raw, seconds=sum(float(s.get("seconds") or 0) for s in raw.get("scenes") or [])
                          if isinstance(p.get("brief"), dict) else seconds, images=len(images))
        brief["images"] = len(images)
        if p.get("style") in STYLES:  # the owner's pick; "auto" leaves the brief's own
            brief["style"] = p["style"]
        brief["style"] = style_of(brief.get("style"))["id"]
        bf.write_text(json.dumps(brief, ensure_ascii=False, indent=2))
    st = style_of(brief.get("style"))
    if "brief" in review and "brief" not in approved:
        raise pipelines.AwaitApproval("brief", {"stage": "brief", "out": str(out), "brief": str(bf), "brief_data": brief,
                                                "title": brief["title"], "style": st["id"], "aspect": aspect})

    # ---- 2. music
    music_id = p.get("music") or "auto"
    if music_id == "none":
        music_id = None
    elif music_id == "auto":
        music_id = pipelines.auto_music(brief.get("music_mood"), kind="motion", seed=str(job_id or brief["title"]))
    music_file = music.resolve(music_id) if music_id else None

    # ---- 3. render: the main version, then one per spreadsheet row
    vals = values_of(brief, p.get("variables"))
    rows = [r for r in (p.get("rows") or []) if isinstance(r, dict)][:50]
    steps = 1 + len(rows)
    final = render_version(brief, st, vals, out / "final.mp4", aspect=aspect, files=files, cache=out / "scenes",
                           music_file=music_file, progress=pipelines._sub(progress, 0.05, 0.05 + 0.9 / steps))
    versions = []
    for i, row in enumerate(rows):
        rv = values_of(brief, {**(p.get("variables") or {}), **{k: v for k, v in row.items() if k != "name"}})
        name = _slug(row.get("name") or next((v for v in rv.values() if v), "") or f"row-{i + 1}") or f"row-{i + 1}"
        f = out / "versions" / f"{i + 1:02d}-{name}.mp4"
        f.parent.mkdir(exist_ok=True)
        lo = 0.05 + 0.9 * (i + 1) / steps
        render_version(brief, st, rv, f, aspect=aspect, files=files, cache=out / "scenes", music_file=music_file,
                       progress=pipelines._sub(progress, lo, lo + 0.9 / steps))
        versions.append({"name": name, "video": str(f), "values": rv})
    thumb = compose.thumbnail(final, out / "thumbnail.jpg", at=min(1.2, sum(s["seconds"] for s in brief["scenes"]) / 2))
    result = {"video": str(final), "thumbnail": str(thumb), "title": fill(brief["title"], vals), "brief": str(bf),
              "brief_data": brief, "values": vals, "style": st["id"], "aspect": aspect, "versions": versions,
              "duration": round(A.probe_duration(final), 2),
              "report": {"aspect": aspect, "style": st["id"], "scenes": len(brief["scenes"]), "music_id": music_id,
                         "music_credit": music.credit(music_id), "elapsed": round(time.time() - t0, 1),
                         "warnings": brief.get("warnings", [])}}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return result
