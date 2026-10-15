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
from mediaforge import asr, lessons, llm
from mediaforge.config import settings
from mediaforge.text import lint as L
from mediaforge.text import normalize as N
from mediaforge.tts.base import DEFAULT_ROUTE, VoiceRef, blend_engines, engines_for, get_engine, self_normalises
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
    # Phrase-wise synthesis: each phrase synthesised alone, joined with these pauses (seconds).
    phrases: list[str] | None = None
    tts_phrases: list[str] | None = None
    pauses: list[float] = field(default_factory=list)
    prompt: VoiceRef | None = None   # prosody guide (owner's recording of this sentence)
    strategy: str = "whole"


# Pause after an internal punctuation mark when splitting a sentence into phrases.
PUNCT_PAUSE = {",": 0.16, ";": 0.22, ":": 0.22, "!": 0.24, "–": 0.18, "-": 0.18}


def punct_phrases(sentence: str) -> tuple[list[str], list[float]]:
    """Split a long sentence at internal punctuation; every phrase keeps >= 2 words."""
    import re

    pieces = [p for p in re.split(r"(?<=[,;:!–-])\s+", sentence.strip()) if p]
    phrases: list[str] = []
    pauses: list[float] = []
    for piece in pieces:
        if phrases and len(piece.split()) < 2:
            phrases[-1] += " " + piece
            continue
        if phrases and len(phrases[-1].split()) < 2:
            phrases[-1] += " " + piece
            continue
        if phrases:
            pauses.append(PUNCT_PAUSE.get(phrases[-1].rstrip()[-1:], 0.16))
        phrases.append(piece)
    return (phrases, pauses) if len(phrases) > 1 else ([sentence], [])


def _threshold(lang: str) -> float:
    return settings.qa_max_cer_vi if lang == "vi" else settings.qa_max_wer_en


def _duration_ratio(text: str, lang: str, audio: np.ndarray) -> float:
    n = max(1, len(N.qa_tokens(text, lang)))
    expected = n / RATE.get(lang, 3.5)
    return (len(audio) / A.SR) / max(expected, 0.3)


def _duration_penalty(text: str, lang: str, audio: np.ndarray) -> float:
    ratio = _duration_ratio(text, lang, audio)
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

    def synth(text: str, seed: int) -> np.ndarray:
        kw = {"prompt": att.prompt} if att.prompt is not None else {}
        raw = engine.synth(text, lang, ref, seed=seed, temperature=att.temperature, **kw)
        return A.trim_silence(A.resample(raw, engine.sample_rate, A.SR))

    if att.tts_phrases and len(att.tts_phrases) > 1:
        x = A.crossfade_concat([synth(t, att.seed + k) for k, t in enumerate(att.tts_phrases)], att.pauses)
    else:
        x = synth(att.tts_text, att.seed)
    att.seconds = round(time.time() - t, 2)
    # Hint rare spellings to Whisper so correct brand names are not reported as mistakes. Greedy decoding: the
    # sampling fallback is where Whisper hallucinates (CJK on Vietnamese audio).
    hint = ", ".join(sorted(lenient)) if lenient else None
    heard = asr.transcribe(x, lang=lang, initial_prompt=hint, temperature=0.0)["text"]
    judge_retry = False
    if asr.judge_error(heard, lang, sentence):
        # The judge failed, not necessarily the voice: listen again primed with the intended sentence (F3).
        judge_retry = True
        heard = asr.transcribe(x, lang=lang, initial_prompt=((hint + ". ") if hint else "") + sentence,
                               temperature=0.0)["text"]
    qa = asr.error_rate(sentence, heard, lang, lenient)
    qa["judge_retry"] = judge_retry
    qa["judge"] = asr.judge_error(heard, lang, sentence)
    qa["repeat"] = asr.repetition(sentence, heard, lang)
    qa["dur_ratio"] = round(_duration_ratio(sentence, lang, x), 2)
    att.audio, att.heard, att.qa = x, heard, qa
    att.score = round(qa["score"] + _duration_penalty(sentence, lang, x), 4)
    return att


