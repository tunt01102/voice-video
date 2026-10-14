"""Vietnamese syllables, tones and poem forms — the text side of singing and chanting (ngâm thơ).

Vietnamese is tonal: a melody that ignores the tones makes the lyrics unintelligible ("ma" sung on a rising
note is heard as "má"). Every syllable's tone is read from its diacritics and later constrains the melody.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Combining marks that carry the tone (the others — breve, circumflex, horn — are vowel quality).
_TONE_MARKS = {"́": "sac", "̀": "huyen", "̉": "hoi", "̃": "nga", "̣": "nang"}
TONES = ("ngang", "huyen", "sac", "hoi", "nga", "nang")
# Bằng (level) vs trắc (oblique) — the axis classical prosody (luật bằng trắc) is built on.
BANG = {"ngang", "huyen"}
_CHECKED = re.compile(r"(p|t|c|ch)$")   # stop-final syllables are short: "hết", "một", "sách"
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)


@dataclass
class Syllable:
    text: str          # as written (keeps capitals)
    tone: str          # one of TONES
    checked: bool      # ends in p/t/c/ch: sung short, no long melisma
    line: int          # 0-based line index in the poem
    index: int         # 0-based position within the line
    last: bool = False  # last syllable of its line

    @property
    def bang(self) -> bool:
        return self.tone in BANG


def tone_of(syllable: str) -> str:
    for ch in unicodedata.normalize("NFD", syllable.lower()):
        if ch in _TONE_MARKS:
            return _TONE_MARKS[ch]
    return "ngang"


def base_letters(syllable: str) -> str:
    """Syllable without tone marks or vowel diacritics (đ → d): 'hết' → 'het'."""
    s = unicodedata.normalize("NFD", syllable.lower()).replace("đ", "d")
    return "".join(c for c in s if not unicodedata.combining(c))


def is_checked(syllable: str) -> bool:
    return bool(_CHECKED.search(base_letters(syllable)))


def split_lines(text: str) -> list[str]:
    """A poem → lines. Accepts one line per row, or rows joined by ',' '/' when pasted inline."""
    rows = [r.strip() for r in re.split(r"[\r\n]+", text) if r.strip()]
    if len(rows) == 1:
        # "Mình ta với cả trời sao sáng, Nâng chén sương đêm cạn hết sầu" → two lines.
        rows = [r.strip() for r in re.split(r"\s*[/|;]\s*|(?<=[,.!?])\s+(?=[A-ZÀ-Ỹ])", rows[0]) if r.strip()]
    return [r for r in rows if _WORD.search(r)]


def syllables(line: str) -> list[str]:
    return _WORD.findall(line)


def parse(text: str) -> list[list[Syllable]]:
    out: list[list[Syllable]] = []
    for li, line in enumerate(split_lines(text)):
        sy = syllables(line)
        out.append([Syllable(s, tone_of(s), is_checked(s), li, i, i == len(sy) - 1) for i, s in enumerate(sy)])
    return [l for l in out if l]


def form(lines: list[list[Syllable]]) -> str:
    """Name the verse form: 'that-ngon' (7), 'ngu-ngon' (5), 'luc-bat' (6/8), else 'tu-do' (free)."""
    n = [len(l) for l in lines]
    if n and all(x == 7 for x in n):
        return "that-ngon"
    if n and all(x == 5 for x in n):
        return "ngu-ngon"
    if len(n) >= 2 and all(x == (6 if i % 2 == 0 else 8) for i, x in enumerate(n)):
        return "luc-bat"
    return "tu-do"


def caesura(form_name: str, length: int) -> int | None:
    """Index after which the line breathes (ngắt nhịp): 7 → 4/3, 5 → 2/3, 6/8 → 2/2/2."""
    if form_name == "that-ngon" or length == 7:
        return 3
    if form_name == "ngu-ngon" or length == 5:
        return 1
    if length in (6, 8):
        return length // 2 - 1
    return None
