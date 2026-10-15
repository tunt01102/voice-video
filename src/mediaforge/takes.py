"""Guided sentence repair: AI re-renders ("takes") of one sentence of a finished job, and splicing them back.

The owner's recording of a failed sentence is a *guide* (phrasing, prosody prompt) stored under the voice;
it is never mixed into any output. Takes are rendered with the job's own reference clip and voice FX so they
match the neighbouring sentences. See docs/SDD-guided-repair.md.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from mediaforge import asr, db
from mediaforge import audio as A
from mediaforge.config import settings
from mediaforge.voice import fx as FX
from mediaforge.voice import profiles
from mediaforge.voice import speak as S

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]

MARGIN = 0.04       # keep this much of the neighbours' edges untouched (s)
MIN_LEAD = 0.05     # silence kept before a spliced take (s)
MIN_TRAIL = 0.08    # silence kept after a spliced take (s)
MAX_STRETCH = 1.15  # beyond this a take sounds rushed: recompose instead


class Overflow(Exception):
    """A take does not fit its slot even after borrowing silence and stretching."""

    def __init__(self, index: int, need: float, have: float) -> None:
        super().__init__(f"sentence {index}: take needs {need:.2f}s, slot has {have:.2f}s")
        self.index, self.need, self.have = index, need, have


# ---------------------------------------------------------------- job helpers

def load_done_job(jid: str) -> dict[str, Any]:
    job = db.get_job(jid)
    if not job or job["status"] != "done" or not job.get("result"):
        raise ValueError(f"job {jid} is not a finished job")
    return job


def is_video(result: dict[str, Any]) -> bool:
    return bool(result.get("video"))


def source_dir(result: dict[str, Any]) -> Path:
    return Path(result["video"] if is_video(result) else result["audio"]).parent


def voice_track(result: dict[str, Any]) -> Path:
    """The voice-only track the sentences live in (pre-music for videos)."""
    return Path(result["voice"] if is_video(result) else result["audio"])


def save_result(jid: str, result: dict[str, Any]) -> None:
    db.update_job(jid, result=result)
    rj = source_dir(result) / "result.json"
    if rj.exists():
        rj.write_text(json.dumps(result, ensure_ascii=False, indent=2))


def job_voice(job: dict[str, Any]) -> str | None:
    return (job["result"] or {}).get("voice_id") or job["params"].get("voice") or profiles.default_voice()


def job_fx(job: dict[str, Any]) -> FX.VoiceFX:
    r = job["result"] or {}
    if r.get("fx"):
        return FX.VoiceFX.from_dict(r["fx"])
    vid = job_voice(job)
    try:
        base = (profiles.get_voice(vid) if vid else {}).get("fx") or {}
    except KeyError:
        base = {}
    return FX.VoiceFX.from_dict({**base, **(job["params"].get("fx") or {})})


def ensure_spans(job: dict[str, Any]) -> list[list[float]]:
    """[start, end] of every sentence in the voice track; computed once (ASR) for jobs made before spans existed."""
    from mediaforge.pipelines import sentence_spans

    r = job["result"]
    texts = [s["text"] for s in r["sentences"]]
    spans = r.get("sentence_spans") or sentence_spans(texts, r.get("words") or [])
    if not spans:
        track = voice_track(r)
        lang = r.get("lang") or (r["sentences"][0].get("lang") if r["sentences"] else None)
        tr = asr.transcribe(track, lang=lang if lang in ("vi", "en") else None)
        words = [w.as_dict() for w in asr.align_script(" ".join(texts), tr["words"], A.probe_duration(track))]
        spans = sentence_spans(texts, words)
        if not spans:
            raise RuntimeError("could not locate sentences in the voice track")
    if r.get("sentence_spans") != spans:
        r["sentence_spans"] = spans
        save_result(job["id"], r)
    return spans


# ---------------------------------------------------------------- slots and splicing

def regions(spans: list[list[float]], total: float) -> list[tuple[float, float]]:
    """Slot of each sentence: from just after the previous sentence to just before the next one."""
    out = []
    for i, (s, e) in enumerate(spans):
        a = 0.0 if i == 0 else min(spans[i - 1][1] + MARGIN, s)
        b = total if i == len(spans) - 1 else max(spans[i + 1][0] - MARGIN, e)
        out.append((a, b))
    return out


def active_rms(x: np.ndarray, sr: int = A.SR) -> float:
    """RMS of the voiced frames only (silences would bias a level match)."""
    frame = int(0.02 * sr)
    n = len(x) // frame
    if n == 0:
        return float(np.sqrt(np.mean(x ** 2) + 1e-12)) if x.size else 0.0
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    db_ = 20 * np.log10(rms)
    voiced = rms[db_ > db_.max() - 30]
    return float(np.sqrt(np.mean(voiced ** 2))) if voiced.size else 0.0


def level_match(x: np.ndarray, target: np.ndarray) -> np.ndarray:
    a, t = active_rms(x), active_rms(target)
    if a <= 1e-6 or t <= 1e-6:
        return x
    y = x * (t / a)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    return (y * (0.97 / peak) if peak > 0.97 else y).astype(np.float32)


def stretch(x: np.ndarray, factor: float, sr: int = A.SR) -> np.ndarray:
    """Speed up by `factor` (>1 = shorter), pitch preserved."""
    from pedalboard import time_stretch

    return time_stretch(x[np.newaxis, :].astype(np.float32), sr, stretch_factor=factor, high_quality=True,
                        preserve_formants=True)[0].astype(np.float32)


def fit(take: np.ndarray, slot: float, lead: float, sr: int = A.SR, *, allow_stretch: bool = True) -> tuple[np.ndarray, str] | None:
    """Lay `take` into a slot of `slot` seconds, keeping the original lead silence when possible.

    Returns (audio exactly slot long, mode) with mode ∈ pad | borrow | stretch, or None if it cannot fit.
    """
    n_slot = int(round(slot * sr))
    dur = len(take) / sr
    lead = min(max(lead, MIN_LEAD), slot)
    if dur <= slot - lead - MIN_TRAIL:
        mode = "pad"
    elif dur <= slot - MIN_LEAD - MIN_TRAIL:
        lead, mode = slot - MIN_TRAIL - dur, "borrow"
    else:
        room = slot - MIN_LEAD - MIN_TRAIL
        if not allow_stretch or room <= 0 or dur / room > MAX_STRETCH:
            return None
        take = stretch(take, dur / room, sr)[: int(room * sr)]
        lead, mode = MIN_LEAD, "stretch"
    n_lead = int(round(lead * sr))
    out = np.zeros(n_slot, dtype=np.float32)
    seg = take[: max(0, n_slot - n_lead)]
    out[n_lead: n_lead + len(seg)] = seg
    return out, mode


def splice(track: np.ndarray, spans: list[list[float]], replacements: dict[int, np.ndarray], *,
           grow: bool = False, sr: int = A.SR) -> tuple[np.ndarray, dict[int, str]]:
    """Replace sentence slots with takes. Length is preserved unless `grow` (audio-only jobs) lets a slot widen.

    Raises Overflow when a take cannot fit and growing is not allowed.
    """
    total = len(track) / sr
    regs = regions(spans, total)
    modes: dict[int, str] = {}
    pieces: list[np.ndarray] = []
    cursor = 0
    for i in sorted(replacements):
        a, b = regs[i]
        na, nb = int(round(a * sr)), int(round(b * sr))
        lead = spans[i][0] - a
        placed = fit(replacements[i], b - a, lead, sr, allow_stretch=not grow)  # audio-only: grow, never rush
        if placed is None:
            if not grow:
                raise Overflow(i, len(replacements[i]) / sr, b - a)
            body = replacements[i]
            n_lead = int(round(max(lead, MIN_LEAD) * sr))
            placed = (np.concatenate([np.zeros(n_lead, np.float32), body, np.zeros(int(MIN_TRAIL * sr), np.float32)]),
                      "grow")
        audio, modes[i] = placed
        # short crossfade-free joins: slot edges sit in silence by construction
        pieces += [track[cursor:na], audio]
        cursor = nb
    pieces.append(track[cursor:])
    return np.concatenate(pieces).astype(np.float32), modes


# ---------------------------------------------------------------- rerender job

def _deess(x: np.ndarray, fxp: FX.VoiceFX) -> np.ndarray:
    """The same de-esser finalise() applies to whole tracks (loudness is matched separately)."""
    if fxp.clarity <= 0.2 or not x.size:
        return x
    with tempfile.TemporaryDirectory() as td:
        src = A.save(Path(td) / "in.wav", x)
        dst = Path(td) / "out.wav"
        A.run_ffmpeg(["-i", str(src), "-af", f"deesser=i={0.3 + 0.4 * fxp.clarity:.2f}", "-ar", str(A.SR), "-ac", "1",
                      "-c:a", "pcm_s16le", str(dst)])
        return A.load(dst)


def rerender(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """p: {job, index, file?, lang?, source?, readings?}. With `file` the owner's recording guides the re-render; without it this is a
    fresh-seed / phrase-wise retry. Appends a take to the source job's sentence; never re-renders the video."""
    src_id, idx = p["job"], int(p["index"])
    job = load_done_job(src_id)
    r = job["result"]
    if not 0 <= idx < len(r.get("sentences") or []):
        raise ValueError(f"sentence index {idx} out of range")
    sent = r["sentences"][idx]
    text, lang = sent["text"], p.get("lang") or sent.get("lang") or "vi"
    voice_id = job_voice(job)
    store_id = profiles.base_voice(voice_id)  # a blend's recordings belong to its owner's voice

    guide = None
    if p.get("file"):
        if not voice_id:
            raise ValueError("no voice profile to store the recording under")
        progress(0.05, "lưu bản ghi làm mẫu hướng dẫn")
        # The backup is taken once the clip is also enrolled as a sample (below), not twice per recording.
        guide = profiles.add_correction(store_id, p["file"], text=text, lang=lang, job=src_id, index=idx, backup=False)

    progress(0.15, "AI đang đọc lại câu")
    n_prev = len(sent.get("takes") or [])
    seed = int(job["params"].get("seed", 1234)) + 104_729 * (n_prev + 1) + idx
    audio, rep = S.synth_sentence(text, lang, voice_id, engine=job["params"].get("engine"), seed=seed,
                                  ref_path=sent.get("ref"), guide=guide, phrase_first=True)
    if guide:
        profiles.update_correction(store_id, text, guide["id"], strategy=rep["strategy"], score=rep["score"],
                                   passed=rep["passed"])
        try:  # every recording also grows the voice's sample pool (ranked below dedicated enrolment reads)
            profiles.add_sample(store_id, settings.data_dir / guide["raw"], lang=lang, text=text,
                                source="correction")
        except Exception as e:  # noqa: BLE001
            log.warning("could not enrol correction %s as a sample: %s", guide["id"], e)
            profiles.backup_voices(f"correction-{store_id}")

    progress(0.8, "khớp giọng với các câu xung quanh")
    spans = ensure_spans(job)
    track = A.load(voice_track(r))
    a, b = regions(spans, len(track) / A.SR)[idx]
    original = track[int(a * A.SR): int(b * A.SR)]
    fxp = job_fx(job)
    shaped = level_match(_deess(FX.shape(audio, fxp), fxp), original)

    out = source_dir(r) / "takes" / f"{idx:03d}"
    out.mkdir(parents=True, exist_ok=True)
    if not (out / "original.wav").exists():
        A.save(out / "original.wav", original)
    tid = f"t{n_prev + 1}-{uuid.uuid4().hex[:4]}"
    raw_path = A.save(out / f"{tid}.wav", audio)
    fx_path = A.save(out / f"{tid}.fx.wav", shaped)
    take = {
        "id": tid, "source": p.get("source") or ("guided" if guide else "regen"), "engine": rep["engine"], "strategy": rep.get("strategy"),
        "score": rep["score"], "passed": rep["passed"], "heard": rep["heard"], "missing": rep.get("missing", []),
        "extra": rep.get("extra", []), "file": str(raw_path), "audio": str(fx_path),
        "duration": round(len(shaped) / A.SR, 2), "slot": round(b - a, 2), "created_at": time.time(),
        "guide": guide["id"] if guide else None, "recording": str(settings.data_dir / guide["raw"]) if guide else None,
        "rerender_job": job_id, **({"readings": p["readings"]} if p.get("readings") else {}),
    }
    # Re-read: another rerender of this job may have finished while we synthesised.
    fresh = load_done_job(src_id)["result"]
    s2 = fresh["sentences"][idx]
    s2["takes"] = [*(s2.get("takes") or []), take]
    s2["original_audio"] = str(out / "original.wav")
    fresh["sentence_spans"] = spans
    save_result(src_id, fresh)
    progress(1.0, "done")
    return {"job": src_id, "index": idx, "text": text, "take": take, "guide": guide}
