"""Subtitle cues from word timings; SRT, VTT and ASS (incl. karaoke word highlight) writers.

Every line is measured in pixels with the font libass will draw it in, so no cue can run off the frame
(docs/SDD-subtitle-fit-pronunciation.md §3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

PUNCT_STRONG = (".", "!", "?", "…", ":", ";")
PUNCT_SOFT = (",", "–", "-")
NUMBER_WORDS = {
    # vi
    "không", "một", "mốt", "hai", "ba", "bốn", "tư", "năm", "lăm", "sáu", "bảy", "tám", "chín", "mười", "mươi",
    "trăm", "nghìn", "ngàn", "triệu", "tỷ", "tỉ", "linh", "lẻ", "phẩy", "chấm",
    # en
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "twenty",
    "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million", "billion",
    "point",
}
# A line or cue that starts with one of these reads naturally ("… tầng dưới, | nơi tầng pháp lý …").
BREAK_BEFORE = {
    "và", "nhưng", "mà", "thì", "nên", "vì", "nơi", "khi", "để", "rằng", "còn", "hay", "hoặc", "là", "của", "với",
    "trong", "từ", "tại", "theo", "ở", "đến", "cho", "được", "đã", "sẽ", "đang", "nếu", "dù", "rồi", "bởi",
    "and", "but", "or", "so", "because", "which", "that", "when", "while", "where", "with", "for", "from", "to",
    "in", "on", "at", "of", "if", "is", "are", "was", "were",
}
KARAOKE_SCALE = 1.08          # the spoken word grows by 8 %
KARAOKE_HIGHLIGHT = "#FFD400"

STYLES = {
    # font size at PlayResY (1080 for landscape/square, 1920 for vertical), margin V, alignment, outline, shadow
    "landscape": dict(size=58, margin_v=70, align=2, outline=3.2, shadow=1.0),
    "vertical": dict(size=86, margin_v=520, align=2, outline=5.0, shadow=1.5),
    "square": dict(size=70, margin_v=160, align=2, outline=4.0, shadow=1.2),
}
FONT_FILES = {
    "Arial": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
              "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}
# libass sizes a font so ascender+descender span Fontsize px; Pillow sizes by em. Measured for Arial Bold:
# libass width = 0.894 × Pillow width at the same size. We divide by 1.10 (slightly wider than real) and keep
# a further safety factor, so a measured line is never narrower than what is drawn.
LIBASS_EM = 1.10
SAFETY = 1.04


@lru_cache(maxsize=65536)
def _px(font: str, size: int, text: str) -> float:
    f = _font(font, size)
    return f.getlength(text) if f else 0.62 * size * len(text)


@lru_cache(maxsize=16)
def _font(font: str, size: int):
    from PIL import ImageFont

    for path in FONT_FILES.get(font, []) + FONT_FILES["Arial"]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return None


@dataclass(frozen=True)
class Layout:
    """What fits on one subtitle line for a given frame and style."""

    width: int = 1920
    height: int = 1080
    font: str = "Arial"
    font_px: int = 58           # ASS Fontsize at PlayResY == height
    outline: float = 3.2
    margin_lr: int = 115
    max_chars: int = 42
    max_lines: int = 2
    max_duration: float = 6.0
    karaoke: bool = False

    @classmethod
    def for_frame(cls, width: int = 1920, height: int = 1080, *, karaoke: bool = False, max_chars: int | None = None,
                  max_lines: int = 2, max_duration: float = 6.0, font: str = "Arial") -> Layout:
        st = STYLES[style_name(width, height)]
        ref_h = 1920 if height > width else 1080
        return cls(width=width, height=height, font=font, font_px=int(st["size"] * height / ref_h),
                   outline=st["outline"], margin_lr=int(width * 0.06),
                   max_chars=max_chars or (26 if height > width else 42), max_lines=max_lines,
                   max_duration=max_duration, karaoke=karaoke)

    @property
    def safe_px(self) -> float:
        return self.width - 2 * self.margin_lr

    def text_px(self, text: str) -> float:
        return _px(self.font, self.font_px, text) / LIBASS_EM * SAFETY + 2 * self.outline

    def line_px(self, words: list[str]) -> float:
        """Drawn width of a line, reserving room for the karaoke pop of its widest word."""
        w = self.text_px(" ".join(words))
        if self.karaoke and words:
            w += (KARAOKE_SCALE - 1) * max(self.text_px(x) for x in words)
        return w

    def line_fits(self, words: list[str]) -> bool:
        return len(" ".join(words)) <= self.max_chars and self.line_px(words) <= self.safe_px


def style_name(width: int, height: int) -> str:
    return "vertical" if height > width else "square" if height == width else "landscape"


@dataclass
class Cue:
    start: float
    end: float
    words: list[dict[str, Any]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def wrap_words(words: list[str], layout: Layout, max_lines: int | None = None) -> list[str] | None:
    """Split words into ≤ max_lines lines that all fit, as balanced as possible. None if impossible.

    A single word always "fits" (it is shrunk when drawn, see write_ass).
    """
    max_lines = max_lines or layout.max_lines
    if not words:
        return []
    if layout.line_fits(words) or len(words) == 1:
        return [" ".join(words)]
    if max_lines < 2:
        return None
    best: tuple[float, list[str]] | None = None
    # Prefer two lines; only go to more when two cannot fit.
    for n_lines in range(2, max_lines + 1):
        for lines in _splits(words, n_lines):
            if not all(layout.line_fits(ln) or len(ln) == 1 for ln in lines):
                continue
            widths = [layout.line_px(ln) for ln in lines]
            cost = max(widths) - min(widths)
            for a, b in zip(lines, lines[1:]):
                cost += _break_cost(a[-1], b[0]) * layout.safe_px / 1000
            if best is None or cost < best[0]:
                best = (cost, [" ".join(ln) for ln in lines])
        if best:
            return best[1]
    return None


def _splits(words: list[str], n_lines: int):
    if n_lines == 1:
        yield [words]
        return
    for i in range(1, len(words) - n_lines + 2):
        for rest in _splits(words[i:], n_lines - 1):
            yield [words[:i], *rest]


def _bare(word: str) -> str:
    return word.strip(".,!?…:;–-\"'()").lower()


def _is_number(word: str) -> bool:
    return _bare(word) in NUMBER_WORDS


def _break_cost(before: str, after: str) -> float:
    """Cost (per mille of the line width) of breaking between two words; negative = a good place."""
    if before.endswith(PUNCT_STRONG + PUNCT_SOFT):
        return -150
    cost = 0.0
    if _is_number(before) and _is_number(after):
        cost += 200  # "hai | nghìn không trăm" reads badly
    if _bare(after) in BREAK_BEFORE:
        cost -= 80
    return cost


def _groups(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Sentences (strong punctuation) and pause-separated runs."""
    groups: list[list[dict[str, Any]]] = [[]]
    for w in words:
        if groups[-1] and w["start"] - groups[-1][-1]["end"] > 0.6:
            groups.append([])
        groups[-1].append(w)
        if w["text"].endswith(PUNCT_STRONG):
            groups.append([])
    return [g for g in groups if g]


