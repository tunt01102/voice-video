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
        "Local media generation on this Mac: cloned-voice speech (English/Vietnamese), singing and poetry "
        "chanting (ngâm thơ), subtitles, images and videos. Long jobs return a job_id; poll job_status or pass "
        "wait_seconds. Output paths are absolute local files. Use list_voices to find the owner's voice id (prefer it over 'demo')."
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


def _fx(**kw: float | None) -> dict[str, float] | None:
    """Only the settings given: the rest come from the voice profile's saved fx."""
    return {k: v for k, v in kw.items() if v is not None} or None


@mcp.tool()
async def speak(text: str, voice: str | None = None, lang: str = "auto", pitch: float | None = None,
                pace: float | None = None, warmth: float | None = None, clarity: float | None = None,
                engine: str | None = None,
                wait_seconds: float = 300) -> dict[str, Any]:
    """Synthesise speech in a cloned voice (English 'en', Vietnamese 'vi', or 'auto' per sentence).

    pitch: semitones -6..+6 (negative = deeper). pace: 0.7..1.4. warmth/clarity: 0..1. Leave them unset to use
    the voice's saved settings (the owner's last setup in the dashboard).
    engine: None (voice default), 'vieneu', 'voxcpm' or 'best'. Every sentence is checked by speech
    recognition and automatically regenerated/respelled if mispronounced. Returns wav/mp3 + SRT/VTT/ASS paths.
    """
    return await _submit("tts", {"text": text, "voice": voice, "lang": lang, "engine": engine,
                                 "fx": _fx(pitch=pitch, pace=pace, warmth=warmth, clarity=clarity)}, wait_seconds)


@mcp.tool()
async def blend_voices(owner: str, partner: str, amount: float = 0.25, name: str | None = None,
                       wait_seconds: float = 180) -> dict[str, Any]:
    """Pha giọng: create a voice that stays mostly `owner` with `amount` (0..0.5, recommended 0.2-0.3) of `partner`.

    The partner voice must carry a consent record (its owner's own voice, a synthetic voice, or written permission)
    set on the dashboard's Voices tab; otherwise this is refused. Calibration renders a few probes (~1 min).
    The result's `voice` id works as `voice` in speak / create_video / recite_poem / sing_song.
    """
    from mediaforge.voice import blend

    try:
        blend.check_partner(profiles.get_voice(partner))
        blend._amount(amount)
    except (KeyError, ValueError, PermissionError) as e:
        return {"error": str(e)}
    return await _submit("blend", {"owner": owner, "partner": partner, "amount": amount, "name": name}, wait_seconds)


@mcp.tool()
async def generate_image(prompt: str, aspect: str = "16:9", count: int = 1, style: str = "none",
                         seed: int | None = None, wait_seconds: float = 240) -> dict[str, Any]:
    """Generate image(s) locally (Z-Image-Turbo). aspect: 16:9, 9:16, 1:1, 4:3, 3:4.
    style: none, cinematic, photo, illustration, 3d, minimal, tech, chibi (cute small cartoon characters).
    Prompt in English works best."""
    return await _submit("image", {"prompt": prompt, "aspect": aspect, "count": count, "style": style, "seed": seed},
                         wait_seconds)


@mcp.tool()
async def prepare_video(brief: str | None = None, script: str | None = None, kind: str = "narrated",
                        lang: str = "auto", voice: str | None = None, seconds: int = 60, style: str = "cinematic",
                        slides_markdown: str | None = None, document: str | None = None,
                        document_as: str = "brief", wait_seconds: float = 300) -> dict[str, Any]:
    """Step 1 of a video: write the script and scan it for terms the TTS may misread (acronyms, foreign names,
    codes, symbols) BEFORE rendering.

    Returns plan_file, the scene narrations and `candidates` [{term, say, known, reason_labels, context}].
    Decide a reading for each unknown term (ask the user when unsure, e.g. PNJ -> 'Pi En Di'), then call
    create_video(plan_file=..., pronunciations={term: reading}, verified_terms=[terms that read fine as written]).
    The answers are saved to the pronunciation lexicon for every later render.
    document / document_as / style: as in create_video.
    """
    return await _submit("prepare", {"brief": brief, "script": script, "kind": kind, "lang": lang, "voice": voice,
                                     "seconds": seconds, "style": style, "slides_markdown": slides_markdown,
                                     "document": document, "document_as": document_as},
                         wait_seconds)


