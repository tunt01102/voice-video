"""Multi-dimensional review of stock voices (docs/SDD-stock-voices.md §4).

Every candidate reads one fixed Vietnamese presenter test set with two seeds. Each take is synthesised once
(sentence cache) and then shaped with every fx variant, so tuning costs ASR time, not TTS time. Measures, all local
(audio never leaves the Mac, no scoring model beyond the stack's own Whisper and VieNeu speaker encoder):

  clarity      Whisper error rate on the final audio (asr.error_rate, as QA uses)
  tones        share of syllables heard with the right letters but the wrong tone
  stability    share of sentences whose first take already passes QA
  pace         syllables per second against the project's speaking rate (speak.RATE["vi"])
  expressive   pitch movement: semitone std of voiced frames (monotone < ~1.5 st, sing-song > ~5 st)
  pauses       silent gaps (≥ 120 ms) per internal comma/colon — presenters breathe at punctuation
  consistency  cosine of each take's speaker vector to the voice's centroid (same person throughout)

The composite is a ranking aid with documented weights, not a verdict: the owner's ears decide.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import numpy as np

from mediaforge import audio as A
from mediaforge.config import settings

Progress = Callable[[float, str], None]

TEST_SET = [
    "Xin chào quý vị và các bạn, chào mừng quý vị đến với chương trình thời sự hôm nay.",
    "Theo số liệu mới nhất, tăng trưởng kinh tế quý ba đạt 7,4 phần trăm, cao nhất trong ba năm qua.",
    "Ngày 15 tháng 10 năm 2026, hội nghị thượng đỉnh đã chính thức khai mạc tại Hà Nội.",
    "Các doanh nghiệp đang đẩy mạnh ứng dụng AI và điện toán đám mây trong vận hành.",
    "Liệu thị trường bất động sản có phục hồi trong năm tới hay không?",
    "Trong bối cảnh kinh tế thế giới còn nhiều biến động, việc chủ động thích ứng, đổi mới sáng tạo và nâng cao năng "
    "lực cạnh tranh trở thành yêu cầu sống còn đối với mỗi doanh nghiệp Việt Nam.",
    "Mưa rơi lất phất trên những mái ngói rêu phong của phố cổ.",
    "Cảm ơn quý vị đã theo dõi, xin hẹn gặp lại quý vị trong bản tin tiếp theo.",
]
SEEDS = (1234, 4321)
WEIGHTS = {"clarity": 0.35, "tones": 0.20, "stability": 0.15, "pace": 0.10, "expressive": 0.10, "consistency": 0.10}
FX_GRID = [  # around the defaults: presenters sit slightly brighter and a touch quicker than a narrator
    {"pace": 1.0, "warmth": 0.2, "clarity": 0.6},
    {"pace": 1.05, "warmth": 0.2, "clarity": 0.8},
    {"pace": 0.95, "warmth": 0.4, "clarity": 0.6},
    {"pace": 1.0, "warmth": 0.4, "clarity": 0.8},
    {"pace": 1.1, "warmth": 0.2, "clarity": 0.8},   # added after run 1: every voice chose the fastest variant
]


def pitch_stats(x: np.ndarray, sr: int = A.SR) -> tuple[float, float]:
    """(median F0 in Hz, semitone std of voiced frames)."""
    import pyworld as pw

    y = A.resample(x, sr, 16000).astype(np.float64)
    f0, t = pw.dio(y, 16000, f0_floor=60.0, f0_ceil=500.0, frame_period=10.0)
    f0 = pw.stonemask(y, f0, t, 16000)
    v = f0[f0 > 0]
    if v.size < 10:
        return 0.0, 0.0
    med = float(np.median(v))
    return round(med, 1), round(float(np.std(12 * np.log2(v / med))), 2)


def silent_gaps(x: np.ndarray, sr: int = A.SR, min_ms: int = 120) -> int:
    """Silent stretches inside the utterance (leading/trailing silence excluded)."""
    hop = sr // 100
    n = len(x) // hop
    if n < 3:
        return 0
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms / (rms.max() + 1e-12))
    voiced = np.where(db > -35)[0]
    if voiced.size == 0:
        return 0
    quiet = db[voiced[0]: voiced[-1] + 1] <= -35
    gaps, run = 0, 0
    for q in quiet:
        run = run + 1 if q else 0
        if run == min_ms // 10:
            gaps += 1
    return gaps


def band(value: float, lo: float, hi: float, floor: float, ceil: float) -> float:
    """100 inside [lo, hi], falling linearly to 0 at floor / ceil."""
    if lo <= value <= hi:
        return 100.0
    if value < lo:
        return max(0.0, 100 * (value - floor) / (lo - floor))
    return max(0.0, 100 * (ceil - value) / (ceil - hi))


def take_metrics(text: str, x: np.ndarray, heard: str) -> dict[str, Any]:
    from mediaforge import asr
    from mediaforge.sing import tones
    from mediaforge.sing.pipeline import clarity

    cl = clarity(text, heard)
    base, full = (int(cl[k].split("/")[0]) for k in ("syllables", "with_tones"))
    f0, st = pitch_stats(x)
    commas = len(re.findall(r"[,;:]", text))
    return {"cer": asr.error_rate(text, heard, "vi")["score"], "tone_err": round((base - full) / max(base, 1), 3),
            "rate": round(len(tones.syllables(text)) / max(len(x) / A.SR, 0.1), 2), "f0": f0, "st_std": st,
            "gaps_per_comma": round(silent_gaps(x) / commas, 2) if commas else None, "heard": heard}


def score(rows: list[dict[str, Any]], ref_rate: float) -> dict[str, Any]:
    m = lambda k: float(np.mean([r[k] for r in rows if r.get(k) is not None] or [0]))
    gaps = [r["gaps_per_comma"] for r in rows if r.get("gaps_per_comma") is not None]
    parts = {
        "clarity": 100 * max(0.0, 1 - 2 * m("cer")),
        "tones": 100 * max(0.0, 1 - 3 * m("tone_err")),
        "stability": 100 * m("first_pass"),
        "pace": band(m("rate") / ref_rate, 0.92, 1.1, 0.7, 1.35),
        "expressive": band(m("st_std"), 1.8, 4.5, 0.5, 7.0),
        "consistency": 100 * float(np.clip((m("spk_cos") - 0.6) / 0.35, 0, 1)),
    }
    return {"composite": round(sum(WEIGHTS[k] * v for k, v in parts.items()), 1),
            "parts": {k: round(v, 1) for k, v in parts.items()},
            "means": {k: round(m(k), 3) for k in ("cer", "tone_err", "first_pass", "rate", "f0", "st_std", "spk_cos")},
            "gaps_per_comma": round(float(np.mean(gaps)), 2) if gaps else None}


def run(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge import asr
    from mediaforge.tts.base import get_engine
    from mediaforge.voice import fx as FX
    from mediaforge.voice import profiles, stock
    from mediaforge.voice import speak as S

    stock.ensure_profiles()
    ids = p.get("voices") or [v["id"] for v in stock.stock_voices()]
    if p.get("reference"):
        ids = [*ids, p["reference"]]   # the owner's own voice as a yardstick (not ranked)
    grid = p.get("fx_grid") or ([None] if p.get("no_grid") else FX_GRID)
    texts = p.get("texts") or TEST_SET
    seeds = tuple(p.get("seeds") or SEEDS)
    spk = get_engine("vieneu")
    thr = settings.qa_max_cer_vi
    results, t0, total = {}, time.time(), len(ids) * len(texts) * len(seeds)
    k = 0
    for vid in ids:
        v = profiles.get_voice(vid)
        takes = []
        for text in texts:
            for sd in seeds:
                progress(k / total, f"{v['name']}: {text[:30]}")
                k += 1
                audio, rep = S.synth_sentence(text, "vi", vid, seed=sd, learn=False)
                takes.append((text, audio, rep))
        variants = []
        for g in grid:
            fxp = FX.VoiceFX.from_dict({**(v.get("fx") or {}), **(g or {})})
            rows, embs = [], []
            for text, audio, rep in takes:
                x = FX.shape(audio, fxp)
                heard = asr.transcribe(x, lang="vi", temperature=0.0)["text"]
                row = take_metrics(text, x, heard)
                row["first_pass"] = float(rep["attempts"][0]["score"] <= thr)
                row["attempts"] = len(rep["attempts"])
                embs.append(spk.speaker_embedding(x, A.SR))
                rows.append(row)
            E = np.stack([e / (np.linalg.norm(e) + 1e-9) for e in embs])
            c = E.mean(0)
            for row, e in zip(rows, E):
                row["spk_cos"] = float(e @ (c / (np.linalg.norm(c) + 1e-9)))
            variants.append({"fx": fxp.as_dict(), **score(rows, S.RATE["vi"]), "rows": rows})
        variants.sort(key=lambda r: -r["composite"])
        results[vid] = {"name": v["name"], "gender": v.get("gender"), "kind": v.get("kind", "voice"),
                        "best": {k2: variants[0][k2] for k2 in ("fx", "composite", "parts", "means", "gaps_per_comma")},
                        "variants": variants}
    ranking = sorted(((r["best"]["composite"], vid) for vid, r in results.items() if r["kind"] == "stock"), reverse=True)
    summary = {"ranking": [{"id": vid, "name": results[vid]["name"], "gender": results[vid]["gender"], "composite": c,
                            **results[vid]["best"]["parts"]} for c, vid in ranking],
               "reference": {vid: results[vid]["best"]["composite"] for vid in results if results[vid]["kind"] != "stock"},
               "elapsed": round(time.time() - t0, 1)}
    f = settings.sub("evals") / f"stock-{time.strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps({"summary": summary, "voices": results}, ensure_ascii=False, indent=2, default=float))
    if p.get("apply"):  # keep the best fx per stock voice (previews then rebuild as stale)
        for vid, r in results.items():
            if r["kind"] == "stock":
                profiles.update_voice(vid, {"fx": {k2: r["best"]["fx"][k2] for k2 in ("pitch", "pace", "warmth", "clarity")}})
    progress(1.0, "done")
    return {"summary": summary, "file": str(f)}
