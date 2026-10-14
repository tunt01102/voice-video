"""Export a finished job with a chosen voice per sentence, in a chosen format — without re-rendering the video.

Default path ("remux"): splice the selected takes into the voice track, re-mix it exactly like compose (music
ducking + loudnorm) and copy the video stream. Fallback ("recompose"): a take that cannot fit its slot re-runs
the video pipeline from plan.json with cached images. See docs/SDD-guided-repair.md §5.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from mediaforge import audio as A
from mediaforge import music
from mediaforge import takes as T
from mediaforge.config import settings
from mediaforge.video import compose

Progress = Callable[[float, str], None]

# One table drives the dashboard dropdown, the API validation and the ffmpeg arguments.
FORMATS: dict[str, dict[str, Any]] = {
    "mp4": {"label": "MP4 (H.264)", "video": True, "v": ["-c:v", "copy"], "a": ["-c:a", "aac", "-b:a", "192k"],
            "s": ["-c:s", "mov_text"], "extra": ["-movflags", "+faststart"]},
    "mov": {"label": "MOV (QuickTime)", "video": True, "v": ["-c:v", "copy"], "a": ["-c:a", "aac", "-b:a", "192k"],
            "s": ["-c:s", "mov_text"], "extra": ["-movflags", "+faststart"]},
    "mkv": {"label": "MKV (Matroska)", "video": True, "v": ["-c:v", "copy"], "a": ["-c:a", "aac", "-b:a", "192k"],
            "s": ["-c:s", "srt"], "extra": []},
    "webm": {"label": "WebM (VP9, chậm hơn)", "video": True,
             "v": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"],
             "a": ["-c:a", "libopus", "-b:a", "160k"], "s": ["-c:s", "webvtt"], "extra": []},
    "mp3": {"label": "MP3 (chỉ âm thanh)", "video": False, "a": ["-c:a", "libmp3lame", "-b:a", "192k"]},
    "wav": {"label": "WAV (chỉ âm thanh)", "video": False, "a": ["-c:a", "pcm_s16le", "-ar", "48000"]},
    "m4a": {"label": "M4A (chỉ âm thanh)", "video": False, "a": ["-c:a", "aac", "-b:a", "192k"]},
}
VIDEO_JOB_FORMATS = ["mp4", "mov", "mkv", "webm", "mp3", "wav", "m4a"]  # first = default
AUDIO_JOB_FORMATS = ["mp3", "wav", "m4a"]


def formats_for(result: dict[str, Any]) -> list[str]:
    return VIDEO_JOB_FORMATS if T.is_video(result) else AUDIO_JOB_FORMATS


def catalog() -> dict[str, list[dict[str, str]]]:
    def rows(ids: list[str]) -> list[dict[str, str]]:
        return [{"id": f, "label": FORMATS[f]["label"]} for f in ids]

    return {"video": rows(VIDEO_JOB_FORMATS), "audio": rows(AUDIO_JOB_FORMATS)}


def has_subtitle_stream(path: str | Path) -> bool:
    out = subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "s", "-show_entries", "stream=index",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return bool(json.loads(out or "{}").get("streams"))


def ffmpeg_args(fmt: str, base: str | Path, out: str | Path, *, voice: str | Path | None = None,
                music_file: str | Path | None = None, music_volume: float = 0.18, base_is_video: bool = True,
                subs: bool = False) -> list[str]:
    """Arguments (after `ffmpeg -y`) that write `out` in `fmt`.

    base:  the finished video (or audio, for TTS jobs).
    voice: a new voice-only track to mix in place of base's audio (None = keep base's audio).
    """
    spec = FORMATS[fmt]
    args = ["-i", str(base)]
    if voice is not None and base_is_video:
        args += ["-i", str(voice)]
        if music_file:
            args += ["-stream_loop", "-1", "-i", str(music_file)]
        args += ["-filter_complex", ";".join(compose.audio_graph(1, 2 if music_file else None, music_volume))]
        amap = ["-map", "[aout]"]
    elif voice is not None:
        args = ["-i", str(voice)]
        amap = ["-map", "0:a"]
    else:
        amap = ["-map", "0:a"]
    if spec["video"] and base_is_video:
        args += ["-map", "0:v", *amap]
        if subs:
            args += ["-map", "0:s", *spec["s"]]
        args += [*spec["v"], *spec["a"], *spec["extra"]]
        if voice is not None:
            args += ["-shortest"]
    else:
        args += [*amap, "-vn", *spec["a"]]
    return [*args, str(out)]


def _selection_key(selection: dict[int, str], fmt: str) -> str:
    raw = json.dumps({"sel": {str(k): v for k, v in sorted(selection.items())}, "fmt": fmt}, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


def _label(fmt: str, selection: dict[int, str]) -> str:
    n = len(selection)
    return f"{fmt.upper()} · " + (f"{n} câu dùng bản AI sửa" if n else "giọng gốc")


def run(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """p: {job, selection: {index: take_id | "original"}, format}."""
    from mediaforge import pipelines

    src_id = p["job"]
    job = T.load_done_job(src_id)
    r = job["result"]
    video = T.is_video(r)
    fmt = (p.get("format") or formats_for(r)[0]).lower()
    if fmt not in formats_for(r):
        raise ValueError(f"format {fmt!r} not available for this job; choose one of {formats_for(r)}")
    sents = r.get("sentences") or []
    selection: dict[int, str] = {}
    chosen: dict[int, dict[str, Any]] = {}
    for k, tid in (p.get("selection") or {}).items():
        i = int(k)
        if not tid or tid == "original":
            continue
        take = next((t for t in (sents[i].get("takes") or []) if t["id"] == tid), None) if 0 <= i < len(sents) else None
        if not take:
            raise ValueError(f"sentence {i} has no take {tid!r}")
        selection[i], chosen[i] = tid, take

    key = _selection_key(selection, fmt)
    exp_dir = T.source_dir(r) / "exports"
    exp_dir.mkdir(parents=True, exist_ok=True)
    prior = next((e for e in r.get("exports") or [] if e["id"] == key and Path(e["file"]).exists()), None)
    if prior:
        progress(1.0, "done")
        return {"job": src_id, "export": prior, "file": prior["file"], "reused": True}

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = exp_dir / f"{stamp}-{key}.{fmt}"
    base = Path(r["video"] if video else r["audio"])
    new_voice: Path | None = None
    path, modes = "copy", {}
    music_file = music.resolve(job["params"].get("music")) if video else None
    if chosen:
        progress(0.05, "ghép câu đã sửa vào giọng đọc")
        spans = T.ensure_spans(job)
        track = A.load(T.voice_track(r))
        reps = {i: A.load(t["audio"]) for i, t in chosen.items()}
        try:
            spliced, modes = T.splice(track, spans, reps, grow=not video)
            new_voice = A.save(exp_dir / f"{stamp}-{key}.voice.wav", spliced)
            path = "remux"
        except T.Overflow as e:
            # The take is too long for its slot: rebuild the timeline (script and images come from cache).
            progress(0.1, f"câu {e.index + 1} dài hơn chỗ trống — dựng lại timeline")
            params = {**job["params"], "plan_file": r.get("plan"),
                      "takes": {sents[i]["text"]: t["file"] for i, t in chosen.items()}}
            rebuilt = pipelines.run_video(params, pipelines._sub(progress, 0.1, 0.85), job_id=f"{job_id or stamp}-export")
            base, path = Path(rebuilt["video"]), "recompose"
    elif fmt == "mp4" and video:
        progress(0.5, "sao chép")
        shutil.copy(base, out)
    if not out.exists():
        progress(0.88, f"xuất {fmt.upper()}")
        args = ffmpeg_args(fmt, base, out, voice=new_voice, music_file=music_file,
                           music_volume=float(job["params"].get("music_volume", 0.18)), base_is_video=video,
                           subs=video and FORMATS[fmt]["video"] and has_subtitle_stream(base))
        proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg export failed: {proc.stderr[-1500:]}")
    if new_voice:
        new_voice.unlink(missing_ok=True)

    entry = {"id": key, "file": str(out), "format": fmt, "selection": {str(k): v for k, v in selection.items()},
             "path": path, "modes": {str(k): v for k, v in modes.items()}, "label": _label(fmt, selection),
             "created_at": time.time(), "duration": round(A.probe_duration(out), 2), "export_job": job_id}
    fresh = T.load_done_job(src_id)["result"]
    fresh["exports"] = [e for e in fresh.get("exports") or [] if e["id"] != key] + [entry]
    T.save_result(src_id, fresh)
    progress(1.0, "done")
    return {"job": src_id, "export": entry, "file": str(out)}