def _split_group(g: list[dict[str, Any]], layout: Layout) -> list[tuple[list[dict[str, Any]], list[str]]]:
    """Optimal split of one sentence into cues that all fit (dynamic programming)."""
    texts = [w["text"] for w in g]
    n = len(g)
    total = len(" ".join(texts))
    limit = layout.max_chars * layout.max_lines
    target = total / max(1, math.ceil(total / limit))
    best: list[tuple[float, int, list[str]] | None] = [None] * (n + 1)
    best[0] = (0.0, -1, [])
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            if best[i] is None:
                continue
            chunk = texts[i:j]
            chars = len(" ".join(chunk))
            if j - i > 1 and chars > limit:
                break  # longer spans only get longer
            if j - i > 1 and g[j - 1]["end"] - g[i]["start"] > layout.max_duration:
                break
            lines = wrap_words(chunk, layout)
            if lines is None:
                continue
            cost = best[i][0] + 1000 + (chars - target) ** 2 / 10
            if j < n:  # a cue boundary after word j-1
                cost += _break_cost(texts[j - 1], texts[j]) / 2
            if n > 2 and (j - i) <= 2:
                cost += 120
            if best[j] is None or cost < best[j][0]:
                best[j] = (cost, i, lines)
    out: list[tuple[list[dict[str, Any]], list[str]]] = []
    j = n
    while j > 0:
        _, i, lines = best[j]  # type: ignore[misc]
        out.append((g[i:j], lines))
        j = i
    return out[::-1]


