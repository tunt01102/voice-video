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

from mediaforge import __version__, db, models
from mediaforge.config import settings
from mediaforge.jobs import JOB_TYPES, runner
from mediaforge.text import normalize as N
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
                if not job or job["status"] in ("done", "failed", "cancelled"):
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
            for kind in ("tts", "images", "videos", "subtitles"):
                src = settings.data_dir / "outputs" / kind / jid
                if src.exists():
                    shutil.move(str(src), settings.sub("trash") / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{jid}")
        db.delete_job(jid)
        return {"deleted": jid}

    # ---------------------------------------------------------------- voices
    @app.get("/api/voices")
    def list_voices():
        default = profiles.default_voice()
        return [{**v, "default": v["id"] == default} for v in profiles.list_voices()]

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
        return profiles.update_voice(vid, body)

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
        job = runner.submit("enrol", {"voice": vid, "file": str(up), "lang": lang or None, "text": text or None,
                                      "denoise": denoise}, source="dashboard")
        if wait:
            job = await anyio.to_thread.run_sync(runner.wait, job["id"], 300)
        return _public_job(job)

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

    # ---------------------------------------------------------------- uploads & files
    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...)):
        name = Path(file.filename or "upload.bin").name
        dest = settings.sub("uploads") / f"{int(time.time())}-{uuid.uuid4().hex[:6]}-{name}"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return {"path": str(dest), "url": "/files/" + str(dest.relative_to(settings.data_dir))}

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
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    @app.exception_handler(KeyError)
    async def key_error(_, exc: KeyError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    return app
