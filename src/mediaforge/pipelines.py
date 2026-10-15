"""End-to-end orchestrations: tts, image, subtitle, video (narrated / short / explainer)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from typing import Any, Callable

from mediaforge import asr, image, llm, music, subtitles
from mediaforge import audio as A
from mediaforge.config import settings
from mediaforge.text import documents as D
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

CHIBI_HINT = ("Draw every scene in chibi style: one or two small, cute chibi cartoon characters (oversized heads, "
              "tiny bodies, big sparkling eyes) acting out the scene's idea, e.g. a chibi hacker locking a giant padlock; "
              "always cartoon characters, never photoreal people")


def style_hint(style: str) -> str:
    return f"style: {style}" + (f". {CHIBI_HINT}" if style == "chibi" else "")


# Story shapes the script writer can use (SDD-review-upgrade §5.4.1); "auto" lets it choose.
STRUCTURES = {
    "story": "a short story: a character, a problem, a turning point, a resolution",
    "listicle": "a numbered list of distinct tips or facts, each scene one item",
    "myth-fact": "common myths, each followed by the fact that corrects it",
    "before-after": "the situation before, what changed, the situation after",
    "how-to": "a step-by-step guide, one step per scene",
    "news": "news report: what happened, why it matters, what comes next",
}
# Scene moods; they drive motion, transitions and music (video/compose.MOOD_STYLE).
MOODS = ("calm", "reflective", "curious", "tense", "urgent", "hopeful", "triumphant", "playful")
MUSIC_MOODS = ("piano", "classical", "ambient", "acoustic", "lofi")


def plan(brief: str, *, lang: str, kind: str = "narrated", seconds: int = 60, scenes: int | None = None,
         style: str = "cinematic", n_images: int = 0, structure: str = "auto", critique: bool = True) -> dict[str, Any]:
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
    images_note = (
        f"The brief is a document with {n_images} pictures; a line [ảnh N] marks where picture N appears. When a scene "
        f"covers the text around picture N, add \"image\": N to that scene so the video shows the original picture "
        f"(each picture at most once, in document order; leave it out otherwise). Never write the markers in "
        f"narration.\n" if n_images else "")
    prompt = f"""Write a video plan in {LANG_NAME[lang]} for this brief:
\"\"\"{brief}\"\"\"

{extra}
Total narration ≈ {n_units} {unit} (≈{seconds} seconds spoken), split across {n_scenes} scenes.
Narration must be natural spoken {LANG_NAME[lang]}, no stage directions, no emojis, no markdown.
Write numbers the way they should be read if ambiguous.
For each scene also write an English image-generation prompt ("visual_prompt"): concrete subject, setting,
composition, lighting; {style_hint(style)}; never ask for text, letters, logos or watermarks in the image.
{images_note}
Story shape: {STRUCTURES.get(structure) or "choose the best of: " + "; ".join(f"{k} ({v})" for k, v in STRUCTURES.items())}.
Art direction: decide ONE look for the whole video and describe it in "art_direction": a recurring motif or
character that appears in most scenes (concrete and drawable), the camera language, a palette of 3-4 colour
words, and the background-music mood (one of {", ".join(MUSIC_MOODS)}). Every visual_prompt then describes only
that scene's subject and action — the art direction is added to it automatically, so do not repeat it.
Give each scene a "mood" (one of {", ".join(MOODS)}) that follows the emotional arc; vary it.
Mark at most 3 scenes that open a new section with "chapter": "<2-4 word heading in {LANG_NAME[lang]}>".
"cta": one short closing call to action in {LANG_NAME[lang]} (or "" if none fits).
{'"hooks": two alternative opening sentences for scene 1 in ' + LANG_NAME[lang] + ', different angles (question vs bold claim); scene 1 narration starts with hooks[0].' if kind == "short" else ""}
{images_note}
Return JSON:
{{"title": "...", "description": "one-sentence summary in {LANG_NAME[lang]}", "structure": "<shape used>",
  "art_direction": {{"motif": "...", "camera": "...", "palette": ["...", "..."], "music_mood": "..."}},
  "scenes": [{{"narration": "...", "visual_prompt": "...", "mood": "...", "chapter": "", "slide_title": "...", "bullets": ["..."]{', "image": 1' if n_images else ''}}}],
  {'"hooks": ["...", "..."], ' if kind == "short" else ''}"cta": "...", "hashtags": ["..."]}}
