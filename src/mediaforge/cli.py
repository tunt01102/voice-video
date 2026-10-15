"""`mediaforge` command line. Talks to the running server when it is up, otherwise runs in-process."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

from mediaforge.config import settings

app = typer.Typer(add_completion=False, no_args_is_help=True, help="MediaForge — local voice/subtitle/image/video generation")
voice_app = typer.Typer(no_args_is_help=True, help="Manage cloned voices")
app.add_typer(voice_app, name="voice")
music_app = typer.Typer(no_args_is_help=True, help="Background-music library")
app.add_typer(music_app, name="music")

BASE = f"http://{settings.host}:{settings.port}"


def _server_up() -> bool:
    try:
        return httpx.get(f"{BASE}/api/health", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


def _print(obj: Any) -> None:
    typer.echo(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _run(job_type: str, params: dict[str, Any], local: bool) -> None:
    params = {k: v for k, v in params.items() if v is not None}
    if not local and _server_up():
        r = httpx.post(f"{BASE}/api/jobs", params={"wait": True, "timeout": 3600},
                       json={"type": job_type, "params": params, "source": "cli"}, timeout=3700)
        r.raise_for_status()
        job = r.json()
        if job["status"] != "done":
            _print({k: job.get(k) for k in ("id", "status", "message", "error")})
            raise typer.Exit(1)
        _print(job["result"])
        return
    from mediaforge import pipelines

    def progress(p: float, m: str) -> None:
        typer.echo(f"\r[{p * 100:5.1f}%] {m:<60}", nl=False, err=True)

    result = pipelines.run(job_type, params, progress)
    typer.echo("", err=True)
    _print({k: v for k, v in result.items() if k not in ("words",)})


def _fx(pitch, pace, warmth, clarity) -> dict | None:
    fx = {k: v for k, v in dict(pitch=pitch, pace=pace, warmth=warmth, clarity=clarity).items() if v is not None}
    return fx or None


@app.command()
def serve(host: str = settings.host, port: int = settings.port, log_level: str = "info") -> None:
    """Run the API + MCP + dashboard server (127.0.0.1 by default)."""
    import uvicorn

    from mediaforge.api import create_app

    logging.basicConfig(level=log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    uvicorn.run(create_app(), host=host, port=port, log_level=log_level)


@app.command()
def speak(
    text: str = typer.Argument(..., help="Text, or @file.txt"),
    voice: Optional[str] = None, lang: str = "auto", engine: Optional[str] = None,
    pitch: Optional[float] = typer.Option(None, help="semitones, negative = deeper"),
    pace: Optional[float] = None, warmth: Optional[float] = None, clarity: Optional[float] = None,
    local: bool = typer.Option(False, help="run in-process even if the server is up"),
) -> None:
    """Text → cloned-voice audio (+ subtitles), with pronunciation QA."""
    if text.startswith("@"):
        text = Path(text[1:]).read_text("utf-8")
    _run("tts", {"text": text, "voice": voice, "lang": lang, "engine": engine, "fx": _fx(pitch, pace, warmth, clarity)}, local)


@app.command()
def image(prompt: str, aspect: str = "16:9", count: int = 1, style: str = "none", seed: Optional[int] = None,
          local: bool = False) -> None:
    """Generate images (Z-Image-Turbo on MLX)."""
    _run("image", {"prompt": prompt, "aspect": aspect, "count": count, "style": style, "seed": seed}, local)


def _video(kind: str, brief, script, lang, voice, seconds, style, music, pitch, aspect, local, extra=None) -> None:
    if script and script.startswith("@"):
        script = Path(script[1:]).read_text("utf-8")
    _run(kind, {"brief": brief, "script": script, "lang": lang, "voice": voice, "seconds": seconds, "style": style,
                "music": music, "aspect": aspect, "fx": _fx(pitch, None, None, None), **(extra or {})}, local)


@app.command()
def video(brief: Optional[str] = typer.Argument(None), script: Optional[str] = typer.Option(None, help="own narration or @file"),
          lang: str = "auto", voice: Optional[str] = None, seconds: int = 75, style: str = "cinematic",
          music: Optional[str] = None, pitch: Optional[float] = None, aspect: Optional[str] = None,
          clips: bool = typer.Option(False, help="animate scenes with LTX-2 (slow)"),
          document: Optional[Path] = typer.Option(None, help="Word/PDF as the content; its pictures go on their scenes"),
          document_as: str = typer.Option("brief", help="brief (Claude writes the script) | script (read verbatim)"),
          local: bool = False) -> None:
    """Narrated 16:9 video with AI visuals and subtitles (style chibi = cute cartoon characters)."""
    extra = {"clips": clips, **({"document": str(document.resolve()), "document_as": document_as} if document else {})}
    _video("narrated", brief, script, lang, voice, seconds, style, music, pitch, aspect, local, extra)


@app.command()
def short(brief: Optional[str] = typer.Argument(None), script: Optional[str] = None, lang: str = "auto",
          voice: Optional[str] = None, seconds: int = 40, style: str = "cinematic", music: Optional[str] = None,
          pitch: Optional[float] = None, local: bool = False) -> None:
    """Vertical 9:16 short with karaoke subtitles."""
    _video("short", brief, script, lang, voice, seconds, style, music, pitch, "9:16", local)


@app.command()
def explainer(brief: Optional[str] = typer.Argument(None), slides: Optional[Path] = typer.Option(None, help="markdown slides file"),
              script: Optional[str] = None, lang: str = "auto", voice: Optional[str] = None, seconds: int = 90,
              theme: str = "midnight", music: Optional[str] = None, pitch: Optional[float] = None,
              aspect: str = "16:9", local: bool = False) -> None:
    """Slide explainer (from a brief, a script, or markdown slides)."""
    extra = {"theme": theme, "slides_markdown": slides.read_text("utf-8") if slides else None}
    _video("explainer", brief, script, lang, voice, seconds, "illustration", music, pitch, aspect, local, extra)


@app.command()
def screen(recording: Path, script: str, lang: str = "auto", voice: Optional[str] = None, local: bool = False) -> None:
    """Voice-over + subtitles on top of a screen recording."""
    if script.startswith("@"):
        script = Path(script[1:]).read_text("utf-8")
    _run("screen", {"recording": str(recording.resolve()), "script": script, "lang": lang, "voice": voice}, local)


@app.command()
def resubtitle(job: str, karaoke: bool = typer.Option(True, help="highlight the spoken word"), local: bool = False) -> None:
    """Rebuild a finished video's subtitles (fit the frame, karaoke) without re-rendering voice or images."""
    _run("resubtitle", {"job": job, "karaoke": karaoke}, local)


