"""Pronunciation pre-scan: find terms a TTS engine may misread before anything is rendered.

The owner writes how each term is read (``PNJ → Pi En Di``) or confirms it reads fine; answers are saved in the
lexicon (``manual`` / ``verified``) so later renders and scans reuse them. See
docs/SDD-subtitle-fit-pronunciation.md §5.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Any

from mediaforge import llm
from mediaforge.text import normalize as N

log = logging.getLogger(__name__)

# A token: letters/digits/marks, optionally joined by . + & / ' - _ (keeps "T+120", "CO₂", "4.3" together).
TOKEN = re.compile(r"[\w₀-₉⁰-⁹]+(?:[.+&/'’_-][\w₀-₉⁰-⁹]+)*", re.UNICODE)
# Vietnamese syllable (diacritics removed): onset + nucleus + coda.
VI_SYLLABLE = re.compile(
    r"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnprstvx])?"
    r"(uye|uya|ieu|yeu|uoi|uou|oai|oay|oeo|uay|uoi|[aeiouy]{1,3})"
    r"(ng|nh|ch|[cmnptiyou])?$"
)
VI_DIACRITICS = set("ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
SYMBOLS = set("₀₁₂₃₄₅₆₇₈₉⁰¹²³&@#/+_")
# Roman numerals and common unit abbreviations that the normaliser already voices.
SKIP = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "km", "kg", "cm", "mm", "m", "g"}

REASONS = {
    "acronym": "viết tắt",
    "alnum": "chữ + số",
    "camel": "tên riêng / thương hiệu",
    "foreign": "từ nước ngoài",
    "symbol": "ký hiệu",
    "diacritics": "tên có dấu tiếng Việt",
    "llm": "AI đoán dễ đọc sai",
}


def _ascii(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if unicodedata.category(c) != "Mn").replace("đ", "d")


def is_vi_syllable(word: str) -> bool:
    """True for a word that a Vietnamese reader would pronounce natively (``ban``, ``Hà``, ``Nguyễn``)."""
    w = word.lower()
    if any(c in VI_DIACRITICS for c in w):
        return True  # diacritics only occur in Vietnamese words
    return bool(VI_SYLLABLE.match(_ascii(w)))


def reasons_for(token: str, lang: str) -> list[str]:
    """Why a token may be misread; [] when it looks safe."""
    if token in SKIP or token.isdigit() or re.fullmatch(r"[\d.,/:-]+", token):
        return []  # plain numbers, dates and ranges are voiced by the normaliser
    letters = [c for c in token if c.isalpha()]
    out: list[str] = []
    if any(c in SYMBOLS for c in token):
        out.append("symbol")
    if letters and any(c.isdigit() for c in token):
        out.append("alnum")
    elif letters and all(c.isupper() for c in letters) and (len(letters) >= 2 or len(token) == 1):
        out.append("acronym")  # "PNJ", or a lone letter such as the "T" in "T cộng hai"
    elif re.search(r"[a-z][A-Z]|^[a-z]+[A-Z]", token):
        out.append("camel")
    if not out and letters:
        if lang == "vi":
            parts = re.split(r"[.+&/'’_-]", token)
            if any(p and p.isalpha() and not is_vi_syllable(p) for p in parts):
                out.append("foreign")
        elif any(c in VI_DIACRITICS for c in token.lower()):
            out.append("diacritics")
    return out


def _context(text: str, term: str, width: int = 70) -> str:
    m = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text)
    if not m:
        return ""
    a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
    return ("…" if a else "") + text[a:b].replace("\n", " ").strip() + ("…" if b < len(text) else "")


def _llm_suggestions(text: str, lang: str, terms: list[str]) -> dict[str, str]:
    """Ask the LLM for further risky terms and a respelling for every term. Text only — never audio."""
    lang_name = "Vietnamese" if lang == "vi" else "English"
    example = ("'PNJ' -> 'Pi En Di', 'Claude' -> 'clốt', 'API' -> 'ây pi ai', 'livestream' -> 'lai sờ trim'"
               if lang == "vi" else "'Nguyen' -> 'Win', 'PNJ' -> 'P N J', 'Hanoi' -> 'Ha noy'")
    prompt = (
        f"A {lang_name} text-to-speech engine will read the script below aloud. List every word or term it is likely "
        f"to MISPRONOUNCE: acronyms, foreign names and words, brands, codes, units, symbols. Include these terms "
        f"too: {terms}.\n"
        f"For each term give a respelling in ordinary {lang_name} orthography showing how a native {lang_name} "
        f"presenter would say it (e.g. {example}). Use the term exactly as written in the script. "
        f"Skip ordinary {lang_name} words.\n\n"
        f"Script:\n\"\"\"{text[:12000]}\"\"\"\n\n"
        f"Return JSON: {{\"terms\": {{\"<term exactly as in the script>\": \"<respelling>\"}}}}"
    )
    try:
        data, _ = llm.complete_json(prompt, timeout=180)
        raw = data.get("terms", {}) if isinstance(data, dict) else {}
        return {str(k).strip(): str(v).strip() for k, v in raw.items()
                if k and v and len(str(v)) < 80 and re.search(rf"(?<!\w){re.escape(str(k).strip())}(?!\w)", text)}
    except Exception as e:  # noqa: BLE001 — the scan still works on heuristics alone
        log.warning("pronunciation LLM pass failed: %s", e)
        return {}


def scan(text: str, lang: str, *, voice: str | None = None, use_llm: bool = True) -> list[dict[str, Any]]:
    """Terms in `text` the TTS may misread, most frequent first.

    Each item: {term, say, known, source, reasons, reason_labels, context, count}. `say` is the saved reading
    (known terms) or a suggestion (LLM) or "". Terms the owner already confirmed (`verified`) are left out.
    """
    text = unicodedata.normalize("NFC", text)
    lex_g, lex_v = N.load_lexicon(None).get(lang, {}), (N.load_lexicon(voice).get(lang, {}) if voice else {})
    lex = {**lex_g, **lex_v}
    found: dict[str, dict[str, Any]] = {}
    for m in TOKEN.finditer(text):
        tok = m.group(0).strip("'’.-_/")
        why = reasons_for(tok, lang)
        if not why:
            continue
        item = found.setdefault(tok, {"term": tok, "reasons": why, "count": 0})
        item["count"] += 1
    # "Slide" at the start of a sentence and "slide" are one term (a lower-case lexicon term matches any case).
    for tok in [t for t in found if t[:1].isupper() and t[1:].islower() and t.lower() in found]:
        found[tok.lower()]["count"] += found.pop(tok)["count"]
    # Lexicon terms that occur in the text (multi-word or ordinary-looking ones the heuristics miss).
    for term, entry in lex.items():
        if term not in found and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text):
            found[term] = {"term": term, "reasons": [], "count": len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text))}
    if use_llm:
        unknown = [t for t in found if t not in lex]
        for term, say in _llm_suggestions(text, lang, unknown).items():
            if lex.get(term, {}).get("source") == "verified":
                continue
            item = found.setdefault(term, {"term": term, "reasons": ["llm"], "count": text.count(term)})
            item.setdefault("suggest", say)
    out = []
    for term, item in found.items():
        entry = lex.get(term)
        if entry and entry.get("source") == "verified":
            continue
        item.update({
            "say": entry["say"] if entry else item.pop("suggest", ""),
            "known": bool(entry), "source": entry.get("source") if entry else None,
            "reason_labels": [REASONS[r] for r in item["reasons"]] or ["đã có trong từ điển"],
            "context": _context(text, term),
        })
        item.pop("suggest", None)
        out.append(item)
    out.sort(key=lambda x: (x["known"], -x["count"], x["term"].lower()))
    return out


def remember(lang: str, readings: dict[str, str], verified: list[str] | tuple[str, ...] = (), *,
             voice: str | None = None) -> dict[str, Any]:
    """Save owner answers from a pre-scan: readings → `manual`, confirmed-as-written → `verified`.

    Manual entries are never overwritten by automatic learning (normalize.learn).
    """
    data = N.load_lexicon(voice)
    table = data.setdefault(lang, {})
    now = time.time()
    saved, ok = {}, []
    for term, say in (readings or {}).items():
        term, say = str(term).strip(), str(say).strip()
        if not term or not say:
            continue
        prev = table.get(term, {})
        table[term] = {"say": say, "source": "manual" if say != term else "verified", "hits": prev.get("hits", 0),
                       "origin": "prescan", "added_at": now}
        saved[term] = say
    for term in verified or ():
        term = str(term).strip()
        if term and term not in saved:
            prev = table.get(term, {})
            table[term] = {"say": term, "source": "verified", "hits": prev.get("hits", 0), "origin": "prescan",
                           "added_at": now}
            ok.append(term)
    N.save_lexicon(data, voice)
    return {"lang": lang, "voice": voice, "saved": saved, "verified": ok}
