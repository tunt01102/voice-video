"""Job queue with a single GPU worker thread (models are not shared across threads)."""

from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from typing import Any

from mediaforge import db

log = logging.getLogger(__name__)

JOB_TYPES = {"tts", "image", "subtitle", "video", "short", "explainer", "narrated", "screen", "enrol", "rerender", "export",
             "prepare", "resubtitle"}
# Small interactive jobs jump ahead of long renders.
PRIORITY = {"enrol": 0, "tts": 1, "image": 1, "rerender": 1, "prepare": 1, "subtitle": 2, "export": 2, "resubtitle": 2}


class Cancelled(Exception):
    pass


class JobRunner:
    def __init__(self) -> None:
        self.q: queue.PriorityQueue[tuple[int, float, str]] = queue.PriorityQueue()
        self.thread: threading.Thread | None = None
        self.current: str | None = None
        self._cancel: set[str] = set()
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        db.init()
        for jid in db.queued_ids():
            job = db.get_job(jid)
            self._enqueue(jid, job["type"] if job else "video")
        self.thread = threading.Thread(target=self._loop, name="mediaforge-gpu-worker", daemon=True)
        self.thread.start()

    def submit(self, job_type: str, params: dict[str, Any], source: str = "api") -> dict[str, Any]:
        if job_type not in JOB_TYPES:
            raise ValueError(f"unknown job type {job_type!r}; valid: {sorted(JOB_TYPES)}")
        job = db.create_job(job_type, params, source)
        with self._lock:
            self._events[job["id"]] = threading.Event()
        self._enqueue(job["id"], job_type)
        return job

    def _enqueue(self, jid: str, job_type: str) -> None:
        self.q.put((PRIORITY.get(job_type, 3), time.time(), jid))

    def cancel(self, jid: str) -> bool:
        job = db.get_job(jid)
        if not job or job["status"] in ("done", "failed", "cancelled"):
            return False
        if job["status"] == "queued":
            db.update_job(jid, status="cancelled", finished_at=time.time(), message="cancelled")
            self._done(jid)
        else:
            self._cancel.add(jid)  # checked at the next progress callback
        return True

    def wait(self, jid: str, timeout: float) -> dict[str, Any] | None:
        with self._lock:
            ev = self._events.setdefault(jid, threading.Event())
        job = db.get_job(jid)
        if job and job["status"] in ("done", "failed", "cancelled"):
            return job
        ev.wait(timeout)
        return db.get_job(jid)

    def queue_depth(self) -> int:
        return self.q.qsize()

    # ------------------------------------------------------------ worker
    def _done(self, jid: str) -> None:
        with self._lock:
            ev = self._events.pop(jid, None)
        if ev:
            ev.set()

    def _loop(self) -> None:
        from mediaforge import pipelines

        while True:
            _, _, jid = self.q.get()
            job = db.get_job(jid)
            if not job or job["status"] != "queued":
                continue
            self.current = jid
            db.update_job(jid, status="running", started_at=time.time(), progress=0.0, message="starting")
            last = [0.0]

            def progress(p: float, msg: str, _jid: str = jid) -> None:
                if _jid in self._cancel:
                    raise Cancelled()
                now = time.time()
                if now - last[0] > 0.25 or p >= 1.0:
                    last[0] = now
                    db.update_job(_jid, progress=round(min(max(p, 0.0), 1.0), 4), message=msg)

            try:
                result = pipelines.run(job["type"], job["params"], progress, job_id=jid)
                db.update_job(jid, status="done", progress=1.0, message="done", result=result, finished_at=time.time())
            except Cancelled:
                db.update_job(jid, status="cancelled", message="cancelled", finished_at=time.time())
            except Exception as e:  # noqa: BLE001
                log.exception("job %s failed", jid)
                db.update_job(jid, status="failed", error=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-3000:]}",
                              message=str(e)[:300], finished_at=time.time())
            finally:
                self._cancel.discard(jid)
                self.current = None
                self._done(jid)


runner = JobRunner()
