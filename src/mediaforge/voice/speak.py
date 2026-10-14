"""Sentence-level synthesis with ASR quality control and pronunciation self-repair."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from mediaforge import audio as A
from mediaforge import asr, llm
from mediaforge.config import settings
from mediaforge.text import normalize as N
from mediaforge.tts.base import DEFAULT_ROUTE, engines_for, get_engine, self_normalises
from mediaforge.voice import fx as FX
from mediaforge.voice import profiles

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]

# Typical speaking rates (tokens per second) used to catch babble / truncation.
RATE = {"vi": 5.2, "en": 2.7}


@dataclass
class Attempt:
    engine: str
    seed: int
    temperature: float
    tts_text: str
    respell: dict[str, str] = field(default_factory=dict)
    score: float = 1.0
    heard: str = ""
    qa: dict[str, Any] = field(default_factory=dict)
    audio: np.ndarray | None = None
    seconds: float = 0.0


def _threshold(lang: str) -> float:
    return settings.qa_max_cer_vi if lang == "vi" else settings.qa_max_wer_en


def _duration_penalty(text: str, lang: str, audio: np.ndarray) -> float:
    n = max(1, len(N.qa_tokens(text, lang)))
    expected = n / RATE.get(lang, 3.5)
    actual = len(audio) / A.SR
    ratio = actual / max(expected, 0.3)
    if ratio > 2.3 or ratio < 0.35:
        return 0.5
    return 0.0


def _respell_with_llm(sentence: str, lang: str, qa: dict[str, Any], heard: str) -> dict[str, str]:
    lang_name = "Vietnamese" if lang == "vi" else "English"
    prompt = (
        f"A {lang_name} text-to-speech engine mispronounced part of a sentence.\n"
        f"Intended sentence: {sentence}\n"
        f"Speech recogniser heard: {heard}\n"
        f"Words missing or wrong: {qa.get('missing')}\n"
        f"Heard instead: {qa.get('extra')}\n\n"
        f"For each problematic word or term from the INTENDED sentence, give a phonetic respelling written in "
        f"ordinary {lang_name} orthography that a {lang_name} TTS will pronounce correctly "
        f"(e.g. Vietnamese: 'Claude' -> 'clốt', 'API' -> 'ây pi ai'; English: 'Nguyen' -> 'Win'). "
        f"Keep the respelling minimal. Return JSON: {{\"respellings\": {{\"<term exactly as in the intended sentence>\": \"<respelling>\"}}}}"
    )
    try:
        data, _ = llm.complete_json(prompt, timeout=120)
        resp = data.get("respellings", {}) if isinstance(data, dict) else {}
        return {k: str(v) for k, v in resp.items() if k and v and k in sentence and len(str(v)) < 80}
    except Exception as e:  # noqa: BLE001
        log.warning("respelling LLM failed: %s", e)
        return {}


def _tts_text(sentence: str, lang: str, engine_name: str, lexicon: dict[str, str], respell: dict[str, str]) -> str:
    text = N.apply_lexicon(sentence, lexicon, respell)
    return text if self_normalises(engine_name, lang) else N.normalise(text, lang)


def _run_attempt(att: Attempt, sentence: str, lang: str, ref, lenient: set[str]) -> Attempt:
    engine = get_engine(att.engine)
    t = time.time()
    raw = engine.synth(att.tts_text, lang, ref, seed=att.seed, temperature=att.temperature)
    x = A.trim_silence(A.resample(raw, engine.sample_rate, A.SR))
    att.seconds = round(time.time() - t, 2)
    # Hint rare spellings to Whisper so correct brand names are not reported as mistakes.
    hint = ", ".join(sorted(lenient)) if lenient else None
    heard = asr.transcribe(x, lang=lang, initial_prompt=hint)["text"]
    qa = asr.error_rate(sentence, heard, lang, lenient)
    att.audio, att.heard, att.qa = x, heard, qa
    att.score = round(qa["score"] + _duration_penalty(sentence, lang, x), 4)
    return att


def synth_sentence(
    sentence: str, lang: str, voice_id: str | None, *, engine: str | None = None, seed: int = 1234,
    max_attempts: int | None = None, learn: bool = True,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Synthesise one sentence; retry/repair until it passes ASR QA. Returns (audio48k, report)."""
    max_attempts = max_attempts or settings.qa_max_attempts
    voice = profiles.get_voice(voice_id) if voice_id else None
    ref = profiles.voice_ref(voice_id, lang)
    lexicon = N.merged_lexicon(voice_id, lang)
    capable = engines_for(lang)
    if engine == "best":
        plan_engines = capable
    else:
        preferred = engine or (voice or {}).get("engines", {}).get(lang) or DEFAULT_ROUTE[lang]
        plan_engines = [preferred]
    alternates = [e for e in capable if e not in plan_engines]

    # Sentence-level cache: identical requests are free.
    key_src = json.dumps([sentence, lang, voice_id, ref.audio_path if ref else None, plan_engines, seed,
                          {k: v for k, v in lexicon.items() if k in sentence}], ensure_ascii=False)
    key = hashlib.sha256(key_src.encode()).hexdigest()[:24]
    cache_dir = settings.sub("cache", "tts")
    if (cache_dir / f"{key}.wav").exists() and (cache_dir / f"{key}.json").exists():
        return A.load(cache_dir / f"{key}.wav"), {**json.loads((cache_dir / f"{key}.json").read_text()), "cached": True}

    attempts: list[Attempt] = []
    thr = _threshold(lang)
    lenient = asr.lenient_terms(sentence, {k for k in lexicon if k in sentence})

    def best() -> Attempt:
        return min(attempts, key=lambda a: a.score)

    def run(att: Attempt) -> Attempt:
        nonlocal ref
        try:
            return _run_attempt(att, sentence, lang, ref, lenient)
        except Exception:
            # The reference clip was deleted while this job ran: re-resolve (falls back to a clip that exists) once.
            if not ref or Path(ref.audio_path).exists():
                raise
            stale, ref = ref.audio_path, profiles.voice_ref(voice_id, lang)
            log.warning("reference %s vanished mid-job; retrying with %s", stale, ref.audio_path if ref else None)
            return _run_attempt(att, sentence, lang, ref, lenient)

    # 1) first pass with each planned engine
    for e in plan_engines:
        attempts.append(run(Attempt(e, seed, 0.8, _tts_text(sentence, lang, e, lexicon, {}))))
    # 2) repair loop
    step = 0
    while best().score > thr and len(attempts) < max_attempts + len(plan_engines) - 1:
        b = best()
        if step == 0:          # resample with a calmer temperature
            att = Attempt(b.engine, seed + 17, 0.55, b.tts_text, b.respell)
        elif step == 1 and alternates:  # switch engine
            e = alternates[0]
            att = Attempt(e, seed, 0.7, _tts_text(sentence, lang, e, lexicon, b.respell), b.respell)
        else:                  # ask the LLM for phonetic respellings of the misheard words
            respell = {**b.respell, **_respell_with_llm(sentence, lang, b.qa, b.heard)}
            if respell == b.respell:
                att = Attempt(b.engine, seed + 101 * (step + 1), 0.6, b.tts_text, b.respell)
            else:
                att = Attempt(b.engine, seed + 7, 0.6, _tts_text(sentence, lang, b.engine, lexicon, respell), respell)
        attempts.append(run(att))
        step += 1

    chosen = best()
    if learn and chosen.respell and chosen.score <= thr:
        for term, say in chosen.respell.items():
            N.learn(voice_id, lang, term, say)
    report = {
        "text": sentence, "lang": lang, "engine": chosen.engine, "score": chosen.score, "passed": chosen.score <= thr,
        "heard": chosen.heard, "respell": chosen.respell, "source": "tts",
        "missing": chosen.qa.get("missing", []), "extra": chosen.qa.get("extra", []), "attempts": [
            {"engine": a.engine, "seed": a.seed, "temperature": a.temperature, "score": a.score, "heard": a.heard,
             "respell": a.respell, "seconds": a.seconds} for a in attempts
        ],
    }
    A.save(cache_dir / f"{key}.wav", chosen.audio)  # type: ignore[arg-type]
    (cache_dir / f"{key}.json").write_text(json.dumps(report, ensure_ascii=False))
    return chosen.audio, report  # type: ignore[return-value]


