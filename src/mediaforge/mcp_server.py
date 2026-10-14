"""MCP tools so any Claude Code session on this Mac can generate media.

Register once:  claude mcp add -s user --transport http mediaforge http://127.0.0.1:8765/mcp
"""

from __future__ import annotations

from typing import Any

import anyio
from mcp.server import MCPServer

from mediaforge import db, music
from mediaforge.jobs import runner
from mediaforge.voice import profiles

mcp = MCPServer(
    "mediaforge",
    instructions=(
        "Local media generation on this Mac: cloned-voice speech (English/Vietnamese), subtitles, images and "
        "videos. Long jobs return a job_id; poll job_status or pass wait_seconds. Output paths are absolute local "
        "files. Use list_voices to find the owner's voice id (prefer it over 'demo')."
    ),
)


def _summary(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {"error": "job not found"}
    out = {k: job[k] for k in ("id", "type", "status", "progress", "message")}
    if job.get("result"):
        r = job["result"]
        out["result"] = {k: r[k] for k in r if k not in ("words", "sentences")}
        if "sentences" in r:
            out["result"]["qa_failed_sentences"] = r.get("qa_failed_sentences", [])
    if job.get("error"):
        out["error"] = job["error"].splitlines()[0]
    return out


async def _submit(job_type: str, params: dict[str, Any], wait_seconds: float) -> dict[str, Any]:
    job = runner.submit(job_type, {k: v for k, v in params.items() if v is not None}, source="mcp")
    if wait_seconds > 0:
        job = await anyio.to_thread.run_sync(runner.wait, job["id"], wait_seconds)
    return _summary(job)


@mcp.tool()
async def speak(text: str, voice: str | None = None, lang: str = "auto", pitch: float = 0.0, pace: float = 1.0,
                warmth: float = 0.0, clarity: float = 0.5, engine: str | None = None,
                wait_seconds: float = 300) -> dict[str, Any]:
    """Synthesise speech in a cloned voice (English 'en', Vietnamese 'vi', or 'auto' per sentence).

    pitch: semitones -6..+6 (negative = deeper). pace: 0.7..1.4. warmth/clarity: 0..1.
    engine: None (voice default), 'vieneu', 'voxcpm' or 'best'. Every sentence is checked by speech
    recognition and automatically regenerated/respelled if mispronounced. Returns wav/mp3 + SRT/VTT/ASS paths.
    """
    return await _submit("tts", {"text": text, "voice": voice, "lang": lang, "engine": engine,
                                 "fx": {"pitch": pitch, "pace": pace, "warmth": warmth, "clarity": clarity}}, wait_seconds)


@mcp.tool()
async def generate_image(prompt: str, aspect: str = "16:9", count: int = 1, style: str = "none",
                         seed: int | None = None, wait_seconds: float = 240) -> dict[str, Any]:
    """Generate image(s) locally (Z-Image-Turbo). aspect: 16:9, 9:16, 1:1, 4:3, 3:4.
    style: none, cinematic, photo, illustration, 3d, minimal, tech. Prompt in English works best."""
    return await _submit("image", {"prompt": prompt, "aspect": aspect, "count": count, "style": style, "seed": seed},
                         wait_seconds)


@mcp.tool()
async def prepare_video(brief: str | None = None, script: str | None = None, kind: str = "narrated",
                        lang: str = "auto", voice: str | None = None, seconds: int = 60, style: str = "cinematic",
                        slides_markdown: str | None = None, wait_seconds: float = 300) -> dict[str, Any]:
    """Step 1 of a video: write the script and scan it for terms the TTS may misread (acronyms, foreign names,
    codes, symbols) BEFORE rendering.

    Returns plan_file, the scene narrations and `candidates` [{term, say, known, reason_labels, context}].
    Decide a reading for each unknown term (ask the user when unsure, e.g. PNJ -> 'Pi En Di'), then call
    create_video(plan_file=..., pronunciations={term: reading}, verified_terms=[terms that read fine as written]).
    The answers are saved to the pronunciation lexicon for every later render.
    """
    return await _submit("prepare", {"brief": brief, "script": script, "kind": kind, "lang": lang, "voice": voice,
                                     "seconds": seconds, "style": style, "slides_markdown": slides_markdown},
                         wait_seconds)


@mcp.tool()
async def create_video(brief: str | None = None, script: str | None = None, kind: str = "narrated",
                       lang: str = "auto", voice: str | None = None, seconds: int = 60, style: str = "cinematic",
                       aspect: str | None = None, music: str | None = None, pitch: float = 0.0,
                       slides_markdown: str | None = None, media: list[str] | None = None,
                       burn_subtitles: bool = True, plan_file: str | None = None,
                       pronunciations: dict[str, str] | None = None, verified_terms: list[str] | None = None,
                       karaoke: bool = True, wait_seconds: float = 0) -> dict[str, Any]:
    """Create a narrated video with cloned voice, generated visuals and subtitles.

    kind: 'narrated' (16:9 AI visuals), 'short' (9:16 with karaoke subtitles), 'explainer' (slides).
    Give either a brief (Claude writes the script) or your own script (used verbatim), or slides_markdown for
    explainers ('# Title', '- bullet', '> narration'). media: optional per-scene image/video paths.
    music: optional background track (ducked under the voice): a library id from list_music, or a local audio path. Videos take minutes; by default this returns
    a job_id immediately — poll job_status.
    plan_file / pronunciations / verified_terms: from prepare_video (recommended: avoids mispronounced audio).
    karaoke: highlight the word being spoken (default on).
    """
    return await _submit(kind, {"brief": brief, "script": script, "lang": lang, "voice": voice, "seconds": seconds,
                                "style": style, "aspect": aspect, "music": music, "fx": {"pitch": pitch},
                                "slides_markdown": slides_markdown, "media": media,
                                "burn_subtitles": burn_subtitles, "plan_file": plan_file,
                                "pronunciations": pronunciations, "verified_terms": verified_terms,
                                "karaoke": karaoke}, wait_seconds)


@mcp.tool()
async def resubtitle_video(job_id: str, karaoke: bool = True, wait_seconds: float = 0) -> dict[str, Any]:
    """Rebuild a finished video's burned-in subtitles (always fit the frame; karaoke word highlight) without
    re-rendering its voice or images. The old render and its exports move to the trash."""
    return await _submit("resubtitle", {"job": job_id, "karaoke": karaoke}, wait_seconds)


@mcp.tool()
async def repronounce_video(job_id: str, readings: dict[str, str], format: str = "mp4",
                            wait_seconds: float = 0) -> dict[str, Any]:
    """Fix mispronounced words in a finished video/TTS job without re-rendering it.

    readings: {term as written in the script: how to read it}, e.g. {"PNJ": "Pi En Di", "pháp lý": "pháp lí"}.
    Every sentence containing a term is re-read in the cloned voice and spliced into a new export (video stream
    copied). Readings are saved to the pronunciation lexicon for later renders. Returns the export path."""
    return await _submit("repronounce", {"job": job_id, "readings": readings, "format": format}, wait_seconds)


@mcp.tool()
async def voiceover_recording(recording: str, script: str, lang: str = "auto", voice: str | None = None,
                              wait_seconds: float = 0) -> dict[str, Any]:
    """Add a cloned-voice narration and burned subtitles to an existing screen recording (local video path)."""
    return await _submit("screen", {"recording": recording, "script": script, "lang": lang, "voice": voice}, wait_seconds)


@mcp.tool()
async def transcribe_subtitles(media: str, lang: str | None = None, burn: bool = False,
                               translate_to: str | None = None, wait_seconds: float = 300) -> dict[str, Any]:
    """Create SRT/VTT/ASS subtitles for a local audio/video file (Whisper large-v3-turbo, word timings).
    burn: also render a subtitled MP4. translate_to: 'en' or 'vi' for a translated SRT."""
    return await _submit("subtitle", {"media": media, "lang": lang, "burn": burn, "translate_to": translate_to},
                         wait_seconds)


@mcp.tool()
async def job_status(job_id: str, wait_seconds: float = 0) -> dict[str, Any]:
    """Get a job's status/result. wait_seconds > 0 blocks until it finishes or the time elapses."""
    if wait_seconds > 0:
        return _summary(await anyio.to_thread.run_sync(runner.wait, job_id, wait_seconds))
    return _summary(db.get_job(job_id))


@mcp.tool()
def list_jobs(limit: int = 10) -> list[dict[str, Any]]:
    """Most recent jobs with status."""
    return [_summary(j) for j in db.list_jobs(limit)]


@mcp.tool()
def list_voices() -> list[dict[str, Any]]:
    """Available cloned voices (id, name, languages with references, default voice settings)."""
    return [{"id": v["id"], "name": v["name"], "languages": sorted(v.get("refs", {})), "fx": v.get("fx"),
             "samples": len(v.get("samples", []))} for v in profiles.list_voices()]


@mcp.tool()
def list_music() -> list[dict[str, Any]]:
    """Background-music library (calm, free tracks). Pass a track's id as create_video(music=...).
    Tracks with an 'attribution' need that credit line in the video description."""
    return [{k: t.get(k) for k in ("id", "title", "artist", "mood", "duration_s", "licence", "attribution")}
            for t in music.list_tracks() if t["available"]]
