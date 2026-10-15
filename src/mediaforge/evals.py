"""Voice regression evaluation (docs/SDD-review-upgrade.md §5.2.4).

The corpus is text only: voice_regression.jsonl (sentences that failed QA in real jobs up to 2026-10-15) plus every
sentence that has failed QA since (`qa_fail` signals in the local DB), so the corpus grows with each mistake.
Audio is synthesised locally and never leaves data/; results go to data/evals/.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from mediaforge.config import settings

CORPUS = Path(__file__).parent / "voice_regression.jsonl"


def load_corpus(path: str | Path | None = None, *, include_failures: bool = True) -> list[dict[str, Any]]:
    p = Path(path) if path else CORPUS
    rows = [json.loads(line) for line in p.read_text("utf-8").splitlines() if line.strip()]
    if include_failures and not path:
        from mediaforge import lessons

        seen = {" ".join(r["text"].lower().split()) for r in rows}
        for s in lessons.signals("qa_fail"):
            t = s["payload"].get("text") or ""
            k = " ".join(t.lower().split())
            if t and k not in seen:
                seen.add(k)
                rows.append({"text": t, "lang": s["payload"].get("lang", "vi"), "class": s["payload"].get("class", "other"),
                             "from": "signal"})
    return rows


def run(p: dict[str, Any], progress: Callable[[float, str], None], job_id: str | None = None) -> dict[str, Any]:
    from mediaforge.voice import speak as S

    rows = load_corpus(p.get("corpus"))
    if p.get("classes"):
        rows = [r for r in rows if r.get("class") in set(p["classes"])]
    if p.get("limit"):
        rows = rows[: int(p["limit"])]
    voice = p.get("voice")
    out, t0 = [], time.time()
    # Rows are written as they finish, so an interrupted run still leaves its measurements.
    f = settings.sub("evals") / f"voice-{time.strftime('%Y%m%d-%H%M%S')}.json"
    for i, row in enumerate(rows):
        progress(i / max(len(rows), 1), f"eval {i + 1}/{len(rows)}")
        _, rep = S.synth_sentence(row["text"], row.get("lang", "vi"), voice, engine=p.get("engine"), learn=False,
                                  cache=False)
        out.append({**row, "passed": rep["passed"], "score": rep["score"], "heard": rep["heard"],
                    "engine": rep["engine"], "attempts": len(rep["attempts"]), "failure_class": rep.get("failure_class"),
                    "tries": [[a["strategy"], a["engine"], a["score"]] for a in rep["attempts"]]})
        f.write_text(json.dumps({"summary": {"partial": f"{len(out)}/{len(rows)}"}, "rows": out}, ensure_ascii=False, indent=2))
    by: dict[str, list[int]] = {}
    for r in out:
        by.setdefault(r.get("class", "other"), [0, 0])
        by[r.get("class", "other")][0] += int(r["passed"])
        by[r.get("class", "other")][1] += 1
    summary = {"voice": voice, "n": len(out), "passed": sum(r["passed"] for r in out),
               "pass_rate": round(sum(r["passed"] for r in out) / max(len(out), 1), 3),
               "by_class": {k: f"{a}/{b}" for k, (a, b) in by.items()},
               "mean_attempts_failed": round(sum(r["attempts"] for r in out if not r["passed"]) /
                                             max(1, sum(not r["passed"] for r in out)), 2),
               "elapsed": round(time.time() - t0, 1)}
    f.write_text(json.dumps({"summary": summary, "rows": out}, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return {"summary": summary, "file": str(f)}