def _key(sentence: str) -> str:
    """Stable identity for a sentence across re-renders (whitespace/case-insensitive)."""
    import unicodedata

    return " ".join(unicodedata.normalize("NFC", sentence).lower().split())


def speak(
    text: str, *, voice: str | None = None, lang: str = "auto", engine: str | None = None,
    fx: dict | None = None, out_dir: str | Path | None = None, name: str = "voice", seed: int = 1234,
    progress: Progress | None = None, timings: bool = True,
    overrides: dict[str, str] | None = None, regen: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Text → finished voice track (wav + mp3) with QA report and word timings.

    overrides: {sentence text: recording path} — the owner's own recording replaces that sentence.
    regen:     {sentence text: n} — re-synthesise that sentence with a fresh seed (n-th retry).
    """
    overrides = {_key(k): v for k, v in (overrides or {}).items()}
    regen = {_key(k): int(v) for k, v in (regen or {}).items()}
    progress = progress or (lambda p, m: None)
    out_dir = Path(out_dir or settings.sub("outputs", "tts", time.strftime("%Y%m%d-%H%M%S")))
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = voice or profiles.default_voice()
    vprof = profiles.get_voice(voice) if voice else None
    fxp = FX.VoiceFX.from_dict({**((vprof or {}).get("fx") or {}), **(fx or {})})
    sentences = N.split_sentences(text)
    if not sentences:
        raise ValueError("empty text")
    chunks, reports, gaps = [], [], []
    t0 = time.time()
    for i, s in enumerate(sentences):
        s_lang = N.detect_lang(s) if lang == "auto" else lang
        progress(i / len(sentences) * 0.85, f"sentence {i + 1}/{len(sentences)}")
        if _key(s) in overrides and Path(overrides[_key(s)]).exists():
            audio = profiles.clean_recording(overrides[_key(s)])
            rep = {"text": s, "lang": s_lang, "engine": "recording", "score": 0.0, "passed": True, "source": "recording",
                   "heard": "", "respell": {}, "missing": [], "extra": [], "attempts": []}
        else:
            audio, rep = synth_sentence(s, s_lang, voice, engine=engine, seed=seed + i + 7919 * regen.get(_key(s), 0))
        rep = {"source": "tts", "missing": [], "extra": [], **rep, "index": i}  # older cache entries lack these
        chunks.append(audio)
        reports.append(rep)
        gaps.append(0.32 if s.rstrip()[-1:] in ".!?…" else 0.16)
    progress(0.88, "shaping voice")
    joined = A.crossfade_concat(chunks, gaps[:-1])
    shaped = FX.shape(joined, fxp)
    wav = FX.finalise(shaped, fxp, out_dir / f"{name}.wav")
    mp3 = FX.to_mp3(wav, out_dir / f"{name}.mp3")
    duration = A.probe_duration(wav)
    words: list[dict] = []
    if timings:
        progress(0.94, "word timings")
        main_lang = max(("vi", "en"), key=lambda l: sum(r["lang"] == l for r in reports))
        tr = asr.transcribe(wav, lang=main_lang if lang != "auto" or len({r['lang'] for r in reports}) == 1 else None)
        words = [w.as_dict() for w in asr.align_script(" ".join(sentences), tr["words"], duration)]
    result = {
        "audio": str(wav), "mp3": str(mp3), "duration": round(duration, 2), "voice": voice,
        "fx": fxp.as_dict(), "sentences": reports, "words": words, "voice_id": voice,
        "qa_passed": all(r["passed"] for r in reports),
        "qa_failed_sentences": [r["text"] for r in reports if not r["passed"]],
        "elapsed": round(time.time() - t0, 1),
    }
    (out_dir / f"{name}.report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return result