@app.command()
def repronounce(job: str, readings: list[str] = typer.Argument(..., help='TERM=READING, e.g. PNJ="Pi En Di"'),
                format: str = "mp4", local: bool = False) -> None:
    """Change how words are read in a finished job (re-reads only those sentences; no video re-render)."""
    pairs = dict(r.split("=", 1) for r in readings if "=" in r)
    if not pairs:
        raise typer.BadParameter('use TERM=READING, e.g. PNJ="Pi En Di"')
    _run("repronounce", {"job": job, "readings": pairs, "format": format}, local)


def _text_arg(text: Optional[str]) -> Optional[str]:
    """Poem/lyrics given inline or as @file."""
    if text and text.startswith("@"):
        return Path(text[1:]).read_text("utf-8")
    return text


@app.command()
def recite(text: Optional[str] = typer.Argument(None, help="the poem (one line per row) or @file"),
           poem: Optional[str] = typer.Option(None, help="built-in poem id (see `mediaforge poems`)"),
           version: Optional[str] = typer.Option(None, help="phien-am | dich:<translator>"),
           voice: Optional[str] = None, mood: str = "auto", spirit: Optional[str] = None,
           title: Optional[str] = None, author: Optional[str] = None, tempo: float = 1.0,
           accompaniment: str = "auto", video: bool = False, aspect: str = "16:9", local: bool = False) -> None:
    """Ngâm thơ: chant a poem in your cloned voice with đàn tranh / sáo (optionally as an illustrated video)."""
    _run("recite", {"text": _text_arg(text), "poem_id": poem, "version": version, "voice": voice, "mood": mood,
                    "spirit": spirit, "title": title, "author": author, "tempo": tempo,
                    "accompaniment": accompaniment, "video": video, "aspect": aspect}, local)


