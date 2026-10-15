"""Script lint: catch text that makes TTS fail before any GPU time is spent (docs/SDD-review-upgrade.md §5.2.1).

Two kinds of fixes:
- text fixes (L1 incomplete sentence, L5 foreign script) change the script itself and are idempotent;
- TTS-only readings (L3 acronym runs, L4 domains) change only what the engine is given, so subtitles keep the
  written form, and QA treats those spans as lenient.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from mediaforge.text.normalize import detect_lang, split_sentences

# Words a sentence cannot end on (it would be cut mid-clause: "…mà trở thành").
DANGLING = {
    # Only words that cannot close a sentence ("…đã về." / "come in." are fine, so về/đến/in/on are not here).
    "vi": {"mà", "và", "của", "để", "với", "là", "thì", "nhưng", "hoặc", "vì", "nên", "nếu", "rằng", "trở thành",
           "các", "những", "bởi", "tại", "bởi vì", "cũng như"},
    "en": {"and", "or", "but", "the", "a", "an", "of", "to", "with", "into", "which", "because", "become", "becomes",
           "than"},
}
# Above these lengths a sentence is synthesised phrase by phrase (long sentences truncate/babble; the 2026-10-15
# corpus had Vietnamese babble from ~30 words).
LONG = {"vi": 25, "en": 30}
# Places to split a long sentence that has no punctuation.
CONJ = {"vi": ["nhưng", "và", "mà", "để", "khi", "vì", "nên", "thì", "nếu", "hoặc", "rồi", "trong khi", "bởi vì"],
        "en": ["but", "and", "because", "which", "while", "when", "so", "that", "if", "or"]}
FOREIGN_SCRIPT = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯Ѐ-ӿ؀-ۿ]+")
DOMAIN = re.compile(r"(?<![\w@])(?:https?://)?(?:www\.)?[A-Za-z0-9][\w-]*(?:\.[A-Za-z0-9][\w-]*)*\.(vn|com|net|org|io|ai|edu|gov)"
                    r"(?:\.vn)?(?:/[\w./-]*)?(?![\w.])", re.I)
TLD_VI = {"vn": "vi en", "com": "com", "net": "nét", "org": "o rờ gờ", "io": "ai ô", "ai": "ây ai", "edu": "e đu",
          "gov": "gov"}
TLD_EN = {"vn": "V N", "com": "com", "net": "net", "org": "org", "io": "I O", "ai": "A I", "edu": "E D U", "gov": "gov"}
ACRONYM = re.compile(r"^[A-Z]{2,6}\d?$")
# "A.I", "S.M.E" — a closing full stop is kept when it also ends the sentence.
DOTTED = re.compile(r"(?<![\w.])[A-Z](?:\.[A-Z]){1,5}(?![\w])")

MESSAGES = {
    "L1": "Câu bị cắt giữa chừng (kết thúc bằng “{word}”)",
    "L2": "Câu dài {n} {unit} — sẽ đọc theo từng cụm cho khỏi nuốt chữ",
    "L3": "Chuỗi viết tắt liền nhau “{span}” — chèn ngắt nghỉ giữa các chữ viết tắt",
    "L4": "Địa chỉ web “{span}” — đọc là “{say}”",
    "L5": "Có ký tự ngoài tiếng Việt/Anh “{span}”",
}


def _words(s: str) -> list[str]:
    return re.findall(r"[\w'’]+", s)


def _ends_dangling(sentence: str, lang: str) -> str | None:
    core = sentence.rstrip().rstrip(".!?…,;:–- ").rstrip()
    if not core:
        return None
    low = core.lower()
    for w in sorted(DANGLING.get(lang, ()), key=len, reverse=True):
        if low == w or low.endswith(" " + w):
            return w
    return None


def _unit_count(sentence: str, lang: str) -> int:
    return len(_words(sentence))


def is_long(sentence: str, lang: str) -> bool:
    return _unit_count(sentence, lang) > LONG.get(lang, 32)


def conj_phrases(sentence: str, lang: str) -> list[str]:
    """Split a long unpunctuated sentence before conjunctions into phrases of ≥ 6 words; [sentence] if impossible."""
    words = sentence.split()
    if len(words) <= LONG.get(lang, 32):
        return [sentence]
    conj = {c.split()[0] for c in CONJ.get(lang, [])}
    cuts = [i for i, w in enumerate(words) if i >= 6 and w.lower().strip(",.") in conj]
    out, start = [], 0
    target = max(8, len(words) // max(2, round(len(words) / 18)))
    for i in cuts:
        if i - start >= target and len(words) - i >= 6:
            out.append(" ".join(words[start:i]))
            start = i
    out.append(" ".join(words[start:]))
    return out if len(out) > 1 else [sentence]


def tts_readings(sentence: str, lang: str, lexicon: dict[str, str] | None = None) -> dict[str, str]:
    """TTS-only rewrites for this sentence: acronym runs get pauses, domains get a spoken form, dotted acronyms
    ("A.I.") read like the undotted lexicon term (AI → "ây ai"); without a lexicon entry they stay as written."""
    out: dict[str, str] = {}
    for m in DOMAIN.finditer(sentence):
        out[m.group(0)] = domain_reading(m.group(0), lang)
    for m in DOTTED.finditer(sentence):
        letters = m.group(0).replace(".", "")
        # English engines read spaced capitals letter by letter ("A I"); Vietnamese needs the lexicon's reading.
        say = (lexicon or {}).get(letters) or (" ".join(letters) if lang == "en" else None)
        span = m.group(0)
        if sentence[m.end():m.end() + 1] == "." and sentence[m.end() + 1:].strip():
            span += "."  # mid-sentence the closing dot is part of the acronym, not a full stop
        if say and not any(m.group(0) in k for k in out):
            out[span] = say
    toks = sentence.split()
    i = 0
    while i < len(toks):
        j = i
        while j < len(toks) and ACRONYM.match(toks[j].strip(",.;:!?")) and (j == i or not toks[j - 1][-1] in ",.;:!?"):
            j += 1
        if j - i >= 2:
            span = " ".join(t.strip(",.;:!?") if k == j - 1 else t for k, t in enumerate(toks[i:j], start=i))
            out[span] = ", ".join(t.strip(",.;:!?") for t in toks[i:j])
            i = j
        else:
            i += 1
    return out


def domain_reading(dom: str, lang: str) -> str:
    d = re.sub(r"^(https?://)?(www\.)?", "", dom, flags=re.I).rstrip("/")
    d = d.split("/")[0]
    parts = d.split(".")
    tld = TLD_VI if lang == "vi" else TLD_EN
    dot = " chấm " if lang == "vi" else " dot "
    spoken = [tld.get(p.lower(), p) if k > 0 else p for k, p in enumerate(parts)]
    return dot.join(spoken)


def check(text: str, lang: str = "auto") -> list[dict[str, Any]]:
    """Findings for a script: [{code, index, sentence, message, fix}] — `fix` says what fix_text / TTS does."""
    text = unicodedata.normalize("NFC", text)
    out: list[dict[str, Any]] = []
    sents = split_sentences(text)
    for i, s in enumerate(sents):
        lg = detect_lang(s) if lang == "auto" else lang
        w = _ends_dangling(s, lg)
        if w:
            out.append({"code": "L1", "index": i, "sentence": s, "message": MESSAGES["L1"].format(word=w),
                        "fix": "join" if i < len(sents) - 1 else None})
        if is_long(s, lg):
            out.append({"code": "L2", "index": i, "sentence": s,
                        "message": MESSAGES["L2"].format(n=_unit_count(s, lg), unit="âm tiết" if lg == "vi" else "words"),
                        "fix": "phrases"})
        for span, say in tts_readings(s, lg).items():
            code = "L4" if DOMAIN.fullmatch(span) else "L3"
            out.append({"code": code, "index": i, "sentence": s, "fix": "tts",
                        "message": MESSAGES[code].format(span=span, say=say)})
        for m in FOREIGN_SCRIPT.finditer(s):
            out.append({"code": "L5", "index": i, "sentence": s, "message": MESSAGES["L5"].format(span=m.group(0)),
                        "fix": "strip"})
    return out


def fix_text(text: str, lang: str = "auto") -> tuple[str, list[dict[str, Any]]]:
    """Apply the text fixes (L1 join a cut sentence with the next one, L5 strip foreign script). Idempotent.

    Line structure is kept where possible; a join removes the break or full stop between the two sentences."""
    text = unicodedata.normalize("NFC", text)
    findings = check(text, lang)
    fixed = FOREIGN_SCRIPT.sub("", text)
    fixed = re.sub(r"[ \t]{2,}", " ", fixed)
    fixed = re.sub(r"[ \t]+([.,!?;:…])", r"\1", fixed)
    for f in findings:
        if f["code"] != "L1" or f["fix"] != "join":
            continue
        s = f["sentence"].rstrip()
        core = s.rstrip(".!?…").rstrip()
        # "…mà trở thành.\n Người dẫn đường" → "…mà trở thành người dẫn đường"
        pat = re.compile(re.escape(core) + r"[.!?…]*\s*\n?\s*(\S)")
        m = pat.search(fixed)
        if m:
            fixed = fixed[: m.start()] + core + " " + m.group(1) + fixed[m.end():]
    return fixed, findings


def summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f["code"]] = out.get(f["code"], 0) + 1
    return out
