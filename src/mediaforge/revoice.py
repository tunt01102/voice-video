"""Revoice: read a finished video again in another voice — clone, blend or stock — without re-planning or redrawing.

Only the voice stage runs again (every sentence through speak → synth_sentence, QA and repair included), into a
work folder next to the job. The video is then re-composed from the new voice with new subtitles: scene durations
follow the new narration, so Ken Burns moves, transitions, cards, chapter lower-thirds, karaoke and music move with
it, and fixed-length clips are cut / held / looped with a warning (pipelines.fit_clips). Only after the new render
exists are the old voice files swapped out; everything old goes to data/trash, nothing is deleted.
The source job is updated in place; the revoice job's result points to it (source_job).
See docs/SDD-review-upgrade.md §11.5.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from mediaforge import db
from mediaforge import takes as T
from mediaforge.config import settings
from mediaforge.text import normalize as N
from mediaforge.text import pronounce
from mediaforge.video import compose
from mediaforge.voice import blend, profiles

Progress = Callable[[float, str], None]

ENGINES = ("vieneu", "voxcpm", "best")
# Seconds of work per second of narration, for the dashboard's estimate (measured on the M4 Max, §11.5).
SYNTH_RATE = {"vieneu": 0.8, "voxcpm": 1.4, "best": 2.8}
COMPOSE_RATE = 0.25


def check_voice(voice_id: str, *, current: str | None = None) -> dict[str, Any]:
    """The target voice, if it may be used: a stock voice (synthetic), a blend whose partner consented, the owner's
    own voice (the default or the job's current voice) or a voice with recorded consent. Voices are biometric data
    of a real person (blend.check_partner): anything else is refused."""
    prof = profiles.get_voice(voice_id)  # KeyError → unknown voice
    kind = prof.get("kind")
    if kind == "stock":
        return prof
    if kind == "blend":
        partner = profiles.get_voice(prof["blend"]["partner"])
        if not blend.consent_of(partner):
            raise PermissionError(f"giọng pha '{prof.get('name', voice_id)}' dùng giọng '{partner.get('name')}' "
                                  "chưa có ghi nhận đồng ý (tab Giọng nói)")
        return prof
    if voice_id in (current, profiles.default_voice()) or blend.consent_of(prof):
        return prof
    raise PermissionError(
        f"Giọng '{prof.get('name', voice_id)}' chưa có ghi nhận đồng ý. Giọng nói là dữ liệu sinh trắc của một người "
        "thật: chỉ dùng giọng của chính bạn, giọng mẫu, giọng tổng hợp hoặc người đã đồng ý (đánh dấu ở tab Giọng nói).")


def check_source(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job or job["status"] != "done":
        raise ValueError("chỉ đổi giọng được cho video đã render xong")
    r = job.get("result") or {}
    # Word timings of the old voice are not needed (new ones are made), so videos from before they were stored work.
    if not T.is_video(r) or not r.get("plan") or not Path(r["plan"]).exists():
        raise ValueError(f"job {job['id']} không phải video có kịch bản (không đổi giọng được)")
    return r


def target_engine(prof: dict[str, Any], engine: str | None, lang: str) -> str:
    if prof.get("kind") == "stock":
        return prof["engine"]  # a preset/design exists in one engine only: no alternates, no "best"
    if engine and engine not in ENGINES:
        raise ValueError(f"engine must be one of {list(ENGINES)}")
    return engine or (prof.get("engines") or {}).get(lang) or "best"


def estimate(job: dict[str, Any], voice_id: str, engine: str | None = None) -> dict[str, Any]:
    """Seconds a revoice should take: voice synthesis at the target engine's rate plus a compose like the original."""
    r = check_source(job)
    prof = profiles.get_voice(voice_id)
    lang = r.get("lang") or "vi"
    eng = target_engine(prof, engine, lang)
    cards = json.loads(Path(r["plan"]).read_text("utf-8")).get("cards") or {}
    speech = max(1.0, float(r.get("duration") or 0) - float(cards.get("intro") or 0) - float(cards.get("outro") or 0))
    stages = (r.get("report") or {}).get("stages") or {}
    comp = float(stages.get("compose") or COMPOSE_RATE * float(r.get("duration") or speech))
    voice_s = SYNTH_RATE.get(eng, SYNTH_RATE["best"]) * speech
    return {"seconds": round(voice_s + comp), "voice": round(voice_s), "compose": round(comp), "engine": eng,
            "speech_seconds": round(speech, 1), "sentences": len(r.get("sentences") or [])}


def job_readings(job: dict[str, Any], plan_text: str, lang: str) -> dict[str, str]:
    """The owner's readings for this video: pre-render pronunciations, Sửa phát âm fixes, readings of takes and the
    old voice's own manual lexicon entries — only terms that occur in the script."""
    p, r = job["params"], job["result"]
    found: dict[str, str] = {}
    old_base = profiles.base_voice(T.job_voice(job))
    if old_base:
        for term, e in N.load_lexicon(old_base).get(lang, {}).items():
            if e.get("source") in ("manual", "verified"):
                found[term] = e["say"]
    found.update({str(k): str(v) for k, v in (p.get("pronunciations") or {}).items()})
    for t in p.get("verified_terms") or []:
        found.setdefault(str(t), str(t))
    for entry in r.get("repronounced") or []:
        found.update(entry.get("readings") or {})
    for s in r.get("sentences") or []:
        for t in s.get("takes") or []:
            found.update(t.get("readings") or {})
    return {t: say for t, say in found.items() if t.strip() and say.strip() and N.terms_in(plan_text, [t])}


def carry_readings(readings: dict[str, str], lang: str, voice_id: str) -> dict[str, str]:
    """Save the readings the new voice would not already apply, scoped to its base voice (origin `revoice`)."""
    base = profiles.base_voice(voice_id)
    have = N.merged_lexicon(base, lang)
    todo = {t: say for t, say in readings.items() if have.get(t) != say and not (say == t and t not in have)}
    if todo:
        pronounce.remember(lang, {t: s for t, s in todo.items() if s != t}, [t for t, s in todo.items() if s == t],
                           voice=base, origin="revoice")
    return todo


def dropped_edits(job: dict[str, Any]) -> dict[str, Any]:
    """Per-sentence edits that belong to the old voice and cannot carry over."""
    p, r = job["params"], job["result"]
    takes = [{"index": i, "text": s["text"], "takes": len(s["takes"])} for i, s in enumerate(r.get("sentences") or [])
             if s.get("takes")]
    return {"takes": takes, "recordings": sorted((p.get("overrides") or {}).keys()),
            "regen": sorted((p.get("regen") or {}).keys()), "chosen_takes": sorted((p.get("takes") or {}).keys()),
            "exports": len(r.get("exports") or []), "repronounced": len(r.get("repronounced") or [])}


def run(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """p: {job, voice, fx?, engine?}. Resumable: the new voice is kept in out/.revoice-<id>/ until it is swapped in."""
    from mediaforge import pipelines as P

    t0 = time.time()
    src_id = p["job"]
    job = db.get_job(src_id)
    r = check_source(job)
    old_voice = T.job_voice(job)
    new_voice = str(p["voice"])
    prof = check_voice(new_voice, current=old_voice)
    out = Path(r["video"]).parent
    plan_data = json.loads(Path(r["plan"]).read_text("utf-8"))
    lang = P.stage_state(out).get("plan", {}).get("lang") or r.get("lang") or "vi"
    kind = (r.get("report") or {}).get("kind") or job["params"].get("kind") or job["type"]
    aspect = (r.get("report") or {}).get("aspect") or job["params"].get("aspect") or ("9:16" if kind == "short" else "16:9")
    W, H = compose.RESOLUTIONS[aspect]
    stock = prof.get("kind") == "stock"
    engine = None if stock else p.get("engine")
    engine_used = target_engine(prof, engine, lang)
    fx = p.get("fx") or None
    rid = job_id or time.strftime("%Y%m%d-%H%M%S")
    work = out / f".revoice-{rid}"
    trash = settings.data_dir / "trash" / f"{src_id}-revoice-{rid}"
    meta_file = work / "revoice.json"
    meta = json.loads(meta_file.read_text("utf-8")) if meta_file.exists() else {}

    try:
        # ---- 1. voice (the same stage run_video uses, written to the work folder)
        if not (work / "voice.json").exists():
            work.mkdir(exist_ok=True)
            plan_text = " ".join(sc["narration"] for sc in plan_data["scenes"])
            readings = job_readings(job, plan_text, lang)
            meta["readings_kept"] = readings
            meta["readings_saved"] = carry_readings(readings, lang, new_voice) if readings else {}
            meta["dropped"] = dropped_edits(job)
            meta["before"] = {"voice": old_voice, "fx": r.get("fx"), "engine": job["params"].get("engine"),
                              "duration": r.get("duration"), "qa_failed": len(r.get("qa_failed_sentences") or [])}
            ts = time.time()
            vp = {"engine": engine, "fx": fx, "seed": job["params"].get("seed", 1234),
                  "karaoke": job["params"].get("karaoke")}
            P.voice_stage(vp, plan_data, work, lang=lang, voice=new_voice, width=W, height=H,
                          progress=P._sub(progress, 0.0, 0.6))
            meta["voice_seconds"] = round(time.time() - ts, 1)
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        vs = json.loads((work / "voice.json").read_text("utf-8"))

        # ---- 2. compose from the new voice (pictures reused, durations from the new scene audio)
        if not meta.get("composed"):
            ts = time.time()
            res = P.recompose({"job": src_id}, P._sub(progress, 0.62, 0.97), new_subtitles=True, trash=trash,
                              voice_dir=work, words=vs["words"])
            meta.update(composed=True, compose_seconds=round(time.time() - ts, 1), clip_fit=res["clip_fit"],
                        warnings=res["warnings"])
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception:
        if not meta.get("composed"):
            shutil.rmtree(work, ignore_errors=True)  # the source video was not touched
        raise

    # ---- 3. swap the voice in; the old voice, its takes and timings go to the same trash folder as the old render
    progress(0.98, "thay giọng cũ")
    trash.mkdir(parents=True, exist_ok=True)
    for name in ("voice", "voice.wav", "voice.json", "takes"):
        if (work / name).exists() or name in ("voice.json", "takes"):
            P.to_trash(out / name, trash)
    for name in ("voice", "voice.wav"):
        if (work / name).exists():
            shutil.move(str(work / name), out / name)
    subs = {ext: str(out / f"subtitles.{ext}") for ext in ("srt", "vtt", "ass")}
    (out / "voice.json").write_text(json.dumps({**vs, "subs": subs}, ensure_ascii=False))

    fresh = T.load_done_job(src_id)["result"]
    sentences = P.scene_sentences(vs["scene_reports"])
    failed = [s for rep in vs["scene_reports"] for s in rep["qa_failed_sentences"]]
    before = meta.get("before", {})
    report = fresh.setdefault("report", {})
    report.pop("resubtitle", None)
    fresh.pop("resubtitled_at", None)
    entry = {
        "at": time.time(), "revoice_job": job_id,
        "from": {k: before.get(k) for k in ("voice", "fx", "engine")},
        "to": {"voice": new_voice, "name": prof.get("name"), "kind": prof.get("kind") or "clone", "fx": fx,
               "engine": engine_used},
        "seconds": {"voice": meta.get("voice_seconds"), "compose": meta.get("compose_seconds"),
                    "total": round(time.time() - t0, 1) if "voice_seconds" in meta else None},
        "duration": {"before": before.get("duration"), "after": fresh.get("duration")},
        "qa_failed": {"before": before.get("qa_failed"), "after": len(failed)},
        "dropped": meta.get("dropped", {}), "readings_kept": meta.get("readings_kept", {}),
        "readings_saved": meta.get("readings_saved", {}), "clip_fit": meta.get("clip_fit", []),
        "warnings": meta.get("warnings", []), "trash": str(trash),
    }
    if stock and p.get("engine"):
        entry["warnings"] = [*entry["warnings"], f"giọng mẫu chỉ có trên {engine_used}: bỏ qua engine={p['engine']}"]
    fresh.update({
        "words": vs["words"], "sentences": sentences, "voice_id": new_voice, "voice": str(out / "voice.wav"),
        "fx": vs["scene_reports"][0]["fx"] if vs["scene_reports"] else None, "qa_failed_sentences": failed,
        "sentence_spans": P.sentence_spans([s["text"] for s in sentences], vs["words"]), "repronounced": [],
    })
    report["qa_failed_sentences"] = failed
    report["revoiced"] = [*(e for e in report.get("revoiced") or [] if not job_id or e.get("revoice_job") != job_id),
                          entry]
    T.save_result(src_id, fresh)
    # Later restages, takes and re-renders of this job use the new voice; the old voice's edits are gone.
    params = {k: v for k, v in job["params"].items() if k not in ("overrides", "regen", "takes", "fx", "engine")}
    params.update({"voice": new_voice, **({"fx": fx} if fx else {}), **({"engine": engine} if engine else {})})
    db.update_job(src_id, params=params)
    shutil.rmtree(work, ignore_errors=True)
    progress(1.0, "done")
    return {"job": src_id, "source_job": src_id, "video": str(out / "final.mp4"), **entry}
