"""SQLite persistence for jobs. Voice profiles live on disk (see voice/profiles.py)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from mediaforge.config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued | running | waiting | done | failed | cancelled
    params TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    result TEXT,
    error TEXT,
    source TEXT NOT NULL DEFAULT 'api',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at DESC);
-- Lessons learned from mistakes and owner judgements (docs/SDD-review-upgrade.md §5.1). Text only, never audio.
CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,             -- reading | judge_accept | lint | engine_route | visual_pref | motion_pref | music_pref | plan_rule | ops
    scope TEXT NOT NULL,            -- json {voice?, lang?, engine?, style?, kind?}
    key TEXT NOT NULL,
    value TEXT NOT NULL,            -- json
    status TEXT NOT NULL,           -- candidate | active | retired
    evidence INTEGER NOT NULL DEFAULT 0,
    contra INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    origin TEXT, note TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used_at REAL
);
CREATE INDEX IF NOT EXISTS lessons_kind ON lessons(kind, status);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ratings (
    job_id TEXT PRIMARY KEY, stars INTEGER NOT NULL, tags TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(settings.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


_ready = False


def ensure() -> None:
    """Create tables if missing (cheap; for code paths that run without the job runner, e.g. the CLI or tests)."""
    global _ready
    if not _ready:
        with connect() as c:
            c.executescript(SCHEMA)
        _ready = True


def init(resumable: set[str] | frozenset[str] = frozenset(), max_resumes: int = 2) -> None:
    """Create tables; jobs that were running when the process died are re-queued when they can run again
    (resumable types, at most max_resumes times), otherwise failed."""
    global _ready
    with connect() as c:
        c.executescript(SCHEMA)
        _ready = True
        now = time.time()
        for r in c.execute("SELECT id, type, params FROM jobs WHERE status='running'").fetchall():
            jid, jtype, params = r[0], r[1], json.loads(r[2])
            n = int(params.get("_resumes", 0))
            again = jtype in resumable and n < max_resumes
            c.execute("INSERT INTO signals(job_id,kind,payload,created_at) VALUES(?,?,?,?)",
                      (jid, "incident", json.dumps({"type": jtype, "error": "interrupted by restart", "resumed": again}), now))
            if again:
                params["_resumes"] = n + 1
                c.execute("UPDATE jobs SET status='queued', message='tiếp tục sau khi khởi động lại', params=? WHERE id=?",
                          (json.dumps(params, ensure_ascii=False), jid))
            else:
                c.execute("UPDATE jobs SET status='failed', error='interrupted by restart', finished_at=? WHERE id=?",
                          (now, jid))


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    d["params"] = json.loads(d["params"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d


def create_job(type_: str, params: dict[str, Any], source: str = "api") -> dict[str, Any]:
    jid = uuid.uuid4().hex[:12]
    with connect() as c:
        c.execute(
            "INSERT INTO jobs(id,type,status,params,source,created_at) VALUES(?,?,?,?,?,?)",
            (jid, type_, "queued", json.dumps(params, ensure_ascii=False), source, time.time()),
        )
    return get_job(jid)  # type: ignore[return-value]


def get_job(jid: str) -> dict[str, Any] | None:
    with connect() as c:
        return _row(c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())


def list_jobs(limit: int = 50, status: str | None = None, type_: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM jobs WHERE 1=1", []
    if status:
        q += " AND status=?"
        args.append(status)
    if type_:
        q += " AND type=?"
        args.append(type_)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]  # type: ignore[misc]


def page_jobs(*, offset: int = 0, limit: int = 20, status: str | None = None, since: float | None = None,
              until: float | None = None, hide_types: tuple[str, ...] = (), q: str = "") -> tuple[list[dict[str, Any]], int]:
    """One page of the dashboard's job list, newest first, and how many jobs match: status, created_at in [since,
    until), types left out, preview jobs left out, q (case-insensitive) in the title or the text the owner wrote.
    Search and the preview check run in Python on the rows SQL kept (a few thousand at most)."""
    sql, args = "SELECT * FROM jobs WHERE 1=1", []
    if status:
        sql += " AND status=?"
        args.append(status)
    if since is not None:
        sql += " AND created_at>=?"
        args.append(since)
    if until is not None:
        sql += " AND created_at<?"
        args.append(until)
    if hide_types:
        sql += f" AND type NOT IN ({','.join('?' * len(hide_types))})"
        args += list(hide_types)
    q = q.strip().casefold()
    with connect() as c:
        rows = [_row(r) for r in c.execute(sql + " ORDER BY created_at DESC", args).fetchall()]
    jobs = [j for j in rows if j and not j["params"].get("preview") and (not q or q in _haystack(j))]
    return jobs[offset:offset + limit], len(jobs)


def _haystack(j: dict[str, Any]) -> str:
    p = j["params"]
    return " ".join(str(x or "") for x in ((j["result"] or {}).get("title"), p.get("brief"), p.get("script"),
                                          p.get("text"), p.get("prompt"), p.get("slides_markdown"))).casefold()


def queued_ids() -> list[str]:
    with connect() as c:
        return [r[0] for r in c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at").fetchall()]


def update_job(jid: str, **fields: Any) -> None:
    if not fields:
        return
    for k in ("result", "params"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))


def delete_job(jid: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (jid,))


def stats() -> dict[str, int]:
    with connect() as c:
        return {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()}
