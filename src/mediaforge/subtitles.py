"""Subtitle cues from word timings; SRT, VTT and ASS (incl. karaoke) writers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PUNCT_STRONG = (".", "!", "?", "…", ":", ";")
PUNCT_SOFT = (",", "–", "-")


@dataclass
class Cue:
    start: float
    end: float
    words: list[dict[str, Any]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _wrap(words: list[str], max_chars: int) -> list[str]:
    """Balanced two-line wrap (character width, not word count — VI words are syllables)."""
    full = " ".join(words)
    if len(full) <= max_chars:
        return [full]
    best, best_cost = [full], 1e9
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > max_chars or len(b) > max_chars:
            continue
        cost = abs(len(a) - len(b)) - (8 if words[i - 1].endswith(PUNCT_SOFT + PUNCT_STRONG) else 0)
        if cost < best_cost:
            best, best_cost = [a, b], cost
    return best


def build_cues(
    words: list[dict[str, Any]], *, max_chars: int = 42, max_lines: int = 2, max_duration: float = 6.0,
    min_gap: float = 0.04,
) -> list[Cue]:
    cues: list[Cue] = []
    cur: list[dict[str, Any]] = []
    limit = max_chars * max_lines

    def flush() -> None:
        if not cur:
            return
        cues.append(Cue(cur[0]["start"], cur[-1]["end"], list(cur), _wrap([w["text"] for w in cur], max_chars)))
        cur.clear()

    for w in words:
        if cur:
            length = len(" ".join(x["text"] for x in cur + [w]))
            too_long = length > limit or (w["end"] - cur[0]["start"]) > max_duration
            pause = w["start"] - cur[-1]["end"] > 0.6
            if too_long or pause:
                flush()
        cur.append(w)
        txt = w["text"]
        soft_full = len(" ".join(x["text"] for x in cur)) > limit * 0.6
        if txt.endswith(PUNCT_STRONG) or (txt.endswith(PUNCT_SOFT) and soft_full):
            flush()
    flush()
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


STYLES = {
    # name: (font size at PlayResY, margin V, alignment, outline, bold)
    "landscape": dict(size=58, margin_v=70, align=2, outline=3.2, shadow=1.0),
    "vertical": dict(size=86, margin_v=520, align=2, outline=5.0, shadow=1.5),
    "square": dict(size=70, margin_v=160, align=2, outline=4.0, shadow=1.2),
}


def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def write_ass(
    cues: list[Cue], path: str | Path, *, width: int = 1920, height: int = 1080, karaoke: bool = False,
    font: str = "Arial", text_colour: str = "#FFFFFF", highlight: str = "#FFD400", style: str | None = None,
) -> Path:
    st = STYLES[style or ("vertical" if height > width else "square" if height == width else "landscape")]
    size = int(st["size"] * height / (1920 if height > width else 1080))
    # Karaoke: \kf sweeps from SecondaryColour (unsung) to PrimaryColour (sung).
    primary = _ass_colour(highlight if karaoke else text_colour)
    secondary = _ass_colour(text_colour)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},{secondary},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{st['outline']},{st['shadow']},{st['align']},{int(width*0.06)},{int(width*0.06)},{int(st['margin_v']*height/(1920 if height > width else 1080))},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ass_ts(t: float) -> str:
        cs = int(round(max(t, 0) * 100))
        h, cs = divmod(cs, 360000)
        m, cs = divmod(cs, 6000)
        s, cs = divmod(cs, 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    events = []
    for c in cues:
        if karaoke:
            parts, t = [], c.start
            line_words = [ln.split() for ln in c.lines]
            idx = 0
            for li, lw in enumerate(line_words):
                for w_txt in lw:
                    w = c.words[idx] if idx < len(c.words) else {"start": t, "end": t + 0.2}
                    lead = max(0.0, w["start"] - t)
                    if lead > 0.02:
                        parts.append(f"{{\\k{int(lead*100)}}}")
                    parts.append(f"{{\\kf{max(1, int((w['end'] - max(w['start'], t)) * 100))}}}{w_txt} ")
                    t = max(t, w["end"])
                    idx += 1
                if li < len(line_words) - 1:
                    parts.append("\\N")
            text = "".join(parts).rstrip()
        else:
            text = c.text.replace("\n", "\\N")
        events.append(f"Dialogue: 0,{ass_ts(c.start)},{ass_ts(c.end)},Default,,0,0,0,,{text}")
    Path(path).write_text(header + "\n".join(events) + "\n", "utf-8")
    return Path(path)


def write_all(words: list[dict[str, Any]], out_base: str | Path, *, width: int = 1920, height: int = 1080, karaoke: bool | None = None) -> dict[str, str]:
    vertical = height > width
    karaoke = vertical if karaoke is None else karaoke
    cues = build_cues(words, max_chars=26 if vertical else 42, max_lines=2)
    karaoke_cues = build_cues(words, max_chars=20, max_lines=2, max_duration=3.0) if vertical else cues
    base = Path(out_base)
    return {
        "srt": str(write_srt(cues, base.with_suffix(".srt"))),
        "vtt": str(write_vtt(cues, base.with_suffix(".vtt"))),
        "ass": str(write_ass(karaoke_cues if karaoke else cues, base.with_suffix(".ass"), width=width, height=height, karaoke=karaoke)),
    }
