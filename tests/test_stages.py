"""Staged video pipeline: checkpoints, review gates, resume, plan edits (docs/SDD-review-upgrade.md §5.3).
Every model and FFmpeg call is faked."""

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from mediaforge import asr, db, jobs, music, pipelines as P, subtitles
from mediaforge import audio as A
from mediaforge.video import compose


@pytest.fixture
def fakes(monkeypatch):
    calls = Counter()

    def plan(p, kind, *, style, seconds):
        calls["plan"] += 1
        data = {"title": "T", "scenes": [{"narration": "Câu một.", "visual_prompt": "a"},
                                         {"narration": "Câu hai.", "visual_prompt": "b"}]}
        P.lint_plan(data, "vi")
        return data, "vi"

    def speak(text, *, out_dir, name, **kw):
        calls["speak"] += 1
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        wav = A.save(Path(out_dir) / f"{name}.wav", np.zeros(A.SR // 2, dtype=np.float32))
        return {"audio": str(wav), "sentences": [{"text": text, "passed": True}], "qa_failed_sentences": [],
                "voice": "v", "fx": {}}

    def visuals(p, plan_data, out, durations, **kw):
        calls["visuals"] += 1
        res = []
        for i, _ in enumerate(plan_data["scenes"]):
            f = out / "images" / f"scene_{i + 1:02d}.png"
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.exists():
                calls["image"] += 1
                f.write_bytes(b"png")
            res.append(compose.Scene(str(f), durations[i]))
        return res

    def comp(scenes, voice, out_path, **kw):
        calls["compose"] += 1
        Path(out_path).write_bytes(b"mp4")
        return Path(out_path)

    monkeypatch.setattr(P, "make_plan", plan)
    monkeypatch.setattr(P.S, "speak", speak)
    monkeypatch.setattr(P, "build_visuals", visuals)
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: {"words": []})
    monkeypatch.setattr(subtitles, "write_all", lambda words, base, **k: {"ass": "x.ass", "srt": "x.srt", "vtt": "x.vtt"})
    monkeypatch.setattr(compose, "compose", comp)
    monkeypatch.setattr(compose, "thumbnail", lambda v, o, at=1.0: Path(o))
    monkeypatch.setattr(A, "probe_duration", lambda f: 1.0)
    monkeypatch.setattr(music, "resolve", lambda m: None)
    return calls


def prog(p, m):
    pass


def test_gates_pause_and_approval_continues(fakes):
    p = {"kind": "narrated", "review": ["plan", "visuals"], "use_llm": False}
    with pytest.raises(P.AwaitApproval) as w:
        P.run_video(p, prog, job_id="g1")
    assert w.value.stage == "plan" and w.value.result["plan_data"]["title"] == "T"
    assert "prescan" in w.value.result["plan_data"]
    with pytest.raises(P.AwaitApproval) as w:
        P.run_video({**p, "approved": ["plan"]}, prog, job_id="g1")
    assert w.value.stage == "visuals" and len(w.value.result["storyboard"]) == 2
    r = P.run_video({**p, "approved": ["plan", "visuals"]}, prog, job_id="g1")
    assert r["video"].endswith("final.mp4")
    assert fakes == Counter({"plan": 1, "speak": 2, "visuals": 1, "image": 2, "compose": 1})  # nothing redone
    assert set(r["report"]["stages"]) == {"plan", "voice", "visuals", "compose"}


def test_resume_skips_finished_stages(fakes, monkeypatch):
    real = compose.compose

    def crash(*a, **k):
        raise RuntimeError("server died")

    monkeypatch.setattr(compose, "compose", crash)
    with pytest.raises(RuntimeError):
        P.run_video({"kind": "narrated"}, prog, job_id="r1")
    monkeypatch.setattr(compose, "compose", real)
    P.run_video({"kind": "narrated"}, prog, job_id="r1")
    assert fakes["plan"] == 1 and fakes["speak"] == 2 and fakes["visuals"] == 1 and fakes["compose"] == 1


def test_plan_edit_invalidates_later_stages(fakes):
    p = {"kind": "narrated", "review": ["plan"], "use_llm": False}
    with pytest.raises(P.AwaitApproval) as w:
        P.run_video(p, prog, job_id="e1")
    out = Path(w.value.result["out"])
    P.apply_plan_edits(out, {"scenes": [{"narration": "Câu một sửa rồi.", "from": 0}, {"narration": " ", "from": 1}]})
    plan = json.loads((out / "plan.json").read_text())
    assert [s["narration"] for s in plan["scenes"]] == ["Câu một sửa rồi."]
    assert plan["scenes"][0]["visual_prompt"] == "a"  # fields not edited are kept
    r = P.run_video({**p, "approved": ["plan"]}, prog, job_id="e1")
    assert fakes["plan"] == 1 and fakes["speak"] == 1 and len(r["sentences"]) == 1


def test_interrupted_jobs_resume_or_fail(monkeypatch):
    db.ensure()
    j1 = db.create_job("narrated", {"brief": "x"})
    j2 = db.create_job("enrol", {"voice": "v"})
    j3 = db.create_job("narrated", {"brief": "y", "_resumes": 2})
    for j in (j1, j2, j3):
        db.update_job(j["id"], status="running")
    db.init(resumable=jobs.RESUMABLE, max_resumes=jobs.MAX_RESUMES)
    assert db.get_job(j1["id"])["status"] == "queued" and db.get_job(j1["id"])["params"]["_resumes"] == 1
    assert db.get_job(j2["id"])["status"] == "failed"  # enrolling twice would duplicate a sample
    assert db.get_job(j3["id"])["status"] == "failed"  # crash loop guard
