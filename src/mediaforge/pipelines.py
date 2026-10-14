"""End-to-end orchestrations: tts, image, subtitle, video (narrated / short / explainer)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from mediaforge import asr, image, llm, subtitles
from mediaforge import audio as A
from mediaforge.config import settings
from mediaforge.text.normalize import detect_lang, split_sentences
from mediaforge.video import clips, compose, slides
from mediaforge.voice import speak as S

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]

LANG_NAME = {"vi": "Vietnamese", "en": "English"}
# Narration density used to size scripts to a target duration.
UNITS_PER_SEC = {"vi": 4.3, "en": 2.5}  # syllables/s for vi, words/s for en


def _sub(progress: Progress, lo: float, hi: float) -> Progress:
    return lambda p, m: progress(lo + (hi - lo) * p, m)


def _out_dir(kind: str, job_id: str | None) -> Path:
    return settings.sub("outputs", kind, job_id or time.strftime("%Y%m%d-%H%M%S"))


# ------------------------------------------------------------------ planning

def plan(brief: str, *, lang: str, kind: str = "narrated", seconds: int = 60, scenes: int | None = None,
         style: str = "cinematic") -> dict[str, Any]:
    n_units = int(seconds * UNITS_PER_SEC.get(lang, 3))
    n_scenes = scenes or max(3, min(14, round(seconds / (5 if kind == "short" else 8))))
    unit = "syllables" if lang == "vi" else "words"
    extra = {
        "short": "This is a vertical short (TikTok/Reels/Shorts): open with a strong hook in the first sentence, "
                 "short punchy sentences, end with a call to action.",
        "explainer": "This is an explainer: each scene is a slide with a concise title and 2-4 short bullets; "
                     "narration explains the slide conversationally.",
        "narrated": "This is a narrated documentary-style video: clear structure (intro, body, conclusion).",
    }[kind]
    prompt = f"""Write a video plan in {LANG_NAME[lang]} for this brief:
\"\"\"{brief}\"\"\"

{extra}
Total narration ≈ {n_units} {unit} (≈{seconds} seconds spoken), split across {n_scenes} scenes.
Narration must be natural spoken {LANG_NAME[lang]}, no stage directions, no emojis, no markdown.
Write numbers the way they should be read if ambiguous.
For each scene also write an English image-generation prompt ("visual_prompt"): concrete subject, setting,
composition, lighting; style hint: {style}; never ask for text, letters, logos or watermarks in the image.

Return JSON:
{{"title": "...", "description": "one-sentence summary in {LANG_NAME[lang]}",
  "scenes": [{{"narration": "...", "visual_prompt": "...", "slide_title": "...", "bullets": ["..."]}}],
  "hashtags": ["..."]}}
