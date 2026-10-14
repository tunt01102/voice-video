"""Language detection, text normalisation, pronunciation lexicon and sentence splitting."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from mediaforge.config import settings

VI_CHARS = set(
    "ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
)
VI_WORDS = {"và", "của", "là", "không", "có", "những", "các", "một", "cho", "với", "này", "được", "người", "trong"}


def detect_lang(text: str) -> str:
    t = text.lower()
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return "en"
    vi_ratio = sum(c in VI_CHARS for c in letters) / len(letters)
    if vi_ratio > 0.03 or len(VI_WORDS & set(re.findall(r"\w+", t))) >= 2:
        return "vi"
    return "en"


@lru_cache(maxsize=1)
def _vi_normalizer():
    from vietnormalizer import VietnameseNormalizer

    return VietnameseNormalizer()


_EN_UNITS = {"%": " percent", "&": " and ", "$": " dollars "}


def normalise(text: str, lang: str) -> str:
    """Turn written text into speakable text (numbers, dates, symbols)."""
    text = unicodedata.normalize("NFC", text).strip()
    if lang == "vi":
        return _vi_normalizer().normalize(text)
    for k, v in _EN_UNITS.items():
        text = text.replace(k, v)
    return re.sub(r"\s+", " ", text).strip()


def qa_tokens(text: str, lang: str) -> list[str]:
    """Tokens used to compare intended text against what ASR heard."""
    t = normalise(text, lang).lower()
    t = re.sub(r"[^\w\s']", " ", t).replace("_", " ")
    return t.split()


# ---------------------------------------------------------------- lexicon

def _lexicon_path(voice_id: str | None) -> Path:
    if voice_id:
        return settings.sub("voices", voice_id) / "lexicon.json"
    return settings.sub("lexicon") / "global.json"


DEFAULT_LEXICON = {
    "vi": {"AI": "ây ai", "API": "ây pi ai", "Claude": "clốt", "GitHub": "gít háp", "MediaForge": "mê đi a pho"},
    "en": {"MediaForge": "media forge", "Block8": "Block eight"},
}


def load_lexicon(voice_id: str | None = None) -> dict[str, dict[str, dict]]:
    """Returns {lang: {term: {"say": str, "source": "manual"|"verified"|"learned"|"default", "hits": int}}}.

    verified = the owner confirmed the term reads correctly as written (say == term); pre-scans skip it.
    """
    p = _lexicon_path(voice_id)
    if not p.exists():
        if voice_id:
            return {"vi": {}, "en": {}}
        data = {lang: {k: {"say": v, "source": "default", "hits": 0} for k, v in d.items()} for lang, d in DEFAULT_LEXICON.items()}
        save_lexicon(data, None)
        return data
    data = json.loads(p.read_text("utf-8"))
    data.setdefault("vi", {})
    data.setdefault("en", {})
    return data


def save_lexicon(data: dict, voice_id: str | None = None) -> None:
    _lexicon_path(voice_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def merged_lexicon(voice_id: str | None, lang: str) -> dict[str, str]:
    merged = {k: v["say"] for k, v in load_lexicon(None).get(lang, {}).items()}
    if voice_id:
        merged.update({k: v["say"] for k, v in load_lexicon(voice_id).get(lang, {}).items()})
    return merged


def learn(voice_id: str | None, lang: str, term: str, say: str) -> None:
    data = load_lexicon(voice_id)
    entry = data[lang].get(term)
    if entry and entry.get("source") in ("manual", "verified"):
        return  # never overwrite a human decision
    data[lang][term] = {"say": say, "source": "learned", "hits": (entry or {}).get("hits", 0) + 1}
    save_lexicon(data, voice_id)


def term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word match; a term with a capital (PNJ) is case-sensitive, a lower-case one (pháp lý) is not."""
    flags = 0 if any(c.isupper() for c in term) else re.I
    return re.compile(rf"(?<!\w){re.escape(unicodedata.normalize('NFC', term))}(?!\w)", flags)


def terms_in(text: str, terms) -> list[str]:
    """The lexicon terms apply_lexicon would rewrite in `text`."""
    text = unicodedata.normalize("NFC", text)
    return [t for t in terms if t and term_pattern(t).search(text)]


def apply_lexicon(text: str, lexicon: dict[str, str], extra: dict[str, str] | None = None) -> str:
    table = {**lexicon, **(extra or {})}
    for term in sorted(table, key=len, reverse=True):
        text = term_pattern(term).sub(lambda _m, say=table[term]: say, text)
    return text


# ---------------------------------------------------------------- splitting

def split_sentences(text: str, max_chars: int = 220) -> list[str]:
    text = re.sub(r"[ \t]+", " ", unicodedata.normalize("NFC", text)).strip()
    parts: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        for s in re.split(r"(?<=[.!?…])\s+", para):
            s = s.strip()
            if not s:
                continue
            while len(s) > max_chars:
                cut = max(s.rfind(p, 0, max_chars) for p in (", ", "; ", ": ", " – ", " - "))
                if cut < max_chars // 3:
                    cut = s.rfind(" ", 0, max_chars)
                if cut <= 0:
                    cut = max_chars
                parts.append(s[: cut + 1].strip())
                s = s[cut + 1 :].strip()
            if s:
                parts.append(s)
    return parts
