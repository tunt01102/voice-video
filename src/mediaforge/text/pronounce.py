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
    "phrase": "cụm từ nước ngoài",
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


# Reasons that let a word join its neighbours into one multi-word term ("pull request", "Claude Code", "AI agent").
PHRASE_REASONS = {"foreign", "camel", "acronym"}
PHRASE_MAX = 4


def _runs(text: str, lang: str) -> list[list[tuple[int, int, str, list[str]]]]:
    """Risky tokens, grouped: adjacent foreign/brand/acronym words separated by spaces only (no punctuation, no line
    break) form one run, so the owner ticks "tiếng Anh" once for "slash command". A run longer than PHRASE_MAX words
    (an English clause) is left as single words."""
    runs: list[list[tuple[int, int, str, list[str]]]] = []
    cur: list[tuple[int, int, str, list[str]]] = []

    def flush() -> None:
        if cur:
            runs.extend([cur[:]] if len(cur) <= PHRASE_MAX else [[t] for t in cur])
            cur.clear()

    for m in TOKEN.finditer(text):
        tok = m.group(0).strip("'’.-_/")
        why = reasons_for(tok, lang)
        if not why:
            flush()
            continue
        start = m.start() + m.group(0).index(tok) if tok else m.start()
        item = (start, start + len(tok), tok, why)
        joins = set(why) <= PHRASE_REASONS
        if cur and joins and set(cur[-1][3]) <= PHRASE_REASONS and re.fullmatch(r"[ \t]+", text[cur[-1][1]:start]):
            cur.append(item)
            continue
        flush()
        cur.append(item)
        if not joins:
            flush()
    flush()
    return runs


def _split_known(runs: list[list[tuple[int, int, str, list[str]]]], text: str,
                 known: set[str]) -> list[list[tuple[int, int, str, list[str]]]]:
    """Cut a run around the longest part that is a term on its own elsewhere ("review pull request" → "review" +
    "pull request" when "Pull request" also stands alone, or the lexicon has "pull request")."""
    phrases = known | {text[r[0][0]:r[-1][1]].casefold() for r in runs if len(r) > 1}
    out: list[list[tuple[int, int, str, list[str]]]] = []
    for run in runs:
        best = None
        for n in range(len(run) - 1, 1, -1):
            for i in range(len(run) - n + 1):
                part = run[i:i + n]
                if text[part[0][0]:part[-1][1]].casefold() in phrases:
                    best = (i, n)
                    break
            if best:
                break
        if not best:
            out.append(run)
            continue
        i, n = best
        out += [[t] for t in run[:i]] + [run[i:i + n]] + [[t] for t in run[i + n:]]
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
    spans: list[tuple[int, int]] = []  # where multi-word terms sit (their words are not counted again alone)
    for run in _split_known(_runs(text, lang), text, {t.casefold() for t in lex if " " in t}):
        if len(run) == 1:
            tok, why = run[0][2], run[0][3]
        else:
            tok, why = text[run[0][0]:run[-1][1]], ["phrase"]
            spans.append((run[0][0], run[-1][1]))
        item = found.setdefault(tok, {"term": tok, "reasons": why, "count": 0})
        item["count"] += 1
    # "Slide" at the start of a sentence and "slide" are one term (a lower-case lexicon term matches any case).
    for tok in [t for t in found if t[:1].isupper() and t[1:].islower() and t.lower() in found]:
        found[tok.lower()]["count"] += found.pop(tok)["count"]
    # Lexicon terms that occur in the text (multi-word or ordinary-looking ones the heuristics miss), counted only
    # where they are not part of a multi-word term already listed ("Claude" inside "Claude Code").
    for term, entry in lex.items():
        if term in found:
            continue
        hits = [m for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", text)
                if not any(a <= m.start() and m.end() <= b and (a, b) != m.span() for a, b in spans)]
        if hits:
            found[term] = {"term": term, "reasons": [], "count": len(hits)}
    if use_llm:
        unknown = [t for t in found if t not in lex]
        seen = {t.casefold() for t in found}
        for term, say in _llm_suggestions(text, lang, unknown).items():
            if lex.get(term, {}).get("source") == "verified" or (term not in found and term.casefold() in seen):
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
             voice: str | None = None, origin: str = "prescan",
             english: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Save owner answers from a pre-scan: readings → `manual`, confirmed-as-written → `verified`, read as English
    → `english` (N.english_terms).

    Owner entries are never overwritten by automatic learning (normalize.learn).
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
                       "origin": origin, "added_at": now}
        saved[term] = say
    for term in verified or ():
        term = str(term).strip()
        if term and term not in saved:
            prev = table.get(term, {})
            table[term] = {"say": term, "source": "verified", "hits": prev.get("hits", 0), "origin": origin,
                           "added_at": now}
            ok.append(term)
    en = []
    for term in english or ():
        term = str(term).strip()
        if term and term not in saved and term not in ok:
            prev = table.get(term, {})
            table[term] = {"say": term, "source": "english", "hits": prev.get("hits", 0), "origin": origin,
                           "added_at": now}
            en.append(term)
    N.save_lexicon(data, voice)
    return {"lang": lang, "voice": voice, "saved": saved, "verified": ok, "english": en}


OWNER_SOURCES = ("manual", "verified", "english")


def job_terms(lang: str, voice: str | None, readings: dict[str, str] | None = None,
              verified: list[str] | tuple[str, ...] = (), english: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Readings that came with a request (MCP / REST): they apply to this job only and are never saved. A term the
    owner already answered in the lexicon (manual / verified / english, global or for `voice`) keeps the owner's
    answer; the conflict is reported in `kept_owner` (term → {owner, request})."""
    lex = {**N.load_lexicon(None).get(lang, {}), **(N.load_lexicon(voice).get(lang, {}) if voice else {})}
    owned = {t.casefold(): e for t, e in lex.items() if e.get("source") in OWNER_SOURCES}
    out: dict[str, str] = {}
    eng: list[str] = []
    kept: dict[str, dict[str, str]] = {}

    def owner_says(term: str) -> str | None:
        e = owned.get(term.casefold())
        return None if e is None else ("tiếng Anh" if e.get("source") == "english" else e["say"])

    asked = [(str(t).strip(), str(v).strip()) for t, v in (readings or {}).items()]
    asked += [(str(t).strip(), str(t).strip()) for t in verified or ()]
    for term, say in asked:
        if not term or not say:
            continue
        mine = owner_says(term)
        if mine is None:
            out[term] = say
        elif mine != say:
            kept[term] = {"owner": mine, "request": say}
    for term in (str(t).strip() for t in english or ()):
        if not term:
            continue
        mine = owner_says(term)
        if mine is None:
            eng.append(term)
        elif mine != "tiếng Anh":
            kept[term] = {"owner": mine, "request": "tiếng Anh"}
    return {"scope": "job", "readings": out, "english": eng, "kept_owner": kept}