def classify(att: Attempt, sentence: str, lang: str) -> str:
    """Failure class of an attempt (SDD-review-upgrade §3.2): repeat | judge | truncated | terms | other."""
    from mediaforge.text.pronounce import is_vi_syllable

    qa = att.qa
    if qa.get("repeat"):
        return "repeat"
    if qa.get("judge"):
        return "judge"
    ref, hyp = N.qa_tokens(sentence, lang), N.qa_tokens(att.heard, lang)
    missing = " ".join(qa.get("missing", [])).split()
    if (qa.get("dur_ratio") or 1) < 0.7 or (ref and missing and ref[-1] in missing and len(hyp) < len(ref)):
        return "truncated"
    raw_missing = [w for w in sentence.split() if w.strip(",.;:!?").lower() in set(missing)]
    if any(w.strip(",.;:!?").isupper() or (lang == "vi" and not is_vi_syllable(w.strip(",.;:!?"))) for w in raw_missing):
        return "terms"
    return "other"


def expected_class(sentence: str, lang: str) -> str | None:
    """The failure class a sentence is likely to hit before any synthesis: terms (acronyms, non-Vietnamese words) or
    truncated (long sentences); None when nothing stands out."""
    from mediaforge.text.pronounce import is_vi_syllable

    words = [w.strip(",.;:!?\"'()") for w in sentence.split()]
    if any(sum(c.isupper() for c in w) >= 2 or (lang == "vi" and w.isalpha() and not is_vi_syllable(w)) for w in words):
        return "terms"
    return "truncated" if L.is_long(sentence, lang) else None


# Which repair strategy to try next for each failure class; each strategy has a use limit per sentence.
REPAIR_ORDER = {
    "repeat": ["reseed", "engine", "phrases", "respell"],
    "judge": ["reseed", "engine", "phrases", "respell"],
    "truncated": ["phrases", "engine", "reseed", "respell"],
    "terms": ["respell", "engine", "reseed", "phrases"],
    "other": ["reseed", "engine", "phrases", "respell"],
}
REPAIR_LIMIT = {"reseed": 2, "engine": 1, "phrases": 1, "respell": 2}
# Stop repairing after this many attempts in a row that do not improve the best score.
STALL_LIMIT = 2


