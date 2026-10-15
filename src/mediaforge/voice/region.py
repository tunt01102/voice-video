"""Regional tones: QA for Central and Southern voices (docs/SDD-stock-voices.md §5.5).

Whisper hears Vietnamese the Northern way. A Southern or Central speaker who merges hỏi and ngã (and, in parts of the
Centre, sắc and nặng) is reading correctly for their region, so QA does not count those swaps as mistakes, and the
repair loop does not "fix" them by respelling native words the Northern way. Only a syllable heard with exactly the
same letters and a tone from the same regional group is forgiven; any other difference still fails.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from mediaforge.sing.tones import tone_of

# Tone pairs a region does not keep apart (as heard by a Northern-trained judge), by profile `region`.
REGION_TONES: dict[str, tuple[frozenset[str], ...]] = {
    "Nam": (frozenset({"hoi", "nga"}),),
    "Trung": (frozenset({"hoi", "nga"}), frozenset({"sac", "nang"})),
}
_TONE_MARKS = {"́", "̀", "̉", "̃", "̣"}


def tone_groups(voice: dict[str, Any] | None) -> tuple[frozenset[str], ...]:
    return REGION_TONES.get((voice or {}).get("region") or "", ())


def strip_tone(syllable: str) -> str:
    """Letters with vowel marks kept and the tone mark removed: 'sửa' → 'sưa', 'số' → 'sô'."""
    s = unicodedata.normalize("NFD", syllable.lower())
    return unicodedata.normalize("NFC", "".join(c for c in s if c not in _TONE_MARKS))


def regional_variant(expected: str, heard: str, groups: tuple[frozenset[str], ...]) -> bool:
    """`heard` is `expected` with only its tone swapped within one regional group."""
    if not groups or expected == heard or strip_tone(expected) != strip_tone(heard):
        return False
    a, b = tone_of(expected), tone_of(heard)
    return any(a in g and b in g for g in groups)