@app.command()
def sing(text: str = typer.Argument(..., help="lyrics (one line per row) or @file"), voice: Optional[str] = None,
         mood: str = "auto", bpm: Optional[float] = None, spirit: Optional[str] = None, title: Optional[str] = None,
         author: Optional[str] = None, accompaniment: str = "auto", video: bool = False, aspect: str = "16:9",
         local: bool = False) -> None:
    """Hát: sing lyrics in your cloned voice on a tone-aware melody with a beat-locked band."""
    _run("sing", {"text": _text_arg(text), "voice": voice, "mood": mood, "bpm": bpm, "spirit": spirit,
                  "title": title, "author": author, "accompaniment": accompaniment, "video": video,
                  "aspect": aspect}, local)


@app.command()
def poems() -> None:
    """Built-in Tang poems for `recite --poem`."""
    from mediaforge.sing import poems as P

    for p in P.summary():
        typer.echo(f"{p['id']:<28} {p['title']} — {p['author']}  [{', '.join(p['versions'])}]")


@app.command()
def subtitle(media: Path, lang: Optional[str] = None, burn: bool = False, translate_to: Optional[str] = None,
             local: bool = False) -> None:
    """Transcribe audio/video to SRT/VTT/ASS (optionally burn in / translate)."""
    _run("subtitle", {"media": str(media.resolve()), "lang": lang, "burn": burn, "translate_to": translate_to}, local)


# ------------------------------------------------------------------ voices

@voice_app.command("list")
def voice_list() -> None:
    from mediaforge.voice import profiles

    d = profiles.default_voice()
    for v in profiles.list_voices():
        if v.get("kind") == "blend":
            typer.echo(f"  {v['id']:<16} {v['name']:<28} blend {v['description']} (w={v['blend']['weight']})")
            continue
        consent = (v.get("consent") or {}).get("kind", "-")
        typer.echo(f"{'*' if v['id'] == d else ' '} {v['id']:<16} {v['name']:<28} samples={len(v['samples'])} "
                   f"consent={consent} refs={v.get('refs')}")


@voice_app.command("create")
def voice_create(name: str, id: Optional[str] = None, default: bool = True) -> None:
    from mediaforge.voice import profiles

    v = profiles.create_voice(name, id)
    if default:
        profiles.set_default_voice(v["id"])
    typer.echo(f"created voice {v['id']}. Add recordings: mediaforge voice add {v['id']} sample.wav --lang vi")


@voice_app.command("add")
def voice_add(voice_id: str, files: list[Path], lang: Optional[str] = None, text: Optional[str] = None,
              no_denoise: bool = False) -> None:
    """Add recordings (any format ffmpeg reads) to a voice."""
    from mediaforge.voice import profiles

    for f in files:
        s = profiles.add_sample(voice_id, f, lang=lang, text=text, denoise=not no_denoise)
        typer.echo(f"+ {f.name}: lang={s['lang']} {s['duration']}s snr={s['snr_db']}dB text='{s['text'][:70]}'")
    typer.echo(f"references: {profiles.get_voice(voice_id)['refs']}")


@voice_app.command("record")
def voice_record(voice_id: str, lang: str = "vi", seconds: int = 10, count: int = 3) -> None:
    """Record samples from the default microphone, reading the suggested prompts."""
    from mediaforge.voice import profiles

    tmp = settings.sub("tmp")
    for i, prompt in enumerate(profiles.ENROL_PROMPTS[lang][:count]):
        typer.echo(f"\n[{i + 1}/{count}] Read aloud after Enter ({seconds}s):\n  “{prompt}”")
        input()
        out = tmp / f"rec_{voice_id}_{lang}_{i}.wav"
        subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "avfoundation", "-i", ":0",
                        "-t", str(seconds), "-ac", "1", "-ar", "48000", str(out)], check=True)
        s = profiles.add_sample(voice_id, out, lang=lang, text=prompt)
        typer.echo(f"  saved: {s['duration']}s snr={s['snr_db']}dB heard='{s['asr_text'][:80]}'")