def synth_sentence(
    sentence: str, lang: str, voice_id: str | None, *, engine: str | None = None, seed: int = 1234,
    max_attempts: int | None = None, learn: bool = True, ref_path: str | None = None,
    guide: dict[str, Any] | None | bool = None, phrase_first: bool = False, cache: bool = True,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Synthesise one sentence; retry/repair until it passes ASR QA. Returns (audio48k, report).

    ref_path:     pin the reference clip (re-rendering a sentence of an earlier job keeps its timbre).
    guide:        the owner's recording of this sentence (profiles.add_correction entry). None = look it up,
                  False = do not use one. A guide is reference data only, never part of the output.
    phrase_first: also try phrase-wise synthesis (split at punctuation) in the first pass.
    """
    max_attempts = max_attempts or settings.qa_max_attempts
    voice = profiles.get_voice(voice_id) if voice_id else None
    ref = profiles.ref_from_path(voice_id, ref_path, lang) if ref_path else None
    ref = ref or profiles.voice_ref(voice_id, lang)
    # A blend profile (pha giọng) borrows its owner's lexicon, guides and learned respellings.
    base_id = profiles.base_voice(voice_id)
    if guide is None:
        guide = profiles.guide_for(base_id, sentence)
    guide = guide or None
    lexicon = N.merged_lexicon(base_id, lang)
    capable = engines_for(lang)
    if ref is not None and ref.blend is not None:
        # Only engines with a speaker vector can render the blend; never fall back to one that would drop it.
        capable = blend_engines(lang)
        if not capable:
            raise ValueError(f"no engine can render a blended voice in {lang!r}")
        if engine and engine not in capable:
            log.info("blend voice %s: engine %s cannot blend, using %s", voice_id, engine, capable[0])
            engine = None
    stock = (voice or {}).get("kind") == "stock"
    if stock:
        # A stock voice (preset speaker / designed voice) exists in one engine only: no alternates, no "best".
        capable, engine = [voice["engine"]], voice["engine"]  # type: ignore[index]
    # "best" (try every capable engine) may come from the request or from the voice's saved routing.
    preferred = engine or (voice or {}).get("engines", {}).get(lang) or DEFAULT_ROUTE[lang]
    if ref is not None and ref.blend is not None and preferred not in capable:
        preferred = capable[0]
    if not engine and preferred != "best" and preferred not in capable:
        log.warning("voice %s routes %s to unknown engine %r; using %s", voice_id, lang, preferred, DEFAULT_ROUTE[lang])
        preferred = DEFAULT_ROUTE[lang]
    plan_engines = capable if preferred == "best" else [preferred]
    alternates = [e for e in capable if e not in plan_engines]
    # An active engine_route lesson for the class this sentence is likely to fail with puts that engine first; with
    # "best", a perfect take on it (score 0) ends the first pass early (SDD-review-upgrade §5.1.3).
    route = lessons.route(base_id, lang, expected_class(sentence, lang)) if len(plan_engines) > 1 and not guide else None
    if route and route[0] in plan_engines:
        plan_engines = [route[0]] + [e for e in plan_engines if e != route[0]]
    else:
        route = None

    # What the engine is given: lint's TTS-only readings (acronym runs, domains) < learned readings (lessons,
    # per engine) < the owner's lexicon. Subtitles keep the written sentence.
    lint_readings = L.tts_readings(sentence, lang, lexicon)
    lesson_readings = {e: {t: v for t, v in lessons.readings(base_id, lang, e).items() if N.terms_in(sentence, [t])}
                       for e in capable}

    def lex_for(e: str) -> dict[str, str]:
        return {**lint_readings, **{t: v[0] for t, v in lesson_readings.get(e, {}).items()}, **lexicon}

    judged = {t for t in lessons.judge_terms(base_id, lang) if N.terms_in(sentence, [t])}
    phrase_first = phrase_first or L.is_long(sentence, lang)

    # Sentence-level cache: identical requests are free. A new guide changes the key, so a sentence the owner
    # corrected is re-synthesised with the guide instead of returning the cached failure.
    key_src = json.dumps([sentence, lang, voice_id, ref.key if ref else None, plan_engines, seed,
                          ref.blend.key if ref and ref.blend else None,
                          {k: lexicon[k] for k in N.terms_in(sentence, lexicon)}, lint_readings,
                          {e: {t: v[0] for t, v in d.items()} for e, d in lesson_readings.items()}, sorted(judged)]
                         + ([guide["id"]] if guide else []) + (["phrases"] if phrase_first else []), ensure_ascii=False)
    key = hashlib.sha256(key_src.encode()).hexdigest()[:24]
    cache_dir = settings.sub("cache", "tts")
    if cache and (cache_dir / f"{key}.wav").exists() and (cache_dir / f"{key}.json").exists():
        return A.load(cache_dir / f"{key}.wav"), {**json.loads((cache_dir / f"{key}.json").read_text()), "cached": True}

    attempts: list[Attempt] = []
    thr = _threshold(lang)
    # Match the case the sentence uses ("Pháp lý" for the lexicon term "pháp lý") so QA forgives the respelling.
    soft = set(N.terms_in(sentence, lexicon)) | set(lint_readings) | judged | \
        {t for d in lesson_readings.values() for t in d}
    lenient = asr.lenient_terms(sentence, {m.group(0) for k in soft for m in N.term_pattern(k).finditer(sentence)})

    def make(e: str, sd: int, temp: float, respell: dict[str, str] | None = None, *, phrases: list[str] | None = None,
             pauses: list[float] | None = None, prompt: VoiceRef | None = None, strategy: str = "whole") -> Attempt:
        respell = dict(respell or {})
        lex = lex_for(e)
        att = Attempt(e, sd, temp, _tts_text(sentence, lang, e, lex, respell), respell, prompt=prompt, strategy=strategy)
        if phrases and len(phrases) > 1:
            att.phrases, att.pauses = list(phrases), list(pauses or [0.16] * (len(phrases) - 1))
            att.tts_phrases = [_tts_text(ph, lang, e, lex, respell) for ph in phrases]
        return att

    def derive(b: Attempt, e: str, sd: int, temp: float, respell: dict[str, str]) -> Attempt:
        return make(e, sd, temp, respell, phrases=b.phrases, pauses=b.pauses, prompt=b.prompt if e == b.engine else None,
                    strategy=b.strategy)

    def rank(a: Attempt) -> float:
        # Prefer the canonical timbre: a prompted take must be clearly better to win.
        return a.score + (0.02 if a.prompt is not None else 0.0)

    def best() -> Attempt:
        return min(attempts, key=rank)

    def run(att: Attempt) -> Attempt:
        nonlocal ref
        try:
            return _run_attempt(att, sentence, lang, ref, lenient)
        except Exception:
            # The reference clip was deleted while this job ran: re-resolve (falls back to a clip that exists) once.
            if not ref or not ref.audio_path or Path(ref.audio_path).exists():
                raise
            stale, ref = ref.audio_path, profiles.voice_ref(voice_id, lang)
            log.warning("reference %s vanished mid-job; retrying with %s", stale, ref.audio_path if ref else None)
            return _run_attempt(att, sentence, lang, ref, lenient)

    p_phrases, p_pauses = punct_phrases(sentence)
    if len(p_phrases) < 2 and L.is_long(sentence, lang):  # long and unpunctuated: split before conjunctions
        p_phrases = L.conj_phrases(sentence, lang)
        p_pauses = [0.14] * (len(p_phrases) - 1)
    # 1) first pass
    first: list[Attempt] = []
    learned = None
    if guide:
        g_phrases, g_pauses = guide.get("phrases") or [], guide.get("pauses") or []
        if len(g_phrases) < 2:
            g_phrases, g_pauses = p_phrases, p_pauses
        said = guide["text"] if (guide.get("asr_score") or 0) <= 0.15 else (guide.get("asr_text") or guide["text"])
        g_ref = VoiceRef(audio_path=str(settings.data_dir / guide["file"]), text=said, lang=lang)
        e0 = plan_engines[0]
        cands = {
            "guide-phrases": make(e0, seed, 0.8, phrases=g_phrases, pauses=g_pauses, strategy="guide-phrases")
            if len(g_phrases) > 1 else None,
            "guide-prompt": make("voxcpm", seed, 0.8, prompt=g_ref, strategy="guide-prompt") if "voxcpm" in capable else None,
            "whole": make(e0, seed, 0.8),
        }
        learned = guide.get("strategy") if guide.get("passed") else None
        order = ([learned] if learned in cands else []) + [k for k in cands if k != learned]
        for k in order:
            if cands[k] is None:
                continue
            first.append(cands[k])
    else:
        first = [make(e, seed, 0.8) for e in plan_engines]
        if phrase_first and len(p_phrases) > 1:
            first.append(make(plan_engines[0], seed + 29, 0.7, phrases=p_phrases, pauses=p_pauses, strategy="phrases"))
    for att in first:
        if route and attempts and attempts[0].score == 0:
            break  # the routed engine read the sentence perfectly: nothing left to compare
        attempts.append(run(att))
        if guide and attempts[-1].score <= thr and (attempts[-1].prompt is None or attempts[-1].strategy == learned):
            break  # a guided candidate with the canonical timbre (or the strategy that worked before) passes
    budget = max_attempts + len(attempts) - 1
    # 2) repair loop: the next strategy depends on how the best attempt failed (SDD-review-upgrade §5.2.3)
    from collections import Counter

    used: Counter[str] = Counter()
    tried_phrases = any(a.phrases for a in attempts)
    # First-pass attempts count towards the stall rule: when "best" already tried both engines (and phrases) without
    # improving, the loop gets one class-directed repair instead of two more blind tries (SDD-review-upgrade §6).
    step, stall, best_score = 0, 0, attempts[0].score
    for a in attempts[1:]:
        if a.score < best_score - 0.005:
            best_score, stall = a.score, 0
        else:
            stall += 1
    if len(attempts) > 1 and best().score > thr:
        # Several engines/strategies already failed: further repairs continue only while they improve.
        stall = max(stall, STALL_LIMIT - 1)
    best_score = best().score
    classes: list[str] = []
    while best().score > thr and len(attempts) < budget and stall < STALL_LIMIT:
        b = best()
        cls = classify(b, sentence, lang)
        classes.append(cls)
        ok = {"reseed": True, "engine": bool(alternates), "phrases": not tried_phrases and len(p_phrases) > 1,
              "respell": True}
        strat = next((k for k in REPAIR_ORDER[cls] if ok[k] and used[k] < REPAIR_LIMIT[k]), "reseed")
        fix = lessons.route(base_id, lang, cls)
        if fix and fix[0] in alternates and fix[0] != b.engine and used["engine"] < REPAIR_LIMIT["engine"]:
            strat = "engine"  # this engine has repaired this class before
        used[strat] += 1
        if strat == "reseed":
            att = derive(b, b.engine, seed + 17 + 101 * step, 0.55, b.respell)
        elif strat == "engine":
            att = derive(b, fix[0] if fix and fix[0] in alternates else alternates[0], seed, 0.7, b.respell)
        elif strat == "phrases":  # long sentences truncate/babble: synthesise phrase by phrase
            att = make(b.engine, seed + 29, 0.7, b.respell, phrases=p_phrases, pauses=p_pauses, strategy="phrases")
            tried_phrases = True
        else:                     # ask the LLM for phonetic respellings of the misheard words
            respell = {**b.respell, **_respell_with_llm(sentence, lang, b.qa, b.heard)}
            if respell == b.respell:
                att = derive(b, b.engine, seed + 101 * (step + 1), 0.6, b.respell)
            else:
                att = derive(b, b.engine, seed + 7, 0.6, respell)
        attempts.append(run(att))
        step += 1
        if best().score < best_score - 0.005:
            best_score, stall = best().score, 0
        else:
            stall += 1

    chosen = best()
    passed = chosen.score <= thr
    # 3) learn — only what provably helped (SDD-review-upgrade §5.1.4). A respelling becomes a lesson when it passes
    # the gate AND the same attempt without it fails (otherwise the new seed, not the respelling, fixed it).
    counterfactual = None
    if learn and passed and chosen.respell:
        known = lex_for(chosen.engine)
        new = {t: v for t, v in chosen.respell.items()
               if t not in known and lessons.reading_gate(t, v, lang, base_id)[0]}
        if new:
            counterfactual = run(derive(chosen, chosen.engine, chosen.seed, chosen.temperature,
                                        {k: v for k, v in chosen.respell.items() if k not in new}))
            counterfactual.strategy = "counterfactual"
            if counterfactual.score > thr:
                for term, say in new.items():
                    lessons.propose_reading(base_id, lang, chosen.engine, term, say, sentence)
            else:
                lessons.signal("reading_not_causal", {"voice": base_id, "lang": lang, "respell": new, "sentence": sentence})
    applied = [lid for t, (_, lid) in lesson_readings.get(attempts[0].engine, {}).items()]
    if route:
        applied.append(route[1])
    if learn:  # evaluations (learn=False) must not feed the lessons they measure
        lessons.record_use(applied, attempts[0].score <= thr)
        # A plain take of another engine passed where the first engine failed: evidence for an engine_route.
        if passed and attempts[0].score > thr and chosen.engine != attempts[0].engine and chosen.strategy == "whole" \
                and not chosen.respell:
            lessons.propose_route(base_id, lang, classify(attempts[0], sentence, lang), chosen.engine,
                                  attempts[0].engine, sentence)
    if not passed and learn:
        lessons.signal("qa_fail", {"voice": base_id, "lang": lang, "engine": chosen.engine, "text": sentence,
                                   "heard": chosen.heard, "score": chosen.score,
                                   "class": classify(chosen, sentence, lang), "attempts": len(attempts)})
    report = {
        "text": sentence, "lang": lang, "engine": chosen.engine, "score": chosen.score, "passed": chosen.score <= thr,
        "heard": chosen.heard, "respell": chosen.respell, "source": "tts", "strategy": chosen.strategy,
        "ref": (ref.audio_path or ref.key) if ref else None, "guide": guide["id"] if guide else None,
        "phrases": chosen.phrases, "pauses": chosen.pauses if chosen.phrases else None,
        "missing": chosen.qa.get("missing", []), "extra": chosen.qa.get("extra", []), "attempts": [
            {"engine": a.engine, "seed": a.seed, "temperature": a.temperature, "score": a.score, "heard": a.heard,
             "respell": a.respell, "seconds": a.seconds, "strategy": a.strategy,
             "class": classify(a, sentence, lang) if a.score > thr else None}
            for a in attempts + ([counterfactual] if counterfactual else [])
        ],
        "failure_class": None if passed else classify(chosen, sentence, lang),
        "lint": [k for k in lint_readings], "lessons": applied, "judge_accepted": sorted(judged),
    }
    A.save(cache_dir / f"{key}.wav", chosen.audio)  # type: ignore[arg-type]
    (cache_dir / f"{key}.json").write_text(json.dumps(report, ensure_ascii=False))
    return chosen.audio, report  # type: ignore[return-value]


def _key(sentence: str) -> str:
    """Stable identity for a sentence across re-renders (whitespace/case-insensitive)."""
    return profiles.sentence_key(sentence)


def speak(
    text: str, *, voice: str | None = None, lang: str = "auto", engine: str | None = None,
    fx: dict | None = None, out_dir: str | Path | None = None, name: str = "voice", seed: int = 1234,
    progress: Progress | None = None, timings: bool = True,
    overrides: dict[str, str] | None = None, regen: dict[str, int] | None = None,
    takes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Text → finished voice track (wav + mp3) with QA report and word timings.

    overrides: {sentence text: recording path} — legacy: the owner's own recording replaces that sentence.
    regen:     {sentence text: n} — re-synthesise that sentence with a fresh seed (n-th retry).
    takes:     {sentence text: raw take wav} — an AI re-render chosen in the dashboard (pre-FX, 48 kHz).
    """
    takes = {_key(k): v for k, v in (takes or {}).items()}
    overrides = {_key(k): v for k, v in (overrides or {}).items()}
    regen = {_key(k): int(v) for k, v in (regen or {}).items()}
    progress = progress or (lambda p, m: None)
    out_dir = Path(out_dir or settings.sub("outputs", "tts", time.strftime("%Y%m%d-%H%M%S")))
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = voice or profiles.default_voice()
    vprof = profiles.get_voice(voice) if voice else None
    fxp = FX.VoiceFX.from_dict({**((vprof or {}).get("fx") or {}), **(fx or {})})
    text, lint_findings = L.fix_text(text, lang)  # join cut sentences, strip foreign script (idempotent)
    sentences = N.split_sentences(text)
    if not sentences:
        raise ValueError("empty text")
    chunks, reports, gaps = [], [], []
    t0 = time.time()
    for i, s in enumerate(sentences):
        s_lang = N.detect_lang(s) if lang == "auto" else lang
        progress(i / len(sentences) * 0.85, f"sentence {i + 1}/{len(sentences)}")
        if _key(s) in takes and Path(takes[_key(s)]).exists():
            audio = A.load(takes[_key(s)])
            rep = {"text": s, "lang": s_lang, "engine": "take", "score": 0.0, "passed": True, "source": "take",
                   "heard": "", "respell": {}, "missing": [], "extra": [], "attempts": []}
        elif _key(s) in overrides and Path(overrides[_key(s)]).exists():
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
        "lint": lint_findings,
        "elapsed": round(time.time() - t0, 1),
    }
    (out_dir / f"{name}.report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return result