(slide_title and bullets are required only for explainers.)"""
    data, provider = llm.complete_json(prompt, timeout=300)
    if not isinstance(data, dict) or not data.get("scenes"):
        raise llm.LLMError(f"bad plan from {provider}: {str(data)[:300]}")
    data["llm"] = provider
    return data


def visual_prompts_for(narrations: list[str], *, style: str) -> list[str]:
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(narrations))
    prompt = (f"For each narration line below, write one English image-generation prompt that illustrates it "
              f"(concrete subject, setting, composition, lighting; style: {style}; no text/letters/logos in the image).\n"
              f"{numbered}\n\nReturn JSON: {{\"prompts\": [\"...\", ...]}} with exactly {len(narrations)} items.")
    try:
        data, _ = llm.complete_json(prompt, timeout=240)
        prompts = data.get("prompts", [])
        if len(prompts) == len(narrations):
            return [str(p) for p in prompts]
    except Exception as e:  # noqa: BLE001
        log.warning("visual prompt generation failed: %s", e)
    return [f"{style} scene illustrating: {t[:200]}" for t in narrations]


def scenes_from_script(script: str, *, target_scene_seconds: float, lang: str) -> list[str]:
    sents = split_sentences(script)
    per_scene = max(1, round(target_scene_seconds * UNITS_PER_SEC.get(lang, 3) / 14))
    return [" ".join(sents[i : i + per_scene]) for i in range(0, len(sents), per_scene)]


# ------------------------------------------------------------------ jobs

def run_tts(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    out = _out_dir("tts", job_id)
    r = S.speak(p["text"], voice=p.get("voice"), lang=p.get("lang", "auto"), engine=p.get("engine"),
                fx=p.get("fx"), out_dir=out, name="voice", seed=int(p.get("seed", 1234)), progress=progress)
    if p.get("subtitles", True) and r["words"]:
        r["subtitles"] = subtitles.write_all(r["words"], out / "voice")
    return r


def run_image(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    out = _out_dir("images", job_id)
    n = int(p.get("count", 1))
    results = []
    seed = p.get("seed")
    for i in range(n):
        progress(i / n, f"image {i + 1}/{n}")
        results.append(image.generate(
            p["prompt"], out_path=out / f"image_{i + 1}.png", aspect=p.get("aspect", "16:9"), width=p.get("width"),
            height=p.get("height"), seed=(int(seed) + i) if seed is not None else None, steps=p.get("steps"),
            quality=p.get("quality", "high"),
            style=p.get("style", "none"), negative_prompt=p.get("negative_prompt"),
        ))
    return {"images": [r["image"] for r in results], "details": results}


def run_subtitle(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """Transcribe any audio/video into SRT/VTT/ASS; optionally burn into the video; optional translation."""
    out = _out_dir("subtitles", job_id)
    src = Path(p["media"])
    progress(0.05, "extracting audio")
    wav = A.to_wav(src, out / "audio.wav", sr=16_000)
    progress(0.15, "transcribing")
    tr = asr.transcribe(wav, lang=p.get("lang"), initial_prompt=p.get("prompt"))
    words = [w.as_dict() for w in tr["words"]]
    if p.get("script"):
        words = [w.as_dict() for w in asr.align_script(p["script"], tr["words"], A.probe_duration(wav))]
    w, h = 1920, 1080
    is_video = src.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".m4v")
    if is_video:
        info = json.loads(subprocess.run([settings.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                          "stream=width,height", "-of", "json", str(src)], capture_output=True, text=True).stdout or "{}")
        st = (info.get("streams") or [{}])[0]
        w, h = int(st.get("width", w)), int(st.get("height", h))
    files = subtitles.write_all(words, out / "subtitles", width=w, height=h, karaoke=p.get("karaoke"))
    result: dict[str, Any] = {"text": tr["text"], "language": tr["language"], "subtitles": files, "words": words}
    if p.get("translate_to") and p["translate_to"] != tr["language"]:
        progress(0.6, "translating")
        cues = subtitles.build_cues(words)
        lines = [c.text.replace("\n", " ") for c in cues]
        data, _ = llm.complete_json(
            f"Translate each subtitle line into {LANG_NAME.get(p['translate_to'], p['translate_to'])}, keep meaning and "
            f"length similar. Return JSON {{\"lines\": [...]}} with exactly {len(lines)} items.\n" + json.dumps(lines, ensure_ascii=False))
        tl = data.get("lines", [])
        if len(tl) == len(cues):
            for c, t in zip(cues, tl):
                c.lines = subtitles._wrap(str(t).split(), 42)
            result["translated"] = {"srt": str(subtitles.write_srt(cues, out / f"subtitles.{p['translate_to']}.srt"))}
    if is_video and p.get("burn"):
        progress(0.8, "burning subtitles")
        shutil.copy(files["ass"], out / "burn.ass")
        # Relative filter path (cwd=out) avoids FFmpeg filter-escaping of spaces/colons in absolute paths.
        proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src.resolve()),
                               "-vf", "ass=filename=burn.ass", "-c:v", "h264_videotoolbox", "-b:v", "10M",
                               "-c:a", "copy", "subtitled.mp4"], cwd=out, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg burn failed: {proc.stderr[-1500:]}")
        result["video"] = str(out / "subtitled.mp4")
    progress(1.0, "done")
    return result


def run_video(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """kind: narrated (16:9 AI visuals) | short (9:16 karaoke) | explainer (slides) | screen (recording + VO)."""
    kind = p.get("kind", "narrated")
    out = _out_dir("videos", job_id)
    aspect = p.get("aspect") or ("9:16" if kind == "short" else "16:9")
    seconds = int(p.get("seconds", 45 if kind == "short" else 75))
    style = p.get("style", "cinematic")
    voice = p.get("voice")
    t0 = time.time()
    report: dict[str, Any] = {"kind": kind, "aspect": aspect}

    # ---- 1. plan
    progress(0.01, "planning")
    lang = p.get("lang") or "auto"
    if kind == "explainer" and p.get("slides_markdown"):
        md_slides = slides.parse_markdown_slides(p["slides_markdown"])
        lang = lang if lang != "auto" else detect_lang(" ".join(s["narration"] for s in md_slides))
        plan_data = {"title": md_slides[0]["title"] if md_slides else "Explainer",
                     "scenes": [{"narration": s["narration"], "slide_title": s["title"], "bullets": s["bullets"],
                                 "visual_prompt": ""} for s in md_slides]}
    elif p.get("script"):
        lang = lang if lang != "auto" else detect_lang(p["script"])
        if kind == "screen":
            parts = [p["script"]]
        else:
            parts = scenes_from_script(p["script"], target_scene_seconds=5 if kind == "short" else 8, lang=lang)
        prompts = [""] * len(parts) if kind in ("screen",) else visual_prompts_for(parts, style=style)
        plan_data = {"title": p.get("title") or parts[0][:60], "scenes": [
            {"narration": t, "visual_prompt": vp, "slide_title": "", "bullets": []} for t, vp in zip(parts, prompts)]}
    else:
        if not p.get("brief"):
            raise ValueError("provide one of: brief, script, slides_markdown")
        lang = lang if lang != "auto" else detect_lang(p["brief"])
        plan_data = plan(p["brief"], lang=lang, kind=kind if kind != "screen" else "narrated", seconds=seconds,
                         scenes=p.get("scenes"), style=style)
    (out / "plan.json").write_text(json.dumps(plan_data, ensure_ascii=False, indent=2))
    scenes_in = plan_data["scenes"]
    report["llm"] = plan_data.get("llm")

    # ---- 2. narration per scene
    scene_audio, scene_reports = [], []
    vprog = _sub(progress, 0.05, 0.40)
    for i, sc in enumerate(scenes_in):
        vprog(i / len(scenes_in), f"voice {i + 1}/{len(scenes_in)}")
        r = S.speak(sc["narration"], voice=voice, lang=lang, engine=p.get("engine"), fx=p.get("fx"),
                    out_dir=out / "voice", name=f"scene_{i + 1:02d}", seed=int(p.get("seed", 1234)) + 100 * i, timings=False)
        scene_audio.append(A.load(r["audio"]))
        scene_reports.append(r)
    gap = 0.35
    voice_track = A.crossfade_concat(scene_audio, [gap] * (len(scene_audio) - 1), fade=0.005)
    voice_wav = A.save(out / "voice.wav", voice_track)
    durations = [len(a) / A.SR + (gap if i < len(scene_audio) - 1 else 0) for i, a in enumerate(scene_audio)]

    # ---- 3. word timings → subtitles
    progress(0.42, "subtitle timing")
    full_text = " ".join(sc["narration"] for sc in scenes_in)
    tr = asr.transcribe(voice_wav, lang=lang)
    words = [w.as_dict() for w in asr.align_script(full_text, tr["words"], len(voice_track) / A.SR)]
    W, H = compose.RESOLUTIONS[aspect]
    subs = subtitles.write_all(words, out / "subtitles", width=W, height=H, karaoke=p.get("karaoke"))

    # ---- 4. visuals
    visuals: list[compose.Scene] = []
    img_aspect = aspect if aspect in image.ASPECTS else "16:9"
    iprog = _sub(progress, 0.45, 0.80)
    media = p.get("media") or []
    use_clips = bool(p.get("clips")) and clips.available()
    if p.get("clips") and not use_clips:
        report["warning"] = "clips requested but ltx-2-mlx is not installed; used Ken Burns stills"
    for i, sc in enumerate(scenes_in):
        iprog(i / len(scenes_in), f"visual {i + 1}/{len(scenes_in)}")
        if kind == "screen":
            visuals.append(compose.Scene(str(p["recording"]), durations[i], kind="video"))
            continue
        if i < len(media) and media[i]:
            m = str(media[i])
            is_vid = Path(m).suffix.lower() in (".mp4", ".mov", ".webm", ".mkv", ".m4v")
            visuals.append(compose.Scene(m, durations[i], kind="video" if is_vid else "image"))
            continue
        bg = None
        if sc.get("visual_prompt") and not (kind == "explainer" and not p.get("slide_backgrounds", True)):
            gen = image.generate(sc["visual_prompt"], out_path=out / "images" / f"scene_{i + 1:02d}.png",
                                 aspect=img_aspect, seed=int(p.get("seed", 1234)) + i, style=style if style in image.STYLE_PRESETS else "none",
                                 quality=p.get("image_quality", "standard"))
            bg = gen["image"]
        if kind == "explainer":
            sl = slides.render_slide(out / "slides" / f"slide_{i + 1:02d}.png", sc.get("slide_title") or plan_data["title"],
                                     sc.get("bullets") or [], width=W, height=H, theme=p.get("theme", "midnight"), background=bg)
            visuals.append(compose.Scene(str(sl), durations[i], motion="static" if not bg else "zoom_in"))
        elif use_clips and bg:
            clip = clips.image_to_video(bg, sc["visual_prompt"], out / "clips" / f"scene_{i + 1:02d}.mp4",
                                        seconds=min(durations[i], 6))
            visuals.append(compose.Scene(str(clip), durations[i], kind="video"))
        else:
            visuals.append(compose.Scene(bg or str(slides.render_slide(out / "slides" / f"slide_{i + 1:02d}.png", sc["narration"][:80], [], width=W, height=H)),
                                         durations[i]))

    # ---- 5. compose
    burn = p.get("burn_subtitles", True)
    final = compose.compose(
        visuals, voice_wav, out / "final.mp4", aspect=aspect, music=p.get("music"),
        music_volume=float(p.get("music_volume", 0.18)), subtitles_ass=subs["ass"] if burn else None,
        soft_srt=None if burn else subs["srt"], transition=0.0 if kind == "screen" else 0.5,
        progress=_sub(progress, 0.80, 0.98),
    )
    thumb = compose.thumbnail(final, out / "thumbnail.jpg", at=min(1.5, sum(durations) / 2))
    report.update({
        "elapsed": round(time.time() - t0, 1), "scenes": len(scenes_in),
        "qa_failed_sentences": [s for r in scene_reports for s in r["qa_failed_sentences"]],
    })
    result = {"video": str(final), "thumbnail": str(thumb), "title": plan_data.get("title"),
              "duration": round(A.probe_duration(final), 2), "subtitles": subs, "voice": str(voice_wav),
              "plan": str(out / "plan.json"), "lang": lang, "report": report}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return result


def run_enrol(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """Add a recording to a voice profile (runs on the GPU worker because it uses ASR)."""
    from mediaforge.voice import profiles

    progress(0.1, "cleaning and transcribing sample")
    if p.get("demo"):
        return {"voice": profiles.create_demo_voice(p.get("voice", "demo"))}
    sample = profiles.add_sample(p["voice"], p["file"], lang=p.get("lang"), text=p.get("text"),
                                 denoise=p.get("denoise", True))
    return {"sample": sample, "voice": profiles.get_voice(p["voice"])}


RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "tts": run_tts, "image": run_image, "subtitle": run_subtitle, "video": run_video, "enrol": run_enrol,
}


def run(job_type: str, params: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    if job_type in ("short", "explainer", "narrated", "screen"):
        params = {**params, "kind": job_type}
        job_type = "video"
    if job_type not in RUNNERS:
        raise ValueError(f"unknown job type {job_type!r}")
    return RUNNERS[job_type](params, progress, job_id)
