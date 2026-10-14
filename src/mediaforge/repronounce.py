"""Fix how a few words are read in a finished job — without re-rendering the video.

The owner types `term → reading` (e.g. `PNJ → Pi En Di`). The reading is saved to the lexicon, every sentence of
the job that contains a term is re-synthesised as a take (same path as 🔁 Tạo lại), and the takes are spliced into
the voice track and remuxed into a new export. Visuals, subtitles and timing are untouched.
See docs/SDD-repronounce.md.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from mediaforge import export
from mediaforge import takes as T
from mediaforge.text import normalize as N
from mediaforge.text import pronounce

Progress = Callable[[float, str], None]


def clean_readings(readings: dict[str, Any] | None) -> dict[str, str]:
    """{term: reading} with blanks dropped and text NFC-normalised (the script is NFC)."""
    import unicodedata

    out = {}
    for term, say in (readings or {}).items():
        term = unicodedata.normalize("NFC", str(term or "").strip())
        say = unicodedata.normalize("NFC", str(say or "").strip())
        if term and say and say != term:
            out[term] = say
    return out


def occurrences(sentences: list[dict[str, Any]], terms) -> dict[str, list[int]]:
    """{term: [sentence index…]} — the sentences whose TTS text the term rewrites (same matcher as apply_lexicon)."""
    terms = list(terms)
    hits: dict[str, list[int]] = {t: [] for t in terms}
    for i, s in enumerate(sentences):
        for t in N.terms_in(s.get("text") or "", terms):
            hits[t].append(i)
    return hits


def save_readings(readings: dict[str, str], langs: set[str], voice_id: str | None) -> dict[str, Any]:
    """Save to the global lexicon as `manual`. A voice-scoped entry for the same term would shadow it
    (normalize.merged_lexicon), so it is updated too."""
    saved: dict[str, Any] = {}
    for lang in sorted(langs):
        saved[lang] = pronounce.remember(lang, readings, origin="repronounce")["saved"]
        if voice_id:
            shadowed = {t: s for t, s in readings.items() if t in N.load_lexicon(voice_id).get(lang, {})}
            if shadowed:
                pronounce.remember(lang, shadowed, voice=voice_id, origin="repronounce")
    return saved


def base_selection(sentences: list[dict[str, Any]]) -> dict[int, str]:
    """What the dashboard selects by default: the latest take that passed QA, per sentence."""
    sel = {}
    for i, s in enumerate(sentences):
        ok = [t for t in s.get("takes") or [] if t.get("passed")]
        if ok:
            sel[i] = ok[-1]["id"]
    return sel


def run(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """p: {job, readings: {term: reading}, format?}."""
    src_id = p["job"]
    job = T.load_done_job(src_id)
    r = job["result"]
    sents = r.get("sentences") or []
    readings = clean_readings(p.get("readings"))
    if not readings:
        raise ValueError("nhập ít nhất một từ và cách đọc mới (khác với từ gốc)")
    hits = occurrences(sents, readings)
    missing = [t for t, idx in hits.items() if not idx]
    if len(missing) == len(readings):
        raise ValueError(f"không tìm thấy {', '.join(missing)} trong lời đọc của video này")
    indices = sorted({i for idx in hits.values() for i in idx})

    progress(0.02, "lưu cách đọc vào từ điển phát âm")
    langs = {sents[i].get("lang") or r.get("lang") or "vi" for i in indices}
    saved = save_readings({t: s for t, s in readings.items() if hits[t]}, langs, T.job_voice(job))

    new_takes: dict[int, dict[str, Any]] = {}
    span = 0.85 / len(indices)
    for k, i in enumerate(indices):
        lo = 0.05 + k * span
        sub = lambda f, msg, lo=lo, k=k: progress(lo + f * span, f"câu {k + 1}/{len(indices)}: {msg}")  # noqa: E731
        used = {t: readings[t] for t in N.terms_in(sents[i]["text"], readings)}
        res = T.rerender({"job": src_id, "index": i, "source": "repronounce", "readings": used}, sub, job_id)
        new_takes[i] = res["take"]

    progress(0.9, "ghép các câu mới vào video")
    fresh = T.load_done_job(src_id)["result"]
    # The owner asked for this reading: select the new take even when QA is unsure (a respelled acronym is often
    # heard back as letters); the per-sentence score is reported so a real failure is visible.
    selection = {**base_selection(fresh["sentences"]), **{i: t["id"] for i, t in new_takes.items()}}
    exp = export.run({"job": src_id, "selection": selection, "format": p.get("format") or "mp4"},
                     lambda f, msg: progress(0.9 + 0.09 * f, msg), job_id)

    entry = {
        "at": time.time(), "readings": readings, "not_found": missing, "lexicon": saved,
        "sentences": [{"index": i, "text": sents[i]["text"], "take": t["id"], "score": t["score"],
                       "passed": t["passed"], "heard": t["heard"]} for i, t in new_takes.items()],
        "export": exp["export"]["id"], "file": exp["file"], "path": exp["export"]["path"],
        "repronounce_job": job_id,
    }
    fresh = T.load_done_job(src_id)["result"]
    fresh["repronounced"] = [*(fresh.get("repronounced") or []), entry]
    T.save_result(src_id, fresh)
    progress(1.0, "done")
    return {"job": src_id, **entry, "selection": {str(k): v for k, v in selection.items()}}