@mcp.tool()
async def create_video(brief: str | None = None, script: str | None = None, kind: str = "narrated",
                       lang: str = "auto", voice: str | None = None, seconds: int = 60, style: str = "cinematic",
                       aspect: str | None = None, music: str | None = None, pitch: float | None = None,
                       slides_markdown: str | None = None, media: list[str] | None = None,
                       burn_subtitles: bool = True, plan_file: str | None = None,
                       pronunciations: dict[str, str] | None = None, verified_terms: list[str] | None = None,
                       karaoke: bool = True, document: str | None = None, document_as: str = "brief",
                       wait_seconds: float = 0) -> dict[str, Any]:
    """Create a narrated video with cloned voice, generated visuals and subtitles.

    kind: 'narrated' (16:9 AI visuals), 'short' (9:16 with karaoke subtitles), 'explainer' (slides).
    Give either a brief (Claude writes the script) or your own script (used verbatim), or slides_markdown for
    explainers ('# Title', '- bullet', '> narration'). media: optional per-scene image/video paths.
    music: optional background track (ducked under the voice): a library id from list_music, or a local audio path. Videos take minutes; by default this returns
    a job_id immediately — poll job_status.
    plan_file / pronunciations / verified_terms: from prepare_video (recommended: avoids mispronounced audio).
    karaoke: highlight the word being spoken (default on).
    style: cinematic, photo, illustration, 3d, minimal, tech, chibi (scenes acted out by cute chibi characters).
    document: local .docx/.pdf (.doc/.rtf/.odt) path used as the content instead of brief/script; its pictures are
    shown on the scenes where they appear. document_as: 'brief' (Claude writes the script from it) or 'script'
    (read verbatim). pitch: omit to use the voice's saved settings.
    """
    return await _submit(kind, {"brief": brief, "script": script, "lang": lang, "voice": voice, "seconds": seconds,
                                "style": style, "aspect": aspect, "music": music, "fx": _fx(pitch=pitch),
                                "slides_markdown": slides_markdown, "media": media,
                                "burn_subtitles": burn_subtitles, "plan_file": plan_file,
                                "pronunciations": pronunciations, "verified_terms": verified_terms,
                                "karaoke": karaoke, "document": document, "document_as": document_as},
                         wait_seconds)


@mcp.tool()
async def recite_poem(text: str | None = None, poem_id: str | None = None, version: str | None = None,
                      voice: str | None = None, mood: str = "auto", spirit: str | None = None,
                      title: str | None = None, author: str | None = None, tempo: float = 1.0,
                      accompaniment: str = "auto", video: bool = False, aspect: str = "16:9",
                      wait_seconds: float = 0) -> dict[str, Any]:
    """Ngâm thơ: chant a Vietnamese poem in the owner's cloned voice, with đàn tranh / sáo accompaniment.

    text: the poem, one line per row (or poem_id from list_poems; version 'phien-am' or 'dich:<translator>').
    mood: auto (Claude reads the poem) | hung-trang (proud, heroic) | tram-mac (calm) | u-hoai (longing) | bi-ai.
    spirit: the feeling the author wants affirmed (guides mood and images). accompaniment: auto (traditional
    instruments in the voice's key) | ace+traditional (adds an ACE-Step AI orchestra bed) | ace | none | a list_music id. video=True adds an illustrated film ("hoạt cảnh")
    with karaoke lyrics. Returns a job id; poll job_status.
    """
    return await _submit("recite", {"text": text, "poem_id": poem_id, "version": version, "voice": voice,
                                    "mood": mood, "spirit": spirit, "title": title, "author": author, "tempo": tempo,
                                    "accompaniment": accompaniment, "video": video, "aspect": aspect}, wait_seconds)


@mcp.tool()
async def sing_song(text: str, voice: str | None = None, mood: str = "auto", bpm: float | None = None,
                    spirit: str | None = None, title: str | None = None, author: str | None = None,
                    accompaniment: str = "auto", video: bool = False, aspect: str = "16:9",
                    wait_seconds: float = 0) -> dict[str, Any]:
    """Hát: sing Vietnamese lyrics in the owner's cloned voice on a tone-aware melody (lyrics stay intelligible),
    with a beat-locked band (đàn tranh, sáo, mõ, trống) in the voice's key. One lyric line per row.
    mood/spirit/accompaniment/video as in recite_poem; bpm defaults to 76 (proud) or 66."""
    return await _submit("sing", {"text": text, "voice": voice, "mood": mood, "bpm": bpm, "spirit": spirit,
                                  "title": title, "author": author, "accompaniment": accompaniment,
                                  "video": video, "aspect": aspect}, wait_seconds)


@mcp.tool()
def list_poems() -> list[dict[str, Any]]:
    """Built-in Tang poems (thơ Đường) for recite_poem: id, title, author, Hán-Việt lines, verse translations."""
    from mediaforge.sing import poems

    return [{k: p[k] for k in ("id", "title", "author", "form", "mood", "versions", "phien_am")} for p in poems.summary()]


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
    """Available cloned voices (id, name, languages with references, default voice settings). Blend voices
    (pha giọng, kind='blend') show their parts and share; consent shows whether a voice may be a blend partner."""
    out = []
    for v in profiles.list_voices():
        item = {"id": v["id"], "name": v["name"], "kind": v.get("kind", "voice"), "fx": v.get("fx")}
        if v.get("kind") == "blend":
            item.update(parts=v["parts"], amount=v["blend"]["amount"], languages=["en", "vi"])
        else:
            item.update(languages=sorted(v.get("refs", {})), samples=len(v.get("samples", [])),
                        consent=(v.get("consent") or {}).get("kind"))
        out.append(item)
    return out


@mcp.tool()
def list_music() -> list[dict[str, Any]]:
    """Background-music library (calm, free tracks). Pass a track's id as create_video(music=...).
    Tracks with an 'attribution' need that credit line in the video description."""
    return [{k: t.get(k) for k in ("id", "title", "artist", "mood", "duration_s", "licence", "attribution")}
            for t in music.list_tracks() if t["available"]]
