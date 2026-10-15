"""Restage: redo one stage of a video without re-rendering the rest (docs/SDD-review-upgrade.md §5.3).

- stage "visuals", scene i: new picture for that scene (new seed and/or edited prompt). On a job waiting at the
  storyboard gate only the picture is made; on a finished video the video is then re-composed in place.
- stage "compose": change music / music volume and re-compose (voice, subtitles and pictures are reused).
Old pictures and renders go to data/trash, never deleted.
"""

from __future__ import annotations

import json
import random
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from mediaforge import db, lessons
from mediaforge.config import settings

Progress = Callable[[float, str], None]


def _trash(src_id: str, f: Path) -> None:
    if f.exists():
        dest = settings.sub("trash", f"{src_id}-restage-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.move(str(f), dest / f.name)


def _new_picture(src_id: str, out: Path, i: int, prompt: str | None, seed: int | None) -> dict[str, Any]:
    plan_file = out / "plan.json"
    plan = json.loads(plan_file.read_text("utf-8"))
    if not 0 <= i < len(plan["scenes"]):
        raise ValueError(f"scene {i} not found")
    sc = plan["scenes"][i]
    old_prompt = sc.get("visual_prompt")
    if prompt and prompt.strip():
        sc["visual_prompt"] = prompt.strip()
    sc["seed"] = int(seed) if seed is not None else random.randint(1, 2**31 - 1)
    if prompt:
        sc.pop("image", None)  # an edited prompt replaces a document picture with a generated one
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    n = f"scene_{i + 1:02d}"
    for f in (out / "images" / f"{n}.png", out / "slides" / f"slide_{i + 1:02d}.png", out / "figures" / f"{n}.png",
              out / "clips" / f"{n}.mp4"):
        _trash(src_id, f)
    lessons.signal("scene_regenerated", {"scene": i, "prompt_changed": bool(prompt), "old_prompt": old_prompt,
                                         "new_prompt": sc.get("visual_prompt")}, src_id)
    return plan


def run(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge import pipelines as P

    src_id, stage = p["job"], p.get("stage", "visuals")
    job = db.get_job(src_id)
    if not job:
        raise ValueError(f"job {src_id} not found")
    if job["status"] == "waiting":
        if stage != "visuals" or (job["result"] or {}).get("stage") != "visuals":
            raise ValueError("a waiting job can only change pictures at the storyboard step")
        out = Path(job["result"]["out"])
        plan = _new_picture(src_id, out, int(p["scene"]), p.get("prompt"), p.get("seed"))
        vs = json.loads((out / "voice.json").read_text("utf-8"))
        jp = job["params"]
        kind = job["result"].get("kind") or jp.get("kind", "narrated")
        scenes = P.build_visuals(jp, plan, out, vs["durations"], kind=kind, aspect=job["result"]["aspect"],
                                 style=jp.get("style", "cinematic"), report={}, progress=progress, reuse=True)
        vis = [v.as_dict() for v in scenes]
        (out / "visuals.json").write_text(json.dumps(vis, ensure_ascii=False, indent=2))
        board = P.storyboard(plan, out, vis)
        result = {**job["result"], "storyboard": board, "updated_at": time.time()}
        db.update_job(src_id, result=result)  # still waiting
        progress(1.0, "done")
        return {"job": src_id, "scene": int(p["scene"]), "image": board[int(p["scene"])]["image"]}
    if job["status"] != "done":
        raise ValueError("only a finished video or one waiting at the storyboard can be restaged")
    r = job["result"] or {}
    out = Path(r["video"]).parent
    if stage == "visuals":
        _new_picture(src_id, out, int(p["scene"]), p.get("prompt"), p.get("seed"))
        return P.recompose({"job": src_id, "log": {"stage": "visuals", "scene": int(p["scene"])}}, progress,
                           new_subtitles=False)
    if stage == "compose":
        changes = {k: p[k] for k in ("music", "music_volume") if k in p}
        if changes.get("music") == "__none":
            changes["music"] = None
        if not changes:
            raise ValueError("nothing to change: give music and/or music_volume")
        return P.recompose({"job": src_id, "log": {"stage": "compose", **changes}}, progress, new_subtitles=False,
                           param_changes=changes)
    raise ValueError(f"unknown stage {stage!r}")
