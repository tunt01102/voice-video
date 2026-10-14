"""Slide rendering with Pillow (Vietnamese-capable fonts), for explainer videos."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from mediaforge.text.documents import MARKER_RE

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
FONT_REG = FONT_DIR / "Arial.ttf"
FONT_BOLD = FONT_DIR / "Arial Bold.ttf"

THEMES = {
    "midnight": {"bg": ((14, 18, 38), (38, 24, 74)), "title": (255, 255, 255), "text": (214, 220, 240), "accent": (255, 196, 0)},
    "ocean": {"bg": ((6, 40, 61), (10, 110, 130)), "title": (255, 255, 255), "text": (220, 240, 245), "accent": (120, 255, 214)},
    "paper": {"bg": ((246, 244, 238), (232, 228, 216)), "title": (24, 24, 28), "text": (55, 55, 64), "accent": (214, 76, 46)},
    "block8": {"bg": ((10, 12, 16), (22, 30, 44)), "title": (255, 255, 255), "text": (200, 208, 220), "accent": (0, 200, 140)},
}


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REG
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _gradient(w: int, h: int, c1, c2) -> Image.Image:
    base = Image.new("RGB", (w, h), c1)
    top = Image.new("RGB", (w, h), c2)
    mask = Image.linear_gradient("L").rotate(-35, expand=True).resize((w, h))
    return Image.composite(top, base, mask)


def render_slide(
    out: str | Path, title: str, bullets: list[str] | None = None, *, width: int = 1920, height: int = 1080,
    theme: str = "midnight", background: str | Path | None = None, footer: str | None = None,
) -> Path:
    t = THEMES.get(theme, THEMES["midnight"])
    if background and Path(background).exists():
        img = Image.open(background).convert("RGB")
        scale = max(width / img.width, height / img.height)
        img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
        left, top = (img.width - width) // 2, (img.height - height) // 2
        img = img.crop((left, top, left + width, top + height)).filter(ImageFilter.GaussianBlur(6))
        img = Image.blend(img, Image.new("RGB", (width, height), t["bg"][0]), 0.62)
    else:
        img = _gradient(width, height, *t["bg"])
    d = ImageDraw.Draw(img)
    vertical = height > width
    m = int(width * (0.08 if vertical else 0.075))
    unit = height / (1920 if vertical else 1080)
    title_font = _font(True, int((88 if vertical else 76) * unit))
    body_font = _font(False, int((58 if vertical else 44) * unit))
    y = int(height * (0.16 if vertical else 0.14))
    d.rectangle([m, y - int(28 * unit), m + int(120 * unit), y - int(18 * unit)], fill=t["accent"])
    for line in _wrap(d, title, title_font, width - 2 * m):
        d.text((m, y), line, font=title_font, fill=t["title"])
        y += int(title_font.size * 1.18)
    y += int(40 * unit)
    for b in bullets or []:
        lines = _wrap(d, b, body_font, width - 2 * m - int(60 * unit))
        r = int(10 * unit)
        cy = y + body_font.size // 2 + int(4 * unit)
        d.ellipse([m + 4, cy - r, m + 4 + 2 * r, cy + r], fill=t["accent"])
        for line in lines:
            d.text((m + int(50 * unit), y), line, font=body_font, fill=t["text"])
            y += int(body_font.size * 1.3)
        y += int(26 * unit)
        if y > height * (0.62 if vertical else 0.86):
            break
    if footer:
        f = _font(False, int(28 * unit))
        d.text((m, height - int(70 * unit)), footer, font=f, fill=t["text"])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return Path(out)


def figure_frame(
    src: str | Path, out: str | Path, *, width: int = 1920, height: int = 1080, title: str | None = None,
    theme: str = "midnight", pad: float = 0.08,
) -> Path:
    """A document figure (chart, diagram, screenshot) fitted whole inside the frame, never cropped.

    The figure sits over a blurred, dimmed copy of itself; `pad` leaves room for the Ken Burns zoom. With a title
    (explainer slides) a band above the figure carries it.
    """
    t = THEMES.get(theme, THEMES["midnight"])
    fig = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
    flat = Image.new("RGB", fig.size, (255, 255, 255))
    flat.paste(fig, mask=fig.split()[3])  # transparent PNG figures are drawn on white, as on the page
    bg = ImageOps.fit(flat, (width, height)).filter(ImageFilter.GaussianBlur(28))
    img = Image.blend(bg, Image.new("RGB", (width, height), t["bg"][0]), 0.55)
    d = ImageDraw.Draw(img)
    unit = height / (1920 if height > width else 1080)
    top = int(height * pad)
    if title:
        f = _font(True, int(56 * unit))
        lines = _wrap(d, title, f, int(width * 0.86))[:2]
        y = int(height * 0.05)
        for line in lines:
            d.text(((width - d.textlength(line, font=f)) / 2, y), line, font=f, fill=t["title"])
            y += int(f.size * 1.2)
        top = y + int(24 * unit)
    box_w, box_h = int(width * (1 - 2 * pad)), height - top - int(height * pad)
    scale = min(box_w / flat.width, box_h / flat.height)
    fig_img = flat.resize((max(1, int(flat.width * scale)), max(1, int(flat.height * scale))), Image.LANCZOS)
    x, y = (width - fig_img.width) // 2, top + (box_h - fig_img.height) // 2
    shadow = Image.new("L", (width, height), 0)
    ImageDraw.Draw(shadow).rectangle([x + 8, y + 12, x + fig_img.width + 8, y + fig_img.height + 12], fill=150)
    img.paste((0, 0, 0), mask=shadow.filter(ImageFilter.GaussianBlur(14)))
    img.paste(fig_img, (x, y))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return Path(out)


def parse_markdown_slides(md: str) -> list[dict]:
    """`# Title` starts a slide; `- bullet` lines are bullets; `> text` lines are narration."""
    slides: list[dict] = []
    cur: dict | None = None
    for raw in md.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            cur = {"title": line.lstrip("#").strip(), "bullets": [], "narration": ""}
            slides.append(cur)
        elif cur is None:
            continue
        elif line.startswith(("- ", "* ")):
            cur["bullets"].append(line[2:].strip())
        elif line.startswith(">"):
            cur["narration"] = (cur["narration"] + " " + line.lstrip("> ").strip()).strip()
        elif m := MARKER_RE.fullmatch(line):  # [ảnh N] from an imported document: this slide's picture
            cur["image"] = int(m.group(1))
    for s in slides:
        if not s["narration"]:
            s["narration"] = ". ".join([s["title"], *s["bullets"]]) + "."
    return slides