@voice_app.command("demo")
def voice_demo() -> None:
    """Create a placeholder voice from macOS `say` to test the pipeline."""
    from mediaforge.voice import profiles

    v = profiles.create_demo_voice()
    typer.echo(f"demo voice ready: refs={v['refs']}")


@voice_app.command("default")
def voice_default(voice_id: str) -> None:
    from mediaforge.voice import profiles

    profiles.set_default_voice(voice_id)
    typer.echo(f"default voice: {voice_id}")


@voice_app.command("set")
def voice_set(voice_id: str, pitch: Optional[float] = None, pace: Optional[float] = None,
              warmth: Optional[float] = None, clarity: Optional[float] = None,
              vi_engine: Optional[str] = None, en_engine: Optional[str] = None) -> None:
    """Set a voice's default pitch/pace/warmth/clarity and engines."""
    from mediaforge.voice import profiles

    engines = {k: v for k, v in {"vi": vi_engine, "en": en_engine}.items() if v}
    v = profiles.update_voice(voice_id, {"fx": _fx(pitch, pace, warmth, clarity) or {}, "engines": engines})
    _print({"fx": v["fx"], "engines": v["engines"]})


@voice_app.command("consent")
def voice_consent(voice_id: str, kind: str = typer.Argument(..., help="self | written | synthetic | none"),
                  by: Optional[str] = None, note: Optional[str] = None) -> None:
    """Record whether a voice may be a blend partner (pha giọng). 'written' needs --by (who agreed)."""
    from mediaforge.voice import profiles

    v = profiles.update_voice(voice_id, {"consent": {"kind": kind, "by": by, "note": note}})
    _print({"id": v["id"], "consent": v.get("consent")})


@voice_app.command("blend")
def voice_blend(owner: str, partner: str, amount: float = typer.Option(0.25, help="partner share 0..0.5 (0.2-0.3 recommended)"),
                name: Optional[str] = None, local: bool = False) -> None:
    """Pha giọng: a new voice that stays mostly OWNER with AMOUNT of PARTNER (partner needs a consent record)."""
    _run("blend", {"owner": owner, "partner": partner, "amount": amount, "name": name}, local)


# ------------------------------------------------------------------ ops

@music_app.command("list")
def music_list() -> None:
    """Library tracks: id, mood, licence, downloaded or not."""
    from mediaforge import music

    for t in music.list_tracks():
        typer.echo(f"{'✓' if t['available'] else '·'} {t['id']:<32} {t['mood']:<10} {t['licence']:<14} {t['title']} — {t['artist']}")


@music_app.command("sync")
def music_sync() -> None:
    """Download any library tracks that are missing (into data/music)."""
    from mediaforge import music

    r = music.sync(typer.echo)
    typer.echo(f"{len(r['downloaded'])} downloaded, {len(r['present'])} already present, {len(r['failed'])} failed")
    if r["failed"]:
        raise typer.Exit(1)


@app.command("mcp-register")
def mcp_register() -> None:
    """Register the MCP server for all Claude Code sessions (user scope)."""
    cmd = ["claude", "mcp", "add", "-s", "user", "--transport", "http", "mediaforge", f"{BASE}/mcp"]
    typer.echo(" ".join(cmd))
    subprocess.run(cmd, check=False)


@app.command()
def doctor() -> None:
    """Check the environment."""
    checks = []
    ff = subprocess.run([settings.ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    checks.append(("ffmpeg (libass)", settings.ffmpeg, " ass " in ff))
    checks.append(("claude CLI", shutil.which("claude") or "-", bool(shutil.which("claude"))))
    try:
        ok = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2).status_code == 200
    except httpx.HTTPError:
        ok = False
    checks.append(("ollama", settings.ollama_url, ok))
    checks.append(("server", BASE, _server_up()))
    import platform

    checks.append(("platform", f"{platform.machine()} py{sys.version.split()[0]}", platform.machine() == "arm64"))
    for name, detail, ok in checks:
        typer.echo(f"{'✔' if ok else '✘'} {name:<16} {detail}")


if __name__ == "__main__":
    app()