def build_cues(
    words: list[dict[str, Any]], *, max_chars: int = 42, max_lines: int = 2, max_duration: float = 6.0,
    min_gap: float = 0.04, layout: Layout | None = None,
) -> list[Cue]:
    layout = layout or Layout(max_chars=max_chars, max_lines=max_lines, max_duration=max_duration)
    cues = [Cue(chunk[0]["start"], chunk[-1]["end"], list(chunk), lines)
            for g in _groups(words) for chunk, lines in _split_group(g, layout)]
    # Merge orphan one-word cues ("mãi.") into the previous cue when it still fits on screen.
    merged: list[Cue] = []
    for c in cues:
        prev = merged[-1] if merged else None
        if prev and len(c.words) == 1 and c.start - prev.end < 0.6:
            ws = prev.words + c.words
            lines = wrap_words([w["text"] for w in ws], layout)
            if lines is not None and ws[-1]["end"] - ws[0]["start"] <= layout.max_duration + 1.0:
                merged[-1] = Cue(prev.start, c.end, ws, lines)
                continue
        merged.append(c)
    cues = merged
    for a, b in zip(cues, cues[1:]):
        # Close short gaps so text doesn't flicker; otherwise linger briefly. Never overlap.
        a.end = b.start - min_gap if b.start - a.end < 0.5 else min(a.end + 0.25, b.start - min_gap)
    if cues:
        cues[-1].end += 0.4
    return cues