(slide_title and bullets are required only for explainers.)"""
    prompt += lessons_note(kind, style)
    data, provider = llm.complete_json(prompt, timeout=300)
    if not isinstance(data, dict) or not data.get("scenes"):
        raise llm.LLMError(f"bad plan from {provider}: {str(data)[:300]}")
    data["llm"] = provider
    if critique:
        data = self_critique(data, lang=lang, kind=kind, seconds=seconds)
    return data


def self_critique(data: dict[str, Any], *, lang: str, kind: str, seconds: int) -> dict[str, Any]:
    """One extra LLM pass: the planner checks its plan against the script lint rules and the owner's plan rules,
    and returns a corrected plan (owner decision 2026-10-15: on by default). Falls back to the draft."""
    from mediaforge import lessons
    from mediaforge.text import lint as L

    findings = []
    for i, sc in enumerate(data.get("scenes", [])):
        findings += [f"scene {i + 1}: {f['code']} {f['message']}" for f in L.check(str(sc.get("narration", "")), lang)]
    rules = lessons.plan_rules(kind)
    prompt = (f"Here is a draft {LANG_NAME[lang]} video plan (JSON). Review it as a demanding editor and return the "
              f"improved plan with exactly the same JSON shape.\nCheck: every narration sentence is complete and "
              f"natural to say aloud; no sentence longer than ~30 words; the first sentence hooks the viewer; scenes "
              f"flow; moods vary with the story; visual prompts are concrete, drawable and different from each other; "
              f"total length still fits ≈{seconds} s.\n"
              + (f"Problems found by the script checker:\n" + "\n".join(findings[:20]) + "\n" if findings else "")
              + ("Owner's rules from earlier videos:\n" + "\n".join(f"- {x}" for x in rules) + "\n" if rules else "")
              + "\nDraft:\n" + json.dumps({k: v for k, v in data.items() if k != "llm"}, ensure_ascii=False))
    try:
        better, _ = llm.complete_json(prompt, timeout=300)
        if isinstance(better, dict) and isinstance(better.get("scenes"), list) and \
                len(better["scenes"]) >= max(1, len(data["scenes"]) // 2) and all(sc.get("narration") for sc in better["scenes"]):
            for k in ("image",):  # document picture numbers must survive the rewrite
                for a, b in zip(data["scenes"], better["scenes"]):
                    if k in a and k not in b:
                        b[k] = a[k]
            better["llm"], better["critiqued"] = data.get("llm"), True
            return better
    except Exception as e:  # noqa: BLE001 — the draft is still a good plan
        log.warning("self-critique failed: %s", e)
    return data


def lessons_note(kind: str, style: str) -> str:
    """What earlier videos taught the script writer and art director (SDD-review-upgrade §5.1.5)."""
    from mediaforge import lessons

    try:
        rules = lessons.plan_rules(kind)
        disliked = lessons.get(lessons.lesson_id("visual_pref", {"kind": kind}, style))
    except Exception as e:  # noqa: BLE001 — lessons are advisory; never block a render
        log.warning("lessons unavailable: %s", e)
        return ""
    out = ""
    if rules:
        out += "\nLessons from the owner's earlier videos (follow them):\n" + "\n".join(f"- {x}" for x in rules)
    if disliked and disliked["status"] == "active" and disliked["value"].get("direction") == "dislike":
        note = disliked["value"].get("last_note")
        out += (f"\nThe owner disliked the pictures of earlier '{style}' videos"
                + (f" (their words: \"{note}\")" if note else "") + "; make the visual prompts noticeably different "
                "(composition, colour, subject choice) while keeping the style.")
    return out


def visual_prompts_for(narrations: list[str], *, style: str, kind: str = "narrated") -> list[str]:
    return visual_plan_for(narrations, style=style, kind=kind)["prompts"]


def visual_plan_for(narrations: list[str], *, style: str, kind: str = "narrated") -> dict[str, Any]:
    """For the owner's own script: per-line image prompts and moods, plus one art direction for the video."""
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(narrations))
    prompt = (f"For each narration line below, write one English image-generation prompt that illustrates it "
              f"(concrete subject, action, setting; {style_hint(style)}; no text/letters/logos in the image) and its "
              f"mood (one of {', '.join(MOODS)}). First decide ONE art direction for the whole video: a recurring "
              f"motif or character, the camera language, 3-4 palette colour words and a music mood (one of "
              f"{', '.join(MUSIC_MOODS)}); it is added to every prompt automatically, so prompts describe only the "
              f"scene subject.\n{numbered}\n\nReturn JSON: {{\"art_direction\": {{\"motif\": \"...\", \"camera\": \"...\", "
              f"\"palette\": [\"...\"], \"music_mood\": \"...\"}}, \"scenes\": [{{\"visual_prompt\": \"...\", \"mood\": \"...\"}}]}} "
              f"with exactly {len(narrations)} scenes." + lessons_note(kind, style))
    try:
        data, _ = llm.complete_json(prompt, timeout=240)
        sc = data.get("scenes") or [{"visual_prompt": x} for x in data.get("prompts", [])]
        if len(sc) == len(narrations):
            return {"prompts": [str(x.get("visual_prompt", "")) for x in sc], "moods": [x.get("mood") for x in sc],
                    "art_direction": data.get("art_direction") if isinstance(data.get("art_direction"), dict) else {}}
    except Exception as e:  # noqa: BLE001
        log.warning("visual prompt generation failed: %s", e)
    return {"prompts": [f"{style} scene illustrating: {t[:200]}" for t in narrations], "moods": [None] * len(narrations),
            "art_direction": {}}


def art_prompt(visual_prompt: str, ad: dict[str, Any] | None) -> str:
    """The scene prompt plus the video's art direction (so an edit to the art direction reaches every scene)."""
    if not ad:
        return visual_prompt
    bits = [visual_prompt.rstrip(". ")]
    if ad.get("motif"):
        bits.append(f"featuring {ad['motif']}")
    if ad.get("camera"):
        bits.append(str(ad["camera"]))
    pal = ad.get("palette")
    if pal:
        bits.append("colour palette: " + (", ".join(map(str, pal)) if isinstance(pal, list) else str(pal)))
    return ". ".join(bits)


def scenes_from_script(script: str, *, target_scene_seconds: float, lang: str) -> list[str]:
    sents = split_sentences(script)
    per_scene = max(1, round(target_scene_seconds * UNITS_PER_SEC.get(lang, 3) / 14))
    return [" ".join(sents[i : i + per_scene]) for i in range(0, len(sents), per_scene)]


def script_scenes(script: str, *, target_scene_seconds: float, lang: str) -> list[tuple[str, int | None]]:
    """(narration, image number) per scene. Image N goes on the first scene after its [ảnh N] marker, so a picture
    never lands mid-scene; a marker with no text after it goes on the previous scene if that has no picture."""
    out: list[tuple[str, int | None]] = []
    pending: int | None = None

    def park(n: int) -> None:
        if out and out[-1][1] is None:
            out[-1] = (out[-1][0], n)

    for n, chunk in D.split_markers(script):
        if n is not None:
            if pending is not None:
                park(pending)
            pending = n
        for k, t in enumerate(scenes_from_script(chunk, target_scene_seconds=target_scene_seconds, lang=lang)):
            out.append((t, pending if k == 0 else None))
            pending = None
    if pending is not None:
        park(pending)
    return out


