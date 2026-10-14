"""FFmpeg composition: scenes (stills with Ken Burns, or clips) + voice + music + subtitles.

Timeline contract: scene i occupies [B_i, B_i + d_i) where B_i = sum(d_j, j<i). Each
rendered segment is d_i + T long (T = transition) so the xfade into scene i+1 starts
exactly at B_{i+1}, keeping pictures in sync with the narration.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mediaforge import audio as A
from mediaforge.config import settings

log = logging.getLogger(__name__)

RESOLUTIONS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
MOTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "zoom_in_tl"]
TRANSITIONS = ["fade", "smoothleft", "fadeblack", "circleopen", "slideup", "dissolve"]


@dataclass
class Scene:
    visual: str               # image or video path
    duration: float           # seconds this scene is on screen
    kind: str = "image"       # image | video
    motion: str | None = None  # Ken Burns style for images; None = auto


def _kenburns_filter(motion: str, w: int, h: int, fps: int, frames: int) -> str:
    big_w, big_h = w * 2, h * 2  # upscale before zoompan to avoid sub-pixel jitter
    n = max(frames - 1, 1)
    z_in, z_out = f"1+0.14*on/{n}", f"1.14-0.14*on/{n}"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    exprs = {
        "zoom_in": (z_in, cx, cy),
        "zoom_out": (z_out, cx, cy),
        "pan_right": ("1.12", f"(iw-iw/zoom)*on/{n}", cy),
        "pan_left": ("1.12", f"(iw-iw/zoom)*(1-on/{n})", cy),
        "pan_up": ("1.12", cx, f"(ih-ih/zoom)*(1-on/{n})"),
        "zoom_in_tl": (z_in, f"(iw-iw/zoom)*0.25", f"(ih-ih/zoom)*0.25"),
        "static": ("1", "0", "0"),
    }
    z, x, y = exprs.get(motion, exprs["zoom_in"])
    return (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},setsar=1,format=yuv420p"
    )


def render_segment(scene: Scene, length: float, out: Path, w: int, h: int, fps: int, motion: str) -> Path:
    frames = max(int(round(length * fps)), 2)
    if scene.kind == "video":
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1,format=yuv420p,"
              f"tpad=stop_mode=clone:stop_duration={length}")
        args = ["-stream_loop", "-1", "-i", scene.visual, "-t", f"{length:.3f}", "-vf", vf]
    else:
        vf = _kenburns_filter(motion, w, h, fps, frames)
        args = ["-loop", "1", "-framerate", str(fps), "-i", scene.visual, "-vf", vf, "-frames:v", str(frames)]
    A.run_ffmpeg([*args, "-an", "-c:v", "h264_videotoolbox", "-b:v", "20M", "-pix_fmt", "yuv420p", str(out)])
    return out


def compose(
    scenes: list[Scene], voice_wav: str | Path, out_path: str | Path, *, aspect: str = "16:9", fps: int = 30,
    transition: float = 0.5, transition_style: str | None = None, music: str | Path | None = None,
    music_volume: float = 0.18, subtitles_ass: str | Path | None = None, soft_srt: str | Path | None = None,
    progress: Callable[[float, str], None] | None = None, bitrate: str | None = None,
) -> Path:
    progress = progress or (lambda p, m: None)
    w, h = RESOLUTIONS.get(aspect, RESOLUTIONS["16:9"])
    out_path = Path(out_path)
    voice_dur = A.probe_duration(voice_wav)
    # Last scene absorbs any rounding so video length == narration length (+ tail).
    total_scenes = sum(s.duration for s in scenes)
    if scenes and total_scenes < voice_dur + 0.5:
        scenes[-1].duration += voice_dur + 0.5 - total_scenes
    T = transition if len(scenes) > 1 else 0.0
    with tempfile.TemporaryDirectory(prefix="mf-compose-") as td:
        tdp = Path(td)
        segs = []
        for i, sc in enumerate(scenes):
            progress(0.05 + 0.6 * i / max(len(scenes), 1), f"rendering scene {i + 1}/{len(scenes)}")
            length = sc.duration + (T if i < len(scenes) - 1 else 0)
            motion = sc.motion or MOTIONS[i % len(MOTIONS)]
            segs.append(render_segment(sc, length, tdp / f"seg{i:03d}.mp4", w, h, fps, motion))
        progress(0.7, "assembling")
        inputs: list[str] = []
        for s in segs:
            inputs += ["-i", str(s)]
        n = len(segs)
        voice_idx = n
        inputs += ["-i", str(voice_wav)]
        fc: list[str] = []
        # chain xfades
        last = "[0:v]"
        boundary = 0.0
        for i in range(1, n):
            boundary += scenes[i - 1].duration
            style = transition_style or TRANSITIONS[(i - 1) % len(TRANSITIONS)]
            label = f"[x{i}]"
            fc.append(f"{last}[{i}:v]xfade=transition={style}:duration={T}:offset={boundary:.3f}{label}")
            last = label
        vlabel = last
        if subtitles_ass:
            ass_local = tdp / "subs.ass"
            shutil.copy(subtitles_ass, ass_local)
            fc.append(f"{vlabel}ass=filename=subs.ass[vout]")
            vlabel = "[vout]"
        # audio
        if music:
            inputs += ["-stream_loop", "-1", "-i", str(music)]
        fc += audio_graph(voice_idx, n + 1 if music else None, music_volume)
        args = [*inputs]
        if soft_srt:
            args += ["-i", str(soft_srt)]
        args += ["-filter_complex", ";".join(fc) if fc else "null", "-map", "0:v" if vlabel == "[0:v]" else vlabel,
                 "-map", "[aout]"]
        if soft_srt:
            args += ["-map", f"{len(segs) + 1 + (1 if music else 0)}:s", "-c:s", "mov_text", "-metadata:s:s:0", "language=und"]
        rate = bitrate or ("14M" if max(w, h) >= 1920 else "8M")
        args += ["-c:v", "h264_videotoolbox", "-b:v", rate, "-profile:v", "high", "-pix_fmt", "yuv420p",
                 "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out_path.resolve())]
        progress(0.75, "encoding")
        proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                              capture_output=True, text=True, cwd=td)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg compose failed: {proc.stderr[-2000:]}")
    progress(1.0, "done")
    return out_path


def audio_graph(voice_idx: int, music_idx: int | None, music_volume: float = 0.18) -> list[str]:
    """Voice (+ ducked music) → [aout]. Shared by compose and export so a remuxed voice track mixes identically."""
    if music_idx is None:
        return [f"[{voice_idx}:a]aresample=48000,apad=pad_dur=0.6[aout]"]
    return [
        f"[{voice_idx}:a]aresample=48000,asplit=2[vo][key]",
        f"[{music_idx}:a]aresample=48000,volume={music_volume}[m]",
        "[m][key]sidechaincompress=threshold=0.04:ratio=8:attack=15:release=350[duck]",
        "[vo][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
    ]


def thumbnail(video: str | Path, out: str | Path, at: float = 1.0) -> Path:
    A.run_ffmpeg(["-ss", f"{at}", "-i", str(video), "-frames:v", "1", "-vf", "scale=480:-2", str(out)])
    return Path(out)