def _ts(t: float, sep: str = ",") -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(cues: list[Cue], path: str | Path) -> Path:
    out = [f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    Path(path).write_text("\n".join(out), "utf-8")
    return Path(path)


def write_vtt(cues: list[Cue], path: str | Path) -> Path:
    out = ["WEBVTT\n"] + [f"{_ts(c.start, '.')} --> {_ts(c.end, '.')}\n{c.text}\n" for c in cues]
    Path(path).write_text("\n".join(out), "utf-8")
    return Path(path)


def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _inline_colour(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def write_ass(
    cues: list[Cue], path: str | Path, *, width: int = 1920, height: int = 1080, karaoke: bool = False,
    font: str = "Arial", text_colour: str = "#FFFFFF", highlight: str = KARAOKE_HIGHLIGHT, style: str | None = None,
) -> Path:
    st = STYLES[style or style_name(width, height)]
    ref_h = 1920 if height > width else 1080
    size = int(st["size"] * height / ref_h)
    layout = Layout(width=width, height=height, font=font, font_px=size, outline=st["outline"],
                    margin_lr=int(width * 0.06), karaoke=karaoke)
    colour = _ass_colour(text_colour)
    # WrapStyle 0 (smart wrap) is a safety net: lines are pre-wrapped to fit, but if one ever did not, libass
    # wraps it onto another line instead of clipping it at the frame edge.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{colour},{colour},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{st['outline']},{st['shadow']},{st['align']},{layout.margin_lr},{layout.margin_lr},{int(st['margin_v'] * height / ref_h)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ass_ts(t: float) -> str:
        cs = int(round(max(t, 0) * 100))
        h, cs = divmod(cs, 360000)
        m, cs = divmod(cs, 6000)
        s, cs = divmod(cs, 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def shrink(line_words: list[str]) -> float:
        """Horizontal scale (≤ 1) for a line that cannot fit even alone (one very long word)."""
        px = layout.line_px(line_words)
        return min(1.0, layout.safe_px / px) if px > 0 else 1.0

    hi, base = _inline_colour(highlight), _inline_colour(text_colour)
    events = []
    for c in cues:
        line_words = [ln.split() for ln in c.lines]
        if karaoke:
            # Per-word pop: the spoken word turns `highlight` and grows KARAOKE_SCALE, then returns to normal
            # when the next word starts. Each word carries its own tags so nothing bleeds into its neighbours.
            parts, idx = [], 0
            flat = c.words
            for li, lw in enumerate(line_words):
                sx = shrink(lw) * 100
                for w_txt in lw:
                    w = flat[idx] if idx < len(flat) else None
                    if w is None:
                        parts.append(f"{{\\1c{base}\\fscx{sx:.0f}\\fscy100}}{w_txt} ")
                        continue
                    nxt = flat[idx + 1]["start"] if idx + 1 < len(flat) else w["end"]
                    t0 = max(0, int((w["start"] - c.start) * 1000))
                    t1 = max(t0 + 80, int((max(w["end"], nxt) - c.start) * 1000) - 40)  # done as the next word lights
                    big = sx * KARAOKE_SCALE
                    parts.append(
                        f"{{\\1c{base}\\fscx{sx:.0f}\\fscy100"
                        f"\\t({t0},{t0 + 80},\\1c{hi}\\fscx{big:.0f}\\fscy{100 * KARAOKE_SCALE:.0f})"
                        f"\\t({t1},{t1 + 60},\\1c{base}\\fscx{sx:.0f}\\fscy100)}}{w_txt} ")
                    idx += 1
                parts[-1] = parts[-1].rstrip()
                if li < len(line_words) - 1:
                    parts.append("\\N")
            text = "".join(parts)
        else:
            text = "\\N".join(
                (f"{{\\fscx{shrink(lw) * 100:.0f}}}" if shrink(lw) < 1 else "") + " ".join(lw) for lw in line_words)
        events.append(f"Dialogue: 0,{ass_ts(c.start)},{ass_ts(c.end)},Default,,0,0,0,,{text}")
    Path(path).write_text(header + "\n".join(events) + "\n", "utf-8")
    return Path(path)


def write_all(words: list[dict[str, Any]], out_base: str | Path, *, width: int = 1920, height: int = 1080,
              karaoke: bool | None = None) -> dict[str, str]:
    """SRT/VTT (plain) and ASS (karaoke word highlight by default, any aspect) for burning in."""
    karaoke = True if karaoke is None else karaoke
    vertical = height > width
    cues = build_cues(words, layout=Layout.for_frame(width, height))
    ass_cues = build_cues(words, layout=Layout.for_frame(
        width, height, karaoke=karaoke, max_chars=20 if vertical and karaoke else None,
        max_duration=3.0 if vertical and karaoke else 6.0))
    base = Path(out_base)
    return {
        "srt": str(write_srt(cues, base.with_suffix(".srt"))),
        "vtt": str(write_vtt(cues, base.with_suffix(".vtt"))),
        "ass": str(write_ass(ass_cues, base.with_suffix(".ass"), width=width, height=height, karaoke=karaoke)),
    }


def _ass_time(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def with_chapters(ass_path: str | Path, chapters: list[tuple[float, float, str]], out_path: str | Path, *,
                  height: int = 1080) -> Path:
    """A copy of an ASS file with chapter headings as a top-left lower-third (SDD-review-upgrade §5.4.3). Only the
    burned-in render uses it; the exported SRT/VTT/ASS stay plain subtitles."""
    src = Path(ass_path).read_text("utf-8")
    unit = height / 1080
    style = (f"Style: Chapter,Arial,{int(40 * unit)},&H00FFFFFF,&H00FFFFFF,&H00F0B45B,&HB4101218,-1,0,0,0,100,100,1,0,3,"
             f"{max(6, int(10 * unit))},0,7,{int(70 * unit)},{int(70 * unit)},{int(60 * unit)},1")
    lines = src.splitlines()
    k = next(i for i, l in enumerate(lines) if l.startswith("Style:"))
    lines.insert(k + 1, style)
    for a, b, text in chapters:
        clean = text.replace("{", "(").replace("}", ")").replace("\n", " ")
        lines.append(f"Dialogue: 1,{_ass_time(a)},{_ass_time(b)},Chapter,,0,0,0,,{{\\fad(250,300)}}{clean}")
    Path(out_path).write_text("\n".join(lines) + "\n", "utf-8")
    return Path(out_path)
