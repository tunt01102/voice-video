"""Jobs tab pages and filters (db.page_jobs, GET /api/jobs/page)."""

from mediaforge import db


def test_page_jobs_filters_and_counts():
    db.init()
    made = []
    for i, (t, params) in enumerate([("tts", {"text": "Xin chào Đà Nẵng"}), ("motion", {"prompt": "Cold brew"}),
                                     ("export", {}), ("tts", {"text": "preview", "preview": True}),
                                     ("tts", {"text": "ĐÀ NẴNG mùa hè"})]):
        j = db.create_job(t, params)
        db.update_job(j["id"], created_at=1_000_000 + i * 86400, status="done" if i % 2 else "failed")
        made.append(j["id"])
    ids = lambda jobs: [j["id"] for j in jobs]  # noqa: E731
    base = dict(since=1_000_000, until=1_000_000 + 5 * 86400, hide_types=("export",))
    jobs, total = db.page_jobs(**base)
    assert total == 3 and ids(jobs) == [made[4], made[1], made[0]]  # newest first, no export, no preview
    jobs, total = db.page_jobs(offset=1, limit=1, **base)
    assert total == 3 and ids(jobs) == [made[1]]
    assert db.page_jobs(q="đà nẵng", **base)[1] == 2  # case-insensitive, Vietnamese capitals too
    assert db.page_jobs(status="done", **base)[1] == 1
    assert db.page_jobs(since=1_000_000 + 86400, until=1_000_000 + 2 * 86400)[1] == 1  # [since, until)
