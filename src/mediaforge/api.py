"""FastAPI app: REST + SSE + MCP (streamable HTTP at /mcp) + dashboard, on 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import anyio
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles


class _DashboardFiles(StaticFiles):
    """Dashboard assets must revalidate on every load (ETag makes it cheap): otherwise browsers keep running the
    old app.js after `service.sh update` and new features look missing."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

from mediaforge import __version__, db, models, music
from mediaforge.config import settings
from mediaforge.jobs import JOB_TYPES, runner
from mediaforge.text import normalize as N
from mediaforge.text import pronounce
from mediaforge.voice import profiles

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"


def _public_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    """Add browser URLs for any output files inside data/."""
    if not job:
        return job
    data_root = str(settings.data_dir.resolve())

    def walk(v: Any) -> Any:
        if isinstance(v, str) and v.startswith(data_root):
            return {"path": v, "url": "/files/" + v[len(data_root) + 1 :]}
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    out = dict(job)
    out["files"] = walk(job.get("result") or {})
    return out


def _readable_audio(path: Path) -> bool:
    """At least 1 KB and ffprobe finds an audio stream with a duration."""
    import subprocess

    if path.stat().st_size < 1024:
        return False
    proc = subprocess.run([settings.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True)
    try:
        return proc.returncode == 0 and float(proc.stdout.strip() or 0) > 0.3
    except ValueError:
        return proc.returncode == 0  # webm from MediaRecorder often has no duration header ("N/A") but is readable


def create_app() -> FastAPI:
    from mediaforge.mcp_server import mcp

    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", host=settings.host)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        runner.start()
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="MediaForge", version=__version__, lifespan=lifespan,
                  description="Local voice, subtitle, image and video generation. See docs/SDD.md.")

    # ---------------------------------------------------------------- health
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "queue": runner.queue_depth(), "running": runner.current,
                "jobs": db.stats(), "models": models.loaded(), "llm_order": settings.llm_order,
                "data_dir": str(settings.data_dir)}

    # ---------------------------------------------------------------- jobs
    @app.post("/api/jobs")
    async def create_job(body: dict[str, Any] = Body(...), wait: bool = Query(False), timeout: float = Query(600)):
        jtype = body.get("type")
        params = body.get("params") or {k: v for k, v in body.items() if k != "type"}
        if jtype not in JOB_TYPES:
            raise HTTPException(400, f"type must be one of {sorted(JOB_TYPES)}")
        job = runner.submit(jtype, params, source=body.get("source", "api"))
        if wait:
            job = await anyio.to_thread.run_sync(runner.wait, job["id"], timeout)
        return _public_job(job)

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50, status: str | None = None, type: str | None = None):
        return [_public_job(j) for j in db.list_jobs(limit, status, type)]

    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        job = db.get_job(jid)
        if not job:
            raise HTTPException(404, "job not found")
        return _public_job(job)

    @app.post("/api/jobs/{jid}/patch")
    def patch_job(jid: str, body: dict[str, Any] = Body(...)):
        """Re-render a finished job with some sentences replaced or regenerated.

        body: {"record": {"text", "file", "lang"}, "regen": [text, ...], "add_sample": true}
        The owner's recording replaces that sentence and (by default) is also saved as a voice sample.
        Unchanged sentences come from cache, and videos keep the same script and images.
        """
        job = db.get_job(jid)
        if not job or job["status"] != "done":
            raise HTTPException(409, "job must be finished")
        params = dict(job["params"])
        result = job.get("result") or {}
        overrides = dict(params.get("overrides") or {})
        regen = dict(params.get("regen") or {})
        enrol = None
        rec = body.get("record")
        if rec:
            if not Path(rec["file"]).is_file():
                raise HTTPException(400, "recording file not found")
            overrides[rec["text"]] = rec["file"]
            voice_id = result.get("voice_id") or params.get("voice") or profiles.default_voice()
            if voice_id and body.get("add_sample", True):
                enrol = runner.submit("enrol", {"voice": voice_id, "file": rec["file"], "lang": rec.get("lang"),
                                                "text": rec["text"]}, source="dashboard")
        for t in body.get("regen") or []:
            regen[t] = int(regen.get(t, 0)) + 1
            overrides.pop(t, None)
        params.update({"overrides": overrides, "regen": regen, "patched_from": jid})
        if result.get("plan"):
            params["plan_file"] = result["plan"]
        new = runner.submit(job["type"], params, source="dashboard")
        return {"job": _public_job(new), "enrol": enrol}

    @app.post("/api/jobs/{jid}/sentences/{index}/rerender")
    def rerender_sentence(jid: str, index: int, body: dict[str, Any] = Body(default={})):
        """AI re-renders one sentence of a finished job as a new take (the video is not re-rendered).

        body: {"file": <uploaded recording path>, "lang"?}. With a recording, it guides the re-render and is kept
        under the voice as training data; it is never inserted into the output. Without one: fresh seed/phrasing.
        """
        job = db.get_job(jid)
        if not job or job["status"] != "done":
            raise HTTPException(409, "job must be finished")
        sents = (job.get("result") or {}).get("sentences") or []
        if not 0 <= index < len(sents):
            raise HTTPException(404, "sentence not found")
        if body.get("file") and not Path(body["file"]).is_file():
            raise HTTPException(400, "recording file not found")
        params = {"job": jid, "index": index, "file": body.get("file"), "lang": body.get("lang")}
        return {"job": _public_job(runner.submit("rerender", params, source="dashboard"))}

    @app.post("/api/jobs/{jid}/sentences/{index}/accept")
    def accept_sentence(jid: str, index: int):
        """✓ Nghe đúng rồi: the owner heard sentence `index` and it is right — ASR misheard it. Marks it passed and
        records a judge_accept lesson so QA stops retrying those terms for this voice (SDD-review-upgrade §5.2.3)."""
        from mediaforge import lessons

        job = db.get_job(jid)
        if not job or job["status"] != "done":
            raise HTTPException(409, "job must be finished")
        sents = (job.get("result") or {}).get("sentences") or []
        if not 0 <= index < len(sents):
            raise HTTPException(404, "sentence not found")
        return {"sentence": lessons.accept_sentence(job, index), "job": _public_job(db.get_job(jid))}

    # ---------------------------------------------------------------- lessons (Kinh nghiệm)
    @app.get("/api/lessons")
    def list_lessons(status: str | None = None, kind: str | None = None, limit: int = 500):
        from mediaforge import lessons

        return lessons.list_lessons(status, kind, limit)

    @app.patch("/api/lessons/{lid}")
    def patch_lesson(lid: str, body: dict[str, Any] = Body(...)):
        """{status: candidate|active|retired, note?} — the owner approves, rejects or restores a lesson."""
        from mediaforge import lessons

        try:
            return lessons.set_status(lid, body["status"], body.get("note"))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/jobs/{jid}/rating")
    def get_rating(jid: str):
        from mediaforge import lessons

        return lessons.get_rating(jid) or {}

    @app.post("/api/jobs/{jid}/rating")
    def rate_job(jid: str, body: dict[str, Any] = Body(...)):
        """{stars 1..5, tags?: [giọng|phát âm|nhịp|hình|chuyển cảnh|phụ đề|nhạc|nội dung], note?}"""
        from mediaforge import lessons

        job = db.get_job(jid)
        if not job or job["status"] != "done":
            raise HTTPException(409, "job must be finished")
        try:
            return lessons.rate(job, body.get("stars", 0), body.get("tags") or [], body.get("note") or "")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/incidents")
    def incidents(days: int = 30):
        from mediaforge import lessons

        return lessons.signals("incident", time.time() - days * 86400, 200)

    @app.get("/api/insights")
    def insights(days: int = 30):
        from mediaforge import lessons

        return lessons.insights(days)

    @app.post("/api/lint")
    def lint_text(body: dict[str, Any] = Body(...)):
        """{text, lang?} → script lint findings and the fixed text (no GPU)."""
        from mediaforge.text import lint as L

        fixed, findings = L.fix_text(body.get("text") or "", body.get("lang") or "auto")
        return {"text": fixed, "findings": findings, "summary": L.summary(findings)}

    @app.post("/api/jobs/{jid}/approve")
    def approve_stage(jid: str, body: dict[str, Any] = Body(default={})):
        """Continue a job waiting at a review step (SDD-review-upgrade §5.3).

        body: {stage: plan|visuals, plan?: {title?, scenes?: [{narration?, visual_prompt?, from?}], art_direction?, hook?},
               pronunciations?: {term: reading}, verified_terms?: [term], lexicon_voice?, skip_rest?: bool}
        """
        from mediaforge import pipelines

        job = db.get_job(jid)
        if not job or job["status"] != "waiting":
            raise HTTPException(409, "job is not waiting for approval")
        stage = body.get("stage") or job["result"].get("stage")
        if stage != job["result"].get("stage"):
            raise HTTPException(409, f"job is waiting at {job['result'].get('stage')}, not {stage}")
        params = dict(job["params"])
        approved = list(dict.fromkeys([*(params.get("approved") or []), stage]))
        if body.get("skip_rest"):
            approved = list(dict.fromkeys([*approved, *(params.get("review") or [])]))
        params["approved"] = approved
        for k in ("pronunciations", "verified_terms", "lexicon_voice"):
            if body.get(k):
                params[k] = body[k]
        out = Path(job["result"]["out"])
        if stage == "plan" and body.get("plan"):
            try:
                plan_data = pipelines.apply_plan_edits(out, body["plan"])
            except (ValueError, KeyError, IndexError) as e:
                raise HTTPException(400, f"bad plan edit: {e}") from e
            if not plan_data["scenes"]:
                raise HTTPException(400, "the script has no scenes left")
        return _public_job(runner.requeue(jid, params, message=f"đã duyệt {stage}"))

    @app.post("/api/jobs/{jid}/restage")
    def restage_job(jid: str, body: dict[str, Any] = Body(...)):
        """Redo one stage without a full render: {stage: visuals, scene, prompt?, seed?} (new picture) or
        {stage: compose, music?, music_volume?}. Works on a finished video, or on one waiting at the storyboard."""
        job = db.get_job(jid)
        if not job or job["status"] not in ("done", "waiting"):
            raise HTTPException(409, "job must be finished or waiting at the storyboard")
        if body.get("stage", "visuals") == "visuals" and body.get("scene") is None:
            raise HTTPException(400, "scene is required")
        if body.get("music") and body["music"] != "__none":
            from mediaforge import music as M

            try:
                M.resolve(body["music"])
            except Exception as e:  # noqa: BLE001
                raise HTTPException(400, str(e)) from e
        params = {"job": jid, **{k: body[k] for k in ("stage", "scene", "prompt", "seed", "music", "music_volume") if k in body}}
        return {"job": _public_job(runner.submit("restage", params, source="dashboard"))}

    @app.post("/api/jobs/{jid}/resubtitle")
    def resubtitle(jid: str, body: dict[str, Any] = Body(default={})):
        """Rebuild a finished video's subtitles (fit-to-frame, karaoke) without re-rendering voice or images."""
        job = db.get_job(jid)
        if not job or job["status"] != "done" or not (job.get("result") or {}).get("video"):
            raise HTTPException(400, "only a finished video can be re-subtitled")
        params = {"job": jid, **({"karaoke": bool(body["karaoke"])} if "karaoke" in body else {})}
        return {"job": _public_job(runner.submit("resubtitle", params, source="dashboard"))}

    @app.post("/api/jobs/{jid}/revoice")
    def revoice_job(jid: str, body: dict[str, Any] = Body(...)):
        """Read a finished video again in another voice (clone, blend or stock): {voice, fx?, engine?}. Only the voice
        and compose stages run; script and pictures are kept, subtitles/cards/music follow the new timing."""
        from mediaforge import revoice

        voice = body.get("voice")
        if not voice:
            raise HTTPException(400, "voice is required")
        job = db.get_job(jid)
        try:
            r = revoice.check_source(job)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        try:
            revoice.check_voice(voice, current=r.get("voice_id") or job["params"].get("voice"))
            est = revoice.estimate(job, voice, body.get("engine"))
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        fx = body.get("fx") if isinstance(body.get("fx"), dict) else None
        params = {"job": jid, "voice": voice, **({"fx": fx} if fx else {}),
                  **({"engine": body["engine"]} if body.get("engine") else {})}
        return {"job": _public_job(runner.submit("revoice", params, source=body.get("source", "dashboard"))),
                "estimate": est}

    @app.get("/api/jobs/{jid}/revoice/estimate")
    def revoice_estimate(jid: str, voice: str, engine: str | None = None):
        from mediaforge import revoice

        try:
            return revoice.estimate(db.get_job(jid), voice, engine)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/jobs/{jid}/export")
    def export_job(jid: str, body: dict[str, Any] = Body(default={})):
        """Export with a chosen voice per sentence ({"selection": {index: take_id|"original"}, "format": "mp4"})."""
        from mediaforge import export

        job = db.get_job(jid)
        if not job or job["status"] != "done" or not job.get("result"):
            raise HTTPException(409, "job must be finished")
        allowed = export.formats_for(job["result"])
        fmt = (body.get("format") or allowed[0]).lower()
        if fmt not in allowed:
            raise HTTPException(400, f"format must be one of {allowed}")
        params = {"job": jid, "selection": body.get("selection") or {}, "format": fmt}
        return {"job": _public_job(runner.submit("export", params, source="dashboard"))}

    @app.get("/api/jobs/{jid}/occurrences")
    def term_occurrences(jid: str, terms: str = ""):
        """Which sentences of a finished job contain each term (comma-separated), for the Sửa phát âm panel."""
        from mediaforge import repronounce

        job = db.get_job(jid)
        if not job or not (job.get("result") or {}).get("sentences"):
            raise HTTPException(404, "job has no sentences")
        wanted = [t.strip() for t in terms.split(",") if t.strip()]
        return repronounce.occurrences(job["result"]["sentences"], wanted)

    @app.post("/api/jobs/{jid}/repronounce")
    def repronounce_job(jid: str, body: dict[str, Any] = Body(...)):
        """Change how words are read in a finished job without re-rendering it.

        body: {"readings": {"PNJ": "Pi En Di"}, "format"?: "mp4"}. Saves the readings to the lexicon, re-reads every
        sentence containing them and exports a new file with those sentences replaced.
        """
        from mediaforge import export, repronounce

        job = db.get_job(jid)
        if not job or job["status"] != "done" or not (job.get("result") or {}).get("sentences"):
            raise HTTPException(409, "job must be finished and have sentences")
        readings = repronounce.clean_readings(body.get("readings"))
        if not readings:
            raise HTTPException(400, "readings must map at least one term to a different reading")
        hits = repronounce.occurrences(job["result"]["sentences"], readings)
        if not any(hits.values()):
            raise HTTPException(400, f"not found in this job: {', '.join(readings)}")
        allowed = export.formats_for(job["result"])
        fmt = (body.get("format") or allowed[0]).lower()
        if fmt not in allowed:
            raise HTTPException(400, f"format must be one of {allowed}")
        params = {"job": jid, "readings": readings, "format": fmt}
        return {"job": _public_job(runner.submit("repronounce", params, source="dashboard")), "occurrences": hits}

    @app.get("/api/export-formats")
    def export_formats():
        from mediaforge import export

        return export.catalog()

    @app.get("/api/jobs/{jid}/events")
    async def job_events(jid: str):
        if not db.get_job(jid):
            raise HTTPException(404, "job not found")

        async def gen():
            last = None
            while True:
                job = db.get_job(jid)
                snap = (job["status"], job["progress"], job["message"]) if job else None
                if snap != last:
                    last = snap
                    yield f"event: progress\ndata: {json.dumps(_public_job(job), ensure_ascii=False)}\n\n"
                if not job or job["status"] in ("done", "failed", "cancelled", "waiting"):
                    yield f"event: end\ndata: {json.dumps({'status': job['status'] if job else 'missing'})}\n\n"
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: str):
        return {"cancelled": runner.cancel(jid)}

    @app.delete("/api/jobs/{jid}")
    def delete_job(jid: str, files: bool = False):
        job = db.get_job(jid)
        if not job:
            raise HTTPException(404, "job not found")
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "cancel the job first")
        if files:  # moved to data/trash, never destroyed outright
            for kind in ("tts", "images", "videos", "subtitles", "songs"):
                src = settings.data_dir / "outputs" / kind / jid
                if src.exists():
                    shutil.move(str(src), settings.sub("trash") / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{jid}")
        db.delete_job(jid)
        return {"deleted": jid}

    # ---------------------------------------------------------------- voices
    @app.get("/api/voices")
    def list_voices():
        from mediaforge.voice import stock

        default = profiles.default_voice()
        return [{**v, "default": v["id"] == default,
                 **({"preview_ready": bool(stock.preview_file(v["id"]))} if v.get("kind") == "stock" else {})}
                for v in profiles.list_voices()]

    @app.post("/api/voices/{vid}/default")
    def make_default(vid: str):
        profiles.set_default_voice(vid)
        return {"default": vid}

    @app.post("/api/voices")
    def create_voice(body: dict[str, Any] = Body(...)):
        try:
            if body.get("demo"):
                job = runner.submit("enrol", {"demo": True, "voice": body.get("id", "demo")}, source="dashboard")
                return {"job": job}
            return profiles.create_voice(body["name"], body.get("id"), body.get("description", ""))
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/voices/prompts")
    def enrol_prompts():
        return profiles.ENROL_PROMPTS

    @app.get("/api/voices/{vid}")
    def get_voice(vid: str):
        try:
            return profiles.get_voice(vid)
        except (KeyError, ValueError) as e:
            raise HTTPException(404, str(e)) from e

    @app.patch("/api/voices/{vid}")
    def patch_voice(vid: str, body: dict[str, Any] = Body(...)):
        try:
            return profiles.update_voice(vid, body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # ---------------------------------------------------------------- pha giọng (voice blends)
    @app.get("/api/blends")
    def list_blends():
        from mediaforge.voice import blend

        return {"blends": [v for v in profiles.list_voices() if v.get("kind") == "blend"],
                "consent_kinds": blend.CONSENT_KINDS, "recommended": blend.RECOMMENDED, "max": blend.MAX_AMOUNT}

    @app.post("/api/blends")
    def create_blend(body: dict[str, Any] = Body(...)):
        """{owner, partner, amount (0..0.5, default 0.25), name?} → calibration job; the result is a new voice id."""
        from mediaforge.voice import blend

        try:
            owner, partner = profiles.get_voice(body["owner"]), profiles.get_voice(body["partner"])
            if owner.get("kind") == "blend" or body["owner"] == body["partner"]:
                raise ValueError("giọng chính phải là một giọng thật và khác giọng pha")
            blend.check_partner(partner)
            blend._amount(body.get("amount", 0.25))
        except KeyError as e:
            raise HTTPException(404, f"voice not found: {e}") from e
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        job = runner.submit("blend", {k: body.get(k) for k in ("owner", "partner", "amount", "name", "id")
                                      if body.get(k) is not None}, source="dashboard")
        return {"job": job}

    @app.patch("/api/blends/{vid}")
    def patch_blend(vid: str, body: dict[str, Any] = Body(...)):
        """{amount} → re-map the share on the stored calibration curve (instant, no GPU)."""
        from mediaforge.voice import blend

        try:
            return blend.set_amount(vid, body["amount"])
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/voices/{vid}")
    def delete_voice(vid: str):
        profiles.delete_voice(vid)
        return {"deleted": vid}

    @app.post("/api/voices/{vid}/samples")
    async def add_sample(vid: str, file: UploadFile = File(...), lang: str | None = Form(None),
                         text: str | None = Form(None), denoise: bool = Form(True), wait: bool = Form(True)):
        profiles.get_voice(vid)
        up = settings.sub("uploads") / f"{uuid.uuid4().hex[:8]}{Path(file.filename or 'rec.webm').suffix or '.webm'}"
        with up.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        if not _readable_audio(up):
            # An empty or cut-off browser recording (seen: webm "EBML … End of file") — reject before queuing.
            from mediaforge import lessons

            lessons.signal("incident", {"type": "enrol", "error": "unreadable recording upload", "bytes": up.stat().st_size})
            raise HTTPException(422, "Bản ghi trống hoặc bị hỏng — hãy ghi lại (giữ nút ghi ít nhất 2 giây).")
        job = runner.submit("enrol", {"voice": vid, "file": str(up), "lang": lang or None, "text": text or None,
                                      "denoise": denoise}, source="dashboard")
        if wait:
            job = await anyio.to_thread.run_sync(runner.wait, job["id"], 300)
        return _public_job(job)

    @app.get("/api/voices/{vid}/corrections")
    def voice_corrections(vid: str):
        return profiles.corrections(vid)

    @app.delete("/api/voices/{vid}/samples/{sid}")
    def delete_sample(vid: str, sid: str):
        return profiles.remove_sample(vid, sid)

    @app.get("/api/voices/{vid}/samples/{sid}/audio")
    def sample_audio(vid: str, sid: str):
        v = profiles.get_voice(vid)
        s = next((s for s in v["samples"] if s["id"] == sid), None)
        if not s:
            raise HTTPException(404, "sample not found")
        return FileResponse(settings.data_dir / s["file"], media_type="audio/wav")

    @app.get("/api/voices/{vid}/preview")
    def voice_preview(vid: str):
        """The pre-rendered preview of a stock voice (giọng mẫu): plays at once, no job."""
        from mediaforge.voice import stock

        f = stock.preview_file(vid)
        if not f:
            raise HTTPException(404, "no preview yet (run the stock_previews job)")
        return FileResponse(f, media_type="audio/mpeg")

    # ---------------------------------------------------------------- lexicon
    @app.get("/api/lexicon")
    def get_lexicon(voice: str | None = None):
        return N.load_lexicon(voice)

    @app.put("/api/lexicon")
    def put_lexicon(body: dict[str, Any] = Body(...), voice: str | None = None):
        data = {lang: {k: (v if isinstance(v, dict) else {"say": v, "source": "manual", "hits": 0})
                       for k, v in (body.get(lang) or {}).items()} for lang in ("vi", "en")}
        N.save_lexicon(data, voice)
        return data

    @app.post("/api/lexicon/terms")
    def add_terms(body: dict[str, Any] = Body(...), voice: str | None = None):
        """Merge pre-scan answers: {lang, terms: {term: reading}, verified: [term]} (PUT replaces the whole file)."""
        lang = body.get("lang", "vi")
        if lang not in ("vi", "en"):
            raise HTTPException(400, "lang must be vi or en")
        return pronounce.remember(lang, body.get("terms") or {}, body.get("verified") or [], voice=voice or body.get("voice"))

    # ---------------------------------------------------------------- poems (Ngâm thơ tab)
    @app.get("/api/poems")
    def list_poems():
        from mediaforge.sing import poems

        return poems.summary()

    @app.get("/api/sing/moods")
    def sing_moods():
        from mediaforge.sing import melody

        return {"moods": list(melody.MOODS), "styles": ["ngam", "hat"]}

    # ---------------------------------------------------------------- music library
    @app.get("/api/music")
    def list_music():
        return [{**t, "url": f"/api/music/{t['id']}/audio" if t["available"] else None} for t in music.list_tracks()]

    @app.get("/api/music/{tid}/audio")
    def music_audio(tid: str):
        t = next((t for t in music.list_tracks() if t["id"] == tid and t["available"]), None)
        if not t:
            raise HTTPException(404, "track not found")
        return FileResponse(music.track_path(t))

    # ---------------------------------------------------------------- uploads & files
    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "upload.bin").name
        dest = settings.sub("uploads") / f"{int(time.time())}-{uuid.uuid4().hex[:6]}-{name}"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return {"path": str(dest), "url": "/files/" + str(dest.relative_to(settings.data_dir))}

    @app.post("/api/documents")
    async def import_document(file: UploadFile = File(...)):
        """Word/PDF → text with [ảnh N] markers + its pictures (docs/SDD-doc-import-preview-chibi.md §3.1)."""
        from mediaforge.text import documents

        name = Path(file.filename or "document").name
        if Path(name).suffix.lower() not in documents.SUPPORTED:
            raise HTTPException(400, "Chỉ nhận .docx, .pdf, .doc, .rtf, .odt")
        up = settings.sub("uploads") / f"{int(time.time())}-{uuid.uuid4().hex[:6]}-{name}"
        with up.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            doc = await anyio.to_thread.run_sync(lambda: documents.import_file(up, settings.data_dir, filename=name))
        except documents.DocumentError as e:
            raise HTTPException(422, str(e)) from e
        root = settings.data_dir.resolve()
        for im in doc["images"]:
            im["url"] = "/files/" + str(Path(im["path"]).resolve().relative_to(root))
        return {**doc, "source": str(up)}

    @app.get("/files/{path:path}")
    def files(path: str):
        root = settings.data_dir.resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(target)

    @app.post("/api/models/unload")
    def unload(prefix: str = ""):
        return {"unloaded": models.unload(prefix)}

    # ---------------------------------------------------------------- MCP + dashboard
    for route in mcp_app.routes:
        app.router.routes.append(route)
    app.mount("/", _DashboardFiles(directory=WEB_DIR, html=True), name="web")

    @app.exception_handler(KeyError)
    async def key_error(_, exc: KeyError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    return app
