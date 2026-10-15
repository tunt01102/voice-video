"""Speech recognition (mlx-whisper), pronunciation scoring and script↔audio alignment."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mediaforge import audio as A
from mediaforge.config import settings
from mediaforge.text.normalize import qa_tokens


@dataclass
class Word:
    text: str
    start: float
    end: float

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}


def transcribe(src: str | Path | np.ndarray, lang: str | None = None, sr: int = A.SR, initial_prompt: str | None = None,
               temperature: float | None = None) -> dict[str, Any]:
    """Returns {"text", "language", "words": [Word]} with word-level timestamps.

    temperature=0.0 decodes greedily with no fallback sampling (QA judging: the fallback is where Whisper
    hallucinates CJK on Vietnamese audio)."""
    import mlx_whisper

    if isinstance(src, np.ndarray):
        audio16 = A.resample(src, sr, 16_000)
    else:
        audio16 = A.load(src, 16_000)
    kw: dict[str, Any] = {"path_or_hf_repo": settings.asr_model, "word_timestamps": True}
    if lang:
        kw["language"] = lang
    if initial_prompt:
        kw["initial_prompt"] = initial_prompt
    if temperature is not None:
        kw["temperature"] = temperature
    r = mlx_whisper.transcribe(audio16, **kw)
    words = [
        Word(w["word"].strip(), float(w["start"]), float(w["end"]))
        for seg in r.get("segments", [])
        for w in seg.get("words", [])
        if w["word"].strip()
    ]
    return {"text": r.get("text", "").strip(), "language": r.get("language", lang), "words": words}


FOREIGN_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff]")


# Whisper's stock hallucinations (YouTube outros it was trained on), typical on short or quiet clips: "chấm Vi En."
# was heard as "Hãy subscribe cho kênh Ghiền Mì Gõ…" by both engines (corpus 2026-10-15).
HALLUCINATIONS = ("subscribe", "ghiền mì gõ", "đăng ký kênh", "thanks for watching", "la la school")


def judge_error(heard: str, lang: str, expected: str | None = None) -> bool:
    """ASR output that cannot be a reading of the speech: foreign script (CJK, Cyrillic, …), or a stock Whisper
    hallucination the script does not contain. The judge failed, not necessarily the voice."""
    if lang not in ("vi", "en"):
        return False
    if FOREIGN_SCRIPT.search(heard):
        return True
    low, exp = heard.lower(), (expected or "").lower()
    return expected is not None and any(h in low and h not in exp for h in HALLUCINATIONS)


def _count(seq: list[str], sub: list[str]) -> int:
    n = len(sub)
    return sum(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def repetition(expected: str, heard: str, lang: str) -> str | None:
    """A word group the engine said twice close together ("lưu lưu lưu", "2025 andra 2025") more often than the
    script has it — engine hallucination (F4): the fix is a new seed, not a respelling."""
    ref, hyp = qa_tokens(expected, lang), qa_tokens(heard, lang)
    for n in range(8, 0, -1):
        for i in range(len(hyp) - 2 * n + 1):
            a = hyp[i:i + n]
            if n == 1 and len(a[0]) < 2:
                continue
            for gap in ((0,) if n == 1 else (0, 1, 2)):
                if hyp[i + n + gap:i + 2 * n + gap] == a and _count(hyp, a) > _count(ref, a):
                    return " ".join(a)
    return None


def lenient_terms(text: str, lexicon_terms: set[str] | None = None) -> set[str]:
    """Tokens ASR cannot be expected to spell back exactly: lexicon terms and brand-like words
    (internal capitals or digits, e.g. MediaForge, Block8, iPhone)."""
    terms = set(lexicon_terms or ())
    terms |= {w.strip(".,!?;:()\"'") for w in text.split() if re.search(r"[a-z][A-Z]|[A-Za-z]\d|^[A-Z]{2,}", w.strip(".,!?;:()\"'"))}
    return {t for t in terms if t}


# Vietnamese/English letter names → the letter (how a spelled acronym or domain is read aloud).
LETTER_NAMES = {
    "a": "a", "ây": "a", "ă": "a", "â": "a", "bê": "b", "bi": "b", "bờ": "b", "xê": "c", "xi": "c", "cờ": "c",
    "dê": "d", "đê": "d", "đi": "d", "di": "d", "đ": "d", "e": "e", "ê": "e", "i": "i", "ai": "i", "ép": "f",
    "ép phờ": "f", "gờ": "g", "giê": "g", "gi": "g", "hát": "h", "hắt": "h", "ca": "k", "kê": "k", "eo": "l",
    "lờ": "l", "em": "m", "mờ": "m", "en": "n", "nờ": "n", "o": "o", "ô": "o", "ơ": "o", "pê": "p", "pi": "p",
    "quy": "q", "cu": "q", "rờ": "r", "ét": "s", "xì": "", "tê": "t", "ti": "t", "u": "u", "ư": "u", "diu": "u",
    "vê": "v", "vi": "v", "kép": "w", "ích": "x", "dét": "z", "zét": "z", "chấm": "", "dot": "", "chấm,": "",
}
SPELLED_MIN_SIMILARITY = 0.8


def _letter_key(tokens: list[str], *, strict: bool = False) -> tuple[str, float]:
    """Letters a token run spells, and the share of tokens that looked spelled. Letter names and single letters
    always count; other short ASCII tokens count only if not strict (the heard side: Whisper writes "aisme") or,
    when strict (the script side), only vowel-less clusters such as "vn", "pnj" — so ordinary English words in the
    script are never letter-compared ("cat" vs "cats" must still fail)."""
    key, spelled = [], 0
    for t in tokens:
        if t in LETTER_NAMES or (len(t) == 1 and t.isalpha()):
            key.append(LETTER_NAMES.get(t, t))
            spelled += 1
        elif t.isascii() and t.isalpha() and len(t) <= 6 and (not strict or not set(t) & set("aeiouy")):
            key.append(t)
            spelled += 1
        else:
            key.append(t)
    folded = "".join(key).translate(str.maketrans("eyo", "iiu"))
    return folded, spelled / max(len(tokens), 1)


def spelled_match(ref_seg: list[str], hyp_seg: list[str]) -> bool:
    """A spelled-out acronym/domain in the script ("ây ai ét em i chấm vi en") heard back in written form
    ("AISME.vn" → "a i ét em ê vn"): compare the letters, not the words (judge fix, SDD-review-upgrade §5.2.3)."""
    if not ref_seg or not hyp_seg:
        return False
    rk, rs = _letter_key(ref_seg, strict=True)
    hk, hs = _letter_key(hyp_seg)
    if rs < 0.6 or hs < 0.6 or not rk:
        return False
    if len(rk) < 3:  # one or two letters: only an exact match counts ("S" heard as "F" must still fail)
        return rk == hk
    return difflib.SequenceMatcher(a=rk, b=hk, autojunk=False).ratio() >= SPELLED_MIN_SIMILARITY


def error_rate(expected: str, heard: str, lang: str, lenient: set[str] | None = None) -> dict[str, Any]:
    """Score = mean(syllable WER, CER) for Vietnamese, WER for English; plus the word diff.

    Diff blocks whose expected side consists only of `lenient` terms are excluded from scoring.
    """
    import jiwer

    ref, hyp = qa_tokens(expected, lang), qa_tokens(heard, lang)
    if not ref:
        return {"score": 0.0, "wer": 0.0, "cer": 0.0, "missing": [], "extra": [], "ignored": []}
    soft = {t for term in (lenient or ()) for t in qa_tokens(term, lang)}
    keep_ref, keep_hyp, missing, extra, ignored = [], [], [], [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False).get_opcodes():
        r_seg, h_seg = ref[i1:i2], hyp[j1:j2]
        if op != "equal" and r_seg and all(t in soft for t in r_seg):
            ignored.append(f"{' '.join(r_seg)} → {' '.join(h_seg)}")
            continue
        if op == "replace" and spelled_match(r_seg, h_seg):
            ignored.append(f"{' '.join(r_seg)} ≈ {' '.join(h_seg)} (đánh vần)")
            continue
        keep_ref += r_seg
        keep_hyp += h_seg
        if op in ("replace", "delete"):
            missing.append(" ".join(r_seg))
        if op in ("replace", "insert"):
            extra.append(" ".join(h_seg))
    r, h = " ".join(keep_ref), " ".join(keep_hyp) or "∅"
    wer, cer = (jiwer.wer(r, h), jiwer.cer(r, h)) if keep_ref else (0.0, 0.0)
    # Vietnamese words are syllables: blend syllable error rate with CER so that a single wrong
    # syllable ("Hà Nội" → "Hà Lội") fails, while tone-mark spelling noise alone does not.
    score = (wer + cer) / 2 if lang == "vi" else wer
    return {"score": round(score, 4), "wer": round(wer, 4), "cer": round(cer, 4), "missing": missing,
            "extra": extra, "ignored": ignored}


# ---------------------------------------------------------------- alignment

def _key(token: str) -> str:
    t = unicodedata.normalize("NFC", token.lower())
    return re.sub(r"[^\w']", "", t)


def align_script(script: str, words: list[Word], total_duration: float | None = None) -> list[Word]:
    """Map ASR word timings onto the known script so subtitles show the script's own text."""
    display = script.split()
    if not display:
        return []
    if not words:
        dur = total_duration or 0.4 * len(display)
        step = dur / len(display)
        return [Word(t, i * step, (i + 1) * step) for i, t in enumerate(display)]
    a = [_key(t) for t in display]
    b = [_key(w.text) for w in words]
    times: list[tuple[float, float] | None] = [None] * len(display)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (words[j1 + k].start, words[j1 + k].end)
        elif op == "replace":
            # Spread the heard span over the script span proportionally.
            s, e = words[j1].start, words[j2 - 1].end
            n = i2 - i1
            for k in range(n):
                times[i1 + k] = (s + (e - s) * k / n, s + (e - s) * (k + 1) / n)
    # Interpolate gaps (script words the ASR missed entirely).
    end_all = total_duration or words[-1].end
    i = 0
    while i < len(times):
        if times[i] is None:
            j = i
            while j < len(times) and times[j] is None:
                j += 1
            left = times[i - 1][1] if i > 0 and times[i - 1] else 0.0
            right = times[j][0] if j < len(times) and times[j] else end_all
            n = j - i
            for k in range(n):
                times[i + k] = (left + (right - left) * k / n, left + (right - left) * (k + 1) / n)
            i = j
        else:
            i += 1
    out = [Word(t, s, e) for t, (s, e) in zip(display, times)]  # type: ignore[misc]
    for k in range(1, len(out)):  # enforce monotonic timings
        if out[k].start < out[k - 1].start:
            out[k].start = out[k - 1].start
        if out[k].end < out[k].start:
            out[k].end = out[k].start + 0.05
    return out