def doc_image(p: dict[str, Any], n: Any) -> str | None:
    """Path of imported document picture number n (1-based), if it exists."""
    imgs = p.get("doc_images") or []
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return imgs[n - 1] if 1 <= n <= len(imgs) and Path(imgs[n - 1]).is_file() else None


def apply_document(p: dict[str, Any]) -> dict[str, Any]:
    """`document` (MCP/CLI): import a Word/PDF server-side into brief/script (+ doc_images) unless text is given."""
    if not p.get("document") or p.get("brief") or p.get("script") or p.get("slides_markdown"):
        return p
    doc = D.import_file(p["document"], settings.data_dir)
    key = "script" if p.get("document_as") == "script" else "brief"
    return {**p, key: doc["text"], "doc_images": [im["path"] for im in doc["images"]],
            "title": p.get("title") or doc["title"], "document_warnings": doc["warnings"]}


def sentence_spans(sentences: list[str], words: list[dict]) -> list[list[float]] | None:
    """[start, end] of each sentence in the track, from word timings aligned to " ".join(sentences)."""
    counts = [len(t.split()) for t in sentences]
    if not words or sum(counts) != len(words):
        return None
    spans, k = [], 0
    for n in counts:
        if n == 0:
            spans.append([words[max(k - 1, 0)]["end"]] * 2)
            continue
        spans.append([round(words[k]["start"], 3), round(words[k + n - 1]["end"], 3)])
        k += n
    return spans


# ------------------------------------------------------------------ jobs

def run_tts(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    out = _out_dir("tts", job_id)
    preview = bool(p.get("preview"))  # "Nghe thử": nobody reads timings or subtitles of a preview
    r = S.speak(D.strip_markers(p["text"]), voice=p.get("voice"), lang=p.get("lang", "auto"), engine=p.get("engine"),
                fx=p.get("fx"), out_dir=out, name="voice", seed=int(p.get("seed", 1234)), progress=progress,
                overrides=p.get("overrides"), regen=p.get("regen"), takes=p.get("takes"), timings=not preview)
    r["sentence_spans"] = sentence_spans([x["text"] for x in r["sentences"]], r["words"])
    if p.get("subtitles", not preview) and r["words"]:
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
        layout = subtitles.Layout.for_frame(w, h)
        cues = subtitles.build_cues(words, layout=layout)
        lines = [c.text.replace("\n", " ") for c in cues]
        data, _ = llm.complete_json(
            f"Translate each subtitle line into {LANG_NAME.get(p['translate_to'], p['translate_to'])}, keep meaning and "
            f"length similar. Return JSON {{\"lines\": [...]}} with exactly {len(lines)} items.\n" + json.dumps(lines, ensure_ascii=False))
        tl = data.get("lines", [])
        if len(tl) == len(cues):
            for c, t in zip(cues, tl):
                # A translation can be longer than the source: allow a third line rather than overflow.
                c.lines = subtitles.wrap_words(str(t).split(), layout, max_lines=3) or [str(t)]
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


def make_plan(p: dict[str, Any], kind: str, *, style: str, seconds: int) -> tuple[dict[str, Any], str]:
    """The script and visual prompts of a video: an earlier plan, slides, the owner's script, or the LLM."""
    p = apply_document(p)
    lang = p.get("lang") or "auto"
    if p.get("plan_file") and Path(p["plan_file"]).exists():
        # Re-render of an earlier job, or a plan prepared (and pronunciation-checked) before rendering.
        plan_data = json.loads(Path(p["plan_file"]).read_text("utf-8"))
        if lang == "auto":
            lang = detect_lang(" ".join(sc["narration"] for sc in plan_data["scenes"]))
    elif kind == "explainer" and p.get("slides_markdown"):
        md_slides = slides.parse_markdown_slides(p["slides_markdown"])
        lang = lang if lang != "auto" else detect_lang(" ".join(s["narration"] for s in md_slides))
        plan_data = {"title": md_slides[0]["title"] if md_slides else "Explainer",
                     "scenes": [{"narration": s["narration"], "slide_title": s["title"], "bullets": s["bullets"],
                                 "visual_prompt": "", "image": doc_image(p, s.get("image"))} for s in md_slides]}
    elif p.get("script"):
        lang = lang if lang != "auto" else detect_lang(D.strip_markers(p["script"]))
        if kind == "screen":
            pairs: list[tuple[str, int | None]] = [(D.strip_markers(p["script"]), None)]
        else:
            pairs = script_scenes(p["script"], target_scene_seconds=5 if kind == "short" else 8, lang=lang)
        parts = [t for t, _ in pairs]
        if not parts:
            raise ValueError("the script has no text to read")
        vplan = ({"prompts": [""] * len(parts), "moods": [None] * len(parts), "art_direction": {}} if kind == "screen"
                 else visual_plan_for(parts, style=style, kind=kind))
        plan_data = {"title": p.get("title") or parts[0][:60], "art_direction": vplan["art_direction"], "scenes": [
            {"narration": t, "visual_prompt": vp, "mood": md, "slide_title": "", "bullets": [], "image": doc_image(p, n)}
            for (t, n), vp, md in zip(pairs, vplan["prompts"], vplan["moods"])]}
    else:
        if not p.get("brief"):
            raise ValueError("provide one of: brief, script, slides_markdown")
        lang = lang if lang != "auto" else detect_lang(D.strip_markers(p["brief"]))
        n_images = len(p.get("doc_images") or []) if D.has_markers(p["brief"]) else 0
        plan_data = plan(p["brief"], lang=lang, kind=kind if kind != "screen" else "narrated", seconds=seconds,
                         scenes=p.get("scenes"), style=style, n_images=n_images, structure=p.get("structure", "auto"),
                         critique=p.get("critique", True))
        used: set[str] = set()
        for sc in plan_data["scenes"]:
            sc["narration"] = D.strip_markers(str(sc.get("narration", "")))
            path = doc_image(p, sc.pop("image", None)) if n_images else None
            sc["image"] = path if path and path not in used else None
            used.add(sc["image"] or "")
        plan_data["scenes"] = [sc for sc in plan_data["scenes"] if sc["narration"]]
    if p.get("document_warnings"):
        plan_data["document_warnings"] = p["document_warnings"]
    lint_plan(plan_data, lang)
    return plan_data, lang


def lint_plan(plan_data: dict[str, Any], lang: str) -> None:
    """Fix and record script problems before any GPU time is spent (SDD-review-upgrade §5.2.1)."""
    from mediaforge.text import lint as L

    findings = []
    for i, sc in enumerate(plan_data["scenes"]):
        sc["narration"], f = L.fix_text(sc["narration"], lang)
        findings += [{**x, "scene": i} for x in f]
    plan_data["lint"] = findings


def build_visuals(p: dict[str, Any], plan_data: dict[str, Any], out: Path, durations: list[float], *, kind: str,
                  aspect: str, style: str, report: dict[str, Any], progress: Progress,
                  reuse: bool = False) -> list[compose.Scene]:
    """One compose.Scene per plan scene. reuse=True takes the job's existing images/clips (re-subtitling)."""
    W, H = compose.RESOLUTIONS[aspect]
    scenes_in = plan_data["scenes"]
    visuals: list[compose.Scene] = []
    img_aspect = aspect if aspect in image.ASPECTS else "16:9"
    media = p.get("media") or []
    use_clips = bool(p.get("clips")) and clips.available()
    if p.get("clips") and not use_clips:
        report["warning"] = "clips requested but ltx-2-mlx is not installed; used Ken Burns stills"
    for i, sc in enumerate(scenes_in):
        progress(i / len(scenes_in), f"visual {i + 1}/{len(scenes_in)}")
        if kind == "screen":
            visuals.append(compose.Scene(str(p["recording"]), durations[i], kind="video"))
            continue
        if i < len(media) and media[i]:
            m = str(media[i])
            is_vid = Path(m).suffix.lower() in (".mp4", ".mov", ".webm", ".mkv", ".m4v")
            visuals.append(compose.Scene(m, durations[i], kind="video" if is_vid else "image"))
            continue
        if sc.get("image") and Path(sc["image"]).is_file():  # a picture from the imported document, shown whole
            fig = slides.figure_frame(sc["image"], out / "figures" / f"scene_{i + 1:02d}.png", width=W, height=H,
                                      title=(sc.get("slide_title") or None) if kind == "explainer" else None,
                                      theme=p.get("theme", "midnight"))
            # zoom_in crops ~7 % per side by the end: fine for the padded figure, not for an explainer's title
            visuals.append(compose.Scene(str(fig), durations[i], motion="static" if kind == "explainer" else "zoom_in"))
            continue
        bg = None
        img_path = out / "images" / f"scene_{i + 1:02d}.png"
        clip_path = out / "clips" / f"scene_{i + 1:02d}.mp4"
        if sc.get("visual_prompt") and not (kind == "explainer" and not p.get("slide_backgrounds", True)):
            if reuse and img_path.exists():
                bg = str(img_path)
            else:
                gen = image.generate(art_prompt(sc["visual_prompt"], plan_data.get("art_direction")), out_path=img_path,
                                     aspect=img_aspect,
                                     seed=int(sc.get("seed") or int(p.get("seed", 1234)) + i),
                                     style=style if style in image.STYLE_PRESETS else "none",
                                     quality=p.get("image_quality", "standard"))
                bg = gen["image"]
        if kind == "explainer":
            sl = slides.render_slide(out / "slides" / f"slide_{i + 1:02d}.png", sc.get("slide_title") or plan_data["title"],
                                     sc.get("bullets") or [], width=W, height=H, theme=p.get("theme", "midnight"), background=bg)
            visuals.append(compose.Scene(str(sl), durations[i], motion="static" if not bg else "zoom_in"))
        elif use_clips and bg:
            clip = clip_path if reuse and clip_path.exists() else clips.image_to_video(
                bg, sc["visual_prompt"], clip_path, seconds=min(durations[i], 6))
            visuals.append(compose.Scene(str(clip), durations[i], kind="video"))
        else:
            visuals.append(compose.Scene(bg or str(slides.render_slide(out / "slides" / f"slide_{i + 1:02d}.png", sc["narration"][:80], [], width=W, height=H)),
                                         durations[i]))
    for v, sc in zip(visuals, scenes_in):  # owner overrides from the storyboard
        v.motion = sc.get("motion") or v.motion
        v.transition = sc.get("transition") or v.transition
    if kind != "screen":
        compose.assign_styles(visuals, [sc.get("mood") for sc in scenes_in], gentle=_gentle_motion(kind))
    return add_cards(p, plan_data, out, visuals, width=W, height=H)


def _gentle_motion(kind: str) -> bool:
    from mediaforge import lessons

    try:
        return lessons.preference("motion_pref", kind, kind) == "dislike"
    except Exception:  # noqa: BLE001
        return False


def card_plan(p: dict[str, Any], plan_data: dict[str, Any], *, kind: str, aspect: str, music_id: str | None) -> dict[str, Any]:
    """Title/end cards (owner decision 2026-10-15: on by default for 16:9; the end card only with a CTA or a
    music credit). Decided once per video and stored in the plan so recompose keeps the same timeline."""
    want = p.get("cards", aspect == "16:9" and kind in ("narrated", "explainer"))
    credit = music.credit(music_id) if music_id else None
    cta = str(plan_data.get("cta") or "").strip()
    return {"intro": 2.5 if want else 0.0, "outro": 3.0 if want and (cta or credit) else 0.0,
            "title": plan_data.get("title") or "", "subtitle": plan_data.get("description") or "",
            "cta": cta, "credit": credit or ""}


def add_cards(p: dict[str, Any], plan_data: dict[str, Any], out: Path, visuals: list[compose.Scene], *,
              width: int, height: int) -> list[compose.Scene]:
    cards = plan_data.get("cards") or {}
    if not visuals or not (cards.get("intro") or cards.get("outro")):
        return visuals
    first_bg = next((v.visual for v in visuals if v.kind == "image"), None)
    theme = p.get("theme", "midnight")
    res = list(visuals)
    if cards.get("intro"):
        f = slides.render_card(out / "cards" / "title.png", cards.get("title", ""), cards.get("subtitle", ""),
                               width=width, height=height, background=first_bg, theme=theme)
        res.insert(0, compose.Scene(str(f), cards["intro"], motion="zoom_out", card="title"))
        if len(res) > 1:
            res[1].transition = res[1].transition or "fade"
    if cards.get("outro"):
        f = slides.render_card(out / "cards" / "end.png", cards.get("cta") or cards.get("title", ""), "",
                               width=width, height=height, background=first_bg, theme=theme,
                               small=("♪ " + cards["credit"]) if cards.get("credit") else "")
        res.append(compose.Scene(str(f), cards["outro"], motion="static", transition="fade", card="end"))
    return res


def chapter_ass(ass: str, plan_data: dict[str, Any], durations: list[float], out: Path, *, height: int) -> str:
    """The burned-in subtitle file, plus chapter headings at the start of scenes the plan marks `chapter`."""
    t = float((plan_data.get("cards") or {}).get("intro") or 0)
    chapters = []
    for sc, d in zip(plan_data["scenes"], durations):
        if str(sc.get("chapter") or "").strip():
            chapters.append((t + 0.2, t + min(3.2, max(d - 0.3, 1.0)), str(sc["chapter"]).strip()))
        t += d
    if not chapters:
        return ass
    return str(subtitles.with_chapters(ass, chapters, Path(ass).with_name("subtitles.compose.ass"), height=height))


def auto_music(mood: str | None, *, kind: str, seed: str) -> str | None:
    """music="auto": a downloaded library track whose mood matches the art direction, never one the owner
    disliked (music_pref lessons), liked ones first; deterministic per job."""
    import hashlib

    from mediaforge import lessons

    tracks = [t for t in music.list_tracks() if t["available"]]
    pref = {t["id"]: lessons.preference("music_pref", kind, t["id"]) for t in tracks}
    ok = [t for t in tracks if pref[t["id"]] != "dislike"]
    pool = [t for t in ok if t["mood"] == mood] or ok
    if not pool:
        return None
    liked = [t for t in pool if pref[t["id"]] == "like"]
    pool = liked or pool
    return pool[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(pool)]["id"]


SCENE_GAP = 0.35  # silence between scene narrations
STAGES = ("plan", "voice", "visuals", "compose")


class AwaitApproval(Exception):
    """A gated stage finished; the job waits for the owner (status `waiting`) with a partial result."""

    def __init__(self, stage: str, result: dict[str, Any]):
        super().__init__(f"waiting for approval: {stage}")
        self.stage, self.result = stage, result


def stage_state(out: Path) -> dict[str, Any]:
    f = out / "stages.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else {}


def save_stage_state(out: Path, state: dict[str, Any]) -> None:
    tmp = out / "stages.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(out / "stages.json")


def invalidate(out: Path, after: str) -> None:
    """Forget every stage after `after` (an edited plan re-voices; a new picture re-composes)."""
    state = stage_state(out)
    for st in STAGES[STAGES.index(after) + 1:]:
        state.pop(st, None)
    save_stage_state(out, state)


def storyboard(plan_data: dict[str, Any], out: Path, visuals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visuals = [v for v in visuals if not v.get("card")]  # title/end cards are not scenes
    return [{"scene": i, "narration": sc.get("narration", ""), "visual_prompt": sc.get("visual_prompt", ""),
             "image": v["visual"], "motion": v.get("motion"), "transition": v.get("transition"),
             "mood": sc.get("mood"), "duration": round(v["duration"], 2)}
            for i, (sc, v) in enumerate(zip(plan_data["scenes"], visuals))]


def run_video(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """kind: narrated (16:9 AI visuals) | short (9:16 karaoke) | explainer (slides) | screen (recording + VO).

    Runs as checkpoints plan → voice → visuals → compose (docs/SDD-review-upgrade.md §5.3). Each finished stage is
    recorded in stages.json in the job's folder, so a re-run of the same job (restart, approval, restage) skips it.
    p["review"] lists stages to pause after (status `waiting`) until approved (p["approved"]).
    """
    from mediaforge.text import pronounce

    kind = p.get("kind", "narrated")
    out = _out_dir("videos", job_id)
    aspect = p.get("aspect") or ("9:16" if kind == "short" else "16:9")
    seconds = int(p.get("seconds", 45 if kind == "short" else 75))
    style = p.get("style", "cinematic")
    voice = p.get("voice")
    review, approved = set(p.get("review") or []), set(p.get("approved") or [])
    t0 = time.time()
    music_id = p.get("music")
    music_file = None if music_id == "auto" else music.resolve(music_id)  # fail fast on a missing library track
    state = stage_state(out)
    report: dict[str, Any] = {"kind": kind, "aspect": aspect, **state.get("report", {})}
    timings: dict[str, float] = dict(state.get("timings", {}))

    def done(stage: str, **data: Any) -> None:
        state[stage] = {"done": True, "at": time.time(), **data}
        state["report"], state["timings"] = report, timings
        save_stage_state(out, state)

    def gate(stage: str, partial: dict[str, Any]) -> None:
        if stage in review and stage not in approved:
            raise AwaitApproval(stage, {"stage": stage, "out": str(out), "kind": kind, "aspect": aspect, **partial})

    # ---- 1. plan
    if state.get("plan", {}).get("done"):
        plan_data = json.loads((out / "plan.json").read_text("utf-8"))
        lang = state["plan"]["lang"]
    else:
        ts = time.time()
        progress(0.01, "planning")
        plan_data, lang = make_plan(p, kind, style=style, seconds=seconds)
        if "plan" in review:  # the owner checks the script and the risky words in one place
            text = "\n".join(sc["narration"] for sc in plan_data["scenes"])
            plan_data["prescan"] = pronounce.scan(text, lang, voice=voice, use_llm=p.get("use_llm", True))
        (out / "plan.json").write_text(json.dumps(plan_data, ensure_ascii=False, indent=2))
        report["llm"] = plan_data.get("llm")
        report["lint"] = plan_data.get("lint", [])
        timings["plan"] = round(time.time() - ts, 1)
        done("plan", lang=lang)
    gate("plan", {"plan": str(out / "plan.json"), "plan_data": plan_data, "lang": lang, "title": plan_data.get("title")})
    scenes_in = plan_data["scenes"]
    if music_id == "auto":
        music_id = state.get("music_auto") or auto_music((plan_data.get("art_direction") or {}).get("music_mood"),
                                                          kind=kind, seed=str(job_id or plan_data.get("title")))
        state["music_auto"] = music_id
        music_file = music.resolve(music_id) if music_id else None
    report["music_id"] = music_id

    # ---- 2. narration per scene + word timings → subtitles
    W, H = compose.RESOLUTIONS[aspect]
    if state.get("voice", {}).get("done"):
        vs = json.loads((out / "voice.json").read_text("utf-8"))
        scene_reports, durations, words, subs = vs["scene_reports"], vs["durations"], vs["words"], vs["subs"]
        voice_wav = out / "voice.wav"
    else:
        ts = time.time()
        if p.get("pronunciations") or p.get("verified_terms"):
            # Readings the owner gave before rendering (pre-scan): save them so this and later renders use them.
            report["pronunciations"] = pronounce.remember(lang, p.get("pronunciations") or {}, p.get("verified_terms") or [],
                                                          voice=p.get("lexicon_voice"))
        if "cards" not in plan_data:
            plan_data["cards"] = card_plan(p, plan_data, kind=kind, aspect=aspect, music_id=music_id)
            (out / "plan.json").write_text(json.dumps(plan_data, ensure_ascii=False, indent=2))
        scene_audio, scene_reports = [], []
        vprog = _sub(progress, 0.05, 0.40)
        for i, sc in enumerate(scenes_in):
            vprog(i / len(scenes_in), f"voice {i + 1}/{len(scenes_in)}")
            r = S.speak(sc["narration"], voice=voice, lang=lang, engine=p.get("engine"), fx=p.get("fx"),
                        out_dir=out / "voice", name=f"scene_{i + 1:02d}", seed=int(p.get("seed", 1234)) + 100 * i,
                        timings=False, overrides=p.get("overrides"), regen=p.get("regen"), takes=p.get("takes"))
            scene_audio.append(A.load(r["audio"]))
            scene_reports.append(r)
        voice_track = A.crossfade_concat(scene_audio, [SCENE_GAP] * (len(scene_audio) - 1), fade=0.005)
        cards = plan_data.get("cards") or {}
        if cards.get("intro") or cards.get("outro"):  # silence under the title/end cards keeps one timeline for all
            voice_track = np.concatenate([np.zeros(int(cards.get("intro", 0) * A.SR), dtype=np.float32), voice_track,
                                          np.zeros(int(cards.get("outro", 0) * A.SR), dtype=np.float32)])
        voice_wav = A.save(out / "voice.wav", voice_track)
        durations = [len(a) / A.SR + (SCENE_GAP if i < len(scene_audio) - 1 else 0) for i, a in enumerate(scene_audio)]
        progress(0.42, "subtitle timing")
        full_text = " ".join(sc["narration"] for sc in scenes_in)
        tr = asr.transcribe(voice_wav, lang=lang)
        words = [w.as_dict() for w in asr.align_script(full_text, tr["words"], len(voice_track) / A.SR)]
        subs = subtitles.write_all(words, out / "subtitles", width=W, height=H, karaoke=p.get("karaoke"))
        (out / "voice.json").write_text(json.dumps({"scene_reports": scene_reports, "durations": durations,
                                                    "words": words, "subs": subs}, ensure_ascii=False))
        timings["voice"] = round(time.time() - ts, 1)
        done("voice")

    # ---- 3. visuals (existing pictures are reused, so a resumed or restaged job only makes what is missing)
    if state.get("visuals", {}).get("done") and (out / "visuals.json").exists():
        vis = json.loads((out / "visuals.json").read_text("utf-8"))
    else:
        ts = time.time()
        scenes = build_visuals(p, plan_data, out, durations, kind=kind, aspect=aspect, style=style, report=report,
                               progress=_sub(progress, 0.45, 0.80), reuse=True)
        vis = [v.as_dict() for v in scenes]
        (out / "visuals.json").write_text(json.dumps(vis, ensure_ascii=False, indent=2))
        timings["visuals"] = round(time.time() - ts, 1)
        done("visuals")
    gate("visuals", {"plan": str(out / "plan.json"), "lang": lang, "title": plan_data.get("title"),
                     "storyboard": storyboard(plan_data, out, vis), "voice": str(voice_wav), "music": music_id})

    # ---- 4. compose
    ts = time.time()
    burn = p.get("burn_subtitles", True)
    final = compose.compose(
        [compose.Scene.from_dict(v) for v in vis],
        voice_wav, out / "final.mp4", aspect=aspect, music=music_file,
        music_volume=float(p.get("music_volume", 0.18)),
        subtitles_ass=chapter_ass(subs["ass"], plan_data, durations, out, height=H) if burn else None,
        soft_srt=None if burn else subs["srt"], transition=0.0 if kind == "screen" else 0.5,
        progress=_sub(progress, 0.80, 0.98),
    )
    thumb = compose.thumbnail(final, out / "thumbnail.jpg", at=min(1.5, sum(durations) / 2))
    timings["compose"] = round(time.time() - ts, 1)
    report.update({
        "elapsed": round(time.time() - t0, 1), "scenes": len(scenes_in), "music_credit": music.credit(music_id),
        "qa_failed_sentences": [s for r in scene_reports for s in r["qa_failed_sentences"]], "stages": timings,
    })
    done("compose")
    sentences = [{**sen, "scene": si, "index": k} for k, (si, sen) in enumerate(
        (si, sen) for si, r in enumerate(scene_reports) for sen in r["sentences"])]
    result = {"video": str(final), "thumbnail": str(thumb), "title": plan_data.get("title"),
              "sentences": sentences, "voice_id": scene_reports[0]["voice"] if scene_reports else voice,
              "qa_failed_sentences": report["qa_failed_sentences"],
              "duration": round(A.probe_duration(final), 2), "subtitles": subs, "voice": str(voice_wav),
              "plan": str(out / "plan.json"), "lang": lang, "report": report,
              "fx": scene_reports[0]["fx"] if scene_reports else None, "words": words,
              "sentence_spans": sentence_spans([s["text"] for s in sentences], words)}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return result


def run_prepare(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """Write the video script first and scan it for terms the TTS may misread (SDD-subtitle-fit-pronunciation §5).

    The owner answers in the dashboard, then renders with {plan_file, pronunciations}.
    """
    from mediaforge.text import pronounce

    kind = p.get("kind", "narrated")
    out = _out_dir("plans", job_id)
    progress(0.05, "viết kịch bản")
    plan_data, lang = make_plan(p, kind, style=p.get("style", "cinematic"),
                                seconds=int(p.get("seconds", 45 if kind == "short" else 75)))
    plan_file = out / "plan.json"
    plan_file.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2))
    progress(0.6, "quét từ dễ đọc sai")
    text = "\n".join(sc["narration"] for sc in plan_data["scenes"])
    candidates = pronounce.scan(text, lang, voice=p.get("voice"), use_llm=p.get("use_llm", True))
    progress(1.0, "done")
    return {"plan_file": str(plan_file), "title": plan_data.get("title"), "lang": lang, "kind": kind,
            "scenes": [sc["narration"] for sc in plan_data["scenes"]], "candidates": candidates,
            "lint": plan_data.get("lint", [])}


def run_resubtitle(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """Rebuild a finished video's subtitles and re-compose it from its own voice track and images.

    No TTS, no ASR, no image generation: the stored word timings, voice.wav and images/ are reused, so the
    picture and the voice are exactly the original render (SDD-subtitle-fit-pronunciation §4).
    """
    return recompose(p, progress, new_subtitles=True)


def recompose(p: dict[str, Any], progress: Progress, *, new_subtitles: bool,
              param_changes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-compose a finished video in place from its voice track and pictures (missing pictures are generated) and
    swap it in; the old render and its exports go to trash. Used by resubtitle and restage."""
    from mediaforge import takes as T

    src_id = p["job"]
    job = T.load_done_job(src_id)
    r, jp = job["result"], {**job["params"], **(param_changes or {})}
    if not T.is_video(r) or not r.get("words") or not r.get("plan"):
        raise ValueError(f"job {src_id} is not a finished video with word timings")
    out = Path(r["video"]).parent
    kind = r.get("report", {}).get("kind") or jp.get("kind") or job["type"]
    kind = kind if kind in ("narrated", "short", "explainer", "screen") else "narrated"
    aspect = r.get("report", {}).get("aspect") or jp.get("aspect") or ("9:16" if kind == "short" else "16:9")
    W, H = compose.RESOLUTIONS[aspect]
    plan_data = json.loads(Path(r["plan"]).read_text("utf-8"))
    if param_changes and "music" in param_changes and plan_data.get("cards", {}).get("outro"):
        plan_data["cards"]["credit"] = music.credit(param_changes["music"]) or ""  # the end card names the new track
        Path(r["plan"]).write_text(json.dumps(plan_data, ensure_ascii=False, indent=2))
    scene_wavs = sorted((out / "voice").glob("scene_*.wav"))
    scene_wavs = [w for w in scene_wavs if w.stem.count("_") == 1]  # scene_01.wav, not scene_01_take…
    if len(scene_wavs) != len(plan_data["scenes"]):
        raise ValueError(f"job {src_id}: {len(scene_wavs)} scene voice files for {len(plan_data['scenes'])} scenes")
    lens = [A.probe_duration(w) for w in scene_wavs]
    durations = [d + (SCENE_GAP if i < len(lens) - 1 else 0) for i, d in enumerate(lens)]

    progress(0.02, "dựng lại phụ đề" if new_subtitles else "dựng lại video")
    karaoke = p.get("karaoke", jp.get("karaoke"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = out / f".resub-{stamp}"
    work.mkdir()
    try:
        if new_subtitles:
            subs = subtitles.write_all(r["words"], work / "subtitles", width=W, height=H, karaoke=karaoke)
        else:
            subs = {}
            for ext in ("srt", "vtt", "ass"):
                shutil.copy(out / f"subtitles.{ext}", work / f"subtitles.{ext}")
                subs[ext] = str(work / f"subtitles.{ext}")
        report: dict[str, Any] = {}
        visuals = build_visuals(jp, plan_data, out, durations, kind=kind, aspect=aspect,
                                style=jp.get("style", "cinematic"), report=report,
                                progress=_sub(progress, 0.05, 0.5 if not new_subtitles else 0.15), reuse=True)
        (out / "visuals.json").write_text(json.dumps([v.as_dict() for v in visuals], ensure_ascii=False, indent=2))
        burn = jp.get("burn_subtitles", True)
        final_new = compose.compose(
            visuals, r["voice"], work / "final.mp4", aspect=aspect, music=music.resolve(jp.get("music")),
            music_volume=float(jp.get("music_volume", 0.18)),
            subtitles_ass=chapter_ass(subs["ass"], plan_data, durations, work, height=H) if burn else None,
            soft_srt=None if burn else subs["srt"], transition=0.0 if kind == "screen" else 0.5,
            progress=_sub(progress, 0.5 if not new_subtitles else 0.15, 0.95))
        # Swap in: the old render and every export made from it (they carry the old burned subtitles) go to trash.
        trash = settings.sub("trash", f"{src_id}-resub-{stamp}")
        fresh = T.load_done_job(src_id)["result"]
        for e in fresh.get("exports") or []:
            if Path(e["file"]).exists():
                shutil.move(e["file"], trash / Path(e["file"]).name)
        shutil.move(str(out / "final.mp4"), trash / "final.mp4")
        for ext in ("srt", "vtt", "ass"):
            if (out / f"subtitles.{ext}").exists():
                shutil.move(str(out / f"subtitles.{ext}"), trash / f"subtitles.{ext}")
            shutil.move(subs[ext], out / f"subtitles.{ext}")
        shutil.move(str(final_new), out / "final.mp4")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    final = out / "final.mp4"
    compose.thumbnail(final, out / "thumbnail.jpg", at=min(1.5, sum(durations) / 2))
    fresh["subtitles"] = {ext: str(out / f"subtitles.{ext}") for ext in ("srt", "vtt", "ass")}
    fresh["duration"] = round(A.probe_duration(final), 2)
    fresh["exports"] = []
    fresh["rendered_at"] = time.time()  # dashboard cache-bust for the swapped final.mp4
    if new_subtitles:
        fresh["resubtitled_at"] = fresh["rendered_at"]
    if new_subtitles:
        fresh.setdefault("report", {})["resubtitle"] = {"at": time.time(), "trash": str(trash), "karaoke": karaoke is not False}
    else:
        fresh.setdefault("report", {}).setdefault("restaged", []).append({"at": time.time(), "trash": str(trash),
                                                                          **(p.get("log") or {})})
    if param_changes:
        from mediaforge import db

        db.update_job(src_id, params={**job["params"], **param_changes})
        if "music" in param_changes:
            fresh.setdefault("report", {})["music_credit"] = music.credit(param_changes["music"])
    T.save_result(src_id, fresh)
    progress(1.0, "done")
    return {"job": src_id, "video": str(final), "subtitles": fresh["subtitles"], "trash": str(trash)}


def run_enrol(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """Add a recording to a voice profile (runs on the GPU worker because it uses ASR)."""
    from mediaforge.voice import profiles

    progress(0.1, "cleaning and transcribing sample")
    if p.get("demo"):
        return {"voice": profiles.create_demo_voice(p.get("voice", "demo"))}
    sample = profiles.add_sample(p["voice"], p["file"], lang=p.get("lang"), text=p.get("text"),
                                 denoise=p.get("denoise", True))
    return {"sample": sample, "voice": profiles.get_voice(p["voice"])}


def run_blend(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """Pha giọng: calibrate and save a blend profile (owner dominant + a consenting partner), or change its share."""
    from mediaforge.voice import blend

    return blend.run_job(p, progress)


def run_rerender(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge import takes

    return takes.rerender(p, progress, job_id)


def run_export(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge import export

    return export.run(p, progress, job_id)


def run_repronounce(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge import repronounce

    return repronounce.run(p, progress, job_id)


def run_sing(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge.sing import pipeline as sing

    return sing.run(p, progress, job_id, kind="sing")


def run_recite(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge.sing import pipeline as sing

    return sing.run(p, progress, job_id, kind="recite")


def run_restage(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge import restage

    return restage.run(p, progress, job_id)


def apply_plan_edits(out: Path, edits: dict[str, Any]) -> dict[str, Any]:
    """The owner edited the script at the plan gate: {title?, scenes: [{narration?, visual_prompt?}], hook?}.
    Narration is linted again; every later stage is invalidated."""
    plan_file = out / "plan.json"
    plan_data = json.loads(plan_file.read_text("utf-8"))
    lang = stage_state(out).get("plan", {}).get("lang", "vi")
    if edits.get("title"):
        plan_data["title"] = str(edits["title"])[:200]
    new_scenes = edits.get("scenes")
    if isinstance(new_scenes, list) and new_scenes:
        kept = []
        for k, e in enumerate(new_scenes):
            base = dict(plan_data["scenes"][e.get("from", k)]) if 0 <= int(e.get("from", k)) < len(plan_data["scenes"]) else {}
            for f in ("narration", "visual_prompt", "slide_title", "bullets", "mood"):
                if f in e:
                    base[f] = e[f]
            if str(base.get("narration", "")).strip():
                kept.append(base)
        plan_data["scenes"] = kept
    if edits.get("art_direction") and isinstance(edits["art_direction"], dict):
        plan_data["art_direction"] = {**plan_data.get("art_direction", {}), **edits["art_direction"]}
    if "hook" in edits and plan_data.get("hooks"):
        plan_data["hook_choice"] = int(edits["hook"])
        hooks = plan_data["hooks"]
        if 0 <= plan_data["hook_choice"] < len(hooks) and plan_data["scenes"]:
            plan_data["scenes"][0]["narration"] = hooks[plan_data["hook_choice"]]
    lint_plan(plan_data, lang)
    plan_file.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2))
    invalidate(out, "plan")
    return plan_data


def run_eval(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge import evals

    return evals.run(p, progress, job_id)


RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "tts": run_tts, "image": run_image, "subtitle": run_subtitle, "video": run_video, "enrol": run_enrol,
    "rerender": run_rerender, "export": run_export, "prepare": run_prepare, "resubtitle": run_resubtitle,
    "repronounce": run_repronounce, "sing": run_sing, "recite": run_recite,
    "blend": run_blend, "eval": run_eval, "restage": run_restage,
}


def run(job_type: str, params: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    if job_type in ("short", "explainer", "narrated", "screen"):
        params = {**params, "kind": job_type}
        job_type = "video"
    if job_type not in RUNNERS:
        raise ValueError(f"unknown job type {job_type!r}")
    return RUNNERS[job_type](params, progress, job_id)
