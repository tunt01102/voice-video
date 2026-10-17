"""Thuyết trình kèm ghi hình (Canva-style "present and record"), docs/SDD-present-record.md.

The owner's slides (PDF, pictures or slide markdown) + their own recording of the talk (camera + voice, or voice only)
+ optionally the script per slide → a video: slides full-frame, the camera in a round bubble, the owner's REAL voice
cleaned (denoise) and shaped (pitch / warmth / clarity — never pace: the camera must stay lip-synced), subtitles from
the script, ducked background music. Slide changes come from where each slide's script starts in the recording
(Whisper word timings aligned to the script), from `slide_times`, or are spread evenly.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from mediaforge import asr, music, subtitles
from mediaforge import audio as A
from mediaforge.config import settings
from mediaforge.text.normalize import detect_lang
from mediaforge.video import compose, slides as SL
from mediaforge.voice.fx import VoiceFX, finalise, shape

Progress = Callable[[float, str], None]

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
LAYOUTS = ("bubble", "slides", "recording")  # camera bubble on slides | slides only | the recording full-frame
CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
# A line that only separates slides: `---`, `[slide 3]`, `[3]`, `Slide 3:`, `Trang 3`.
SPLIT_RE = re.compile(r"^\s*(?:-{3,}|\[\s*(?:slide|trang)?\s*\d+\s*\]|(?:slide|trang)\s*\d+\s*[:.\-–]?)\s*$", re.I)
LEAD = 0.3        # s: a slide appears this long before its first word
MIN_SLIDE = 0.8   # s: shortest time a slide stays on screen
PAUSE = 0.35      # s: a gap between heard words this long is a pause the slide can change in
FADE = 0.3        # s: cross-fade between slides


def split_script(script: str) -> list[str]:
    """Script → one text per slide. Explicit separators (SPLIT_RE lines) win; otherwise blank-line paragraphs."""
    lines = (script or "").splitlines()
    if any(SPLIT_RE.match(ln) for ln in lines):
        parts, cur = [], []
        for ln in lines:
            if SPLIT_RE.match(ln):
                parts.append(" ".join(cur))
                cur = []
            else:
                cur.append(ln.strip())
        parts.append(" ".join(cur))
        if parts and not parts[0].strip():  # a script that starts with its first separator
            parts = parts[1:]
    else:
        parts = re.split(r"\n\s*\n", script or "")
    return [" ".join(p.split()) for p in parts if p.strip()]


def slide_starts(parts: list[str], words: list[dict[str, Any]], total: float,
                 heard: list[dict[str, Any]] | None = None) -> tuple[list[float], list[bool]]:
    """Start time of each slide (LEAD before its script part starts) and whether that time is a guess.

    `words` come from asr.align_script over " ".join(parts) (script word k = words[k]; it splits on whitespace).
    With `heard` (Whisper's own words), a part is anchored on its first script word Whisper heard exactly (in a run
    of at least two matching words). When the
    owner skipped or changed the part's opening words, align_script spreads them over whatever was said instead
    (e.g. an ad-lib at the end of the previous slide), so the slide would come too early: the start is then the
    last pause before the first heard word of the part, and the slide is flagged for the owner to check."""
    import difflib

    matched: dict[int, int] = {}
    if heard:
        a = [asr._key(t) for t in " ".join(parts).split()]
        b = [asr._key(w["text"]) for w in heard]
        for op, i1, i2, j1, _ in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
            if op == "equal" and i2 - i1 >= 2:  # one shared word ("đầu" in "bắt đầu" ~ "đầu tiên") is no anchor
                matched.update({i1 + d: j1 + d for d in range(i2 - i1)})
    starts, guessed, k, prev_h = [], [], 0, -1
    for part in parts:
        n = len(part.split())
        anchor = next((j for j in range(k, k + n) if j in matched), None)
        if not heard:
            t, guess = (float(words[k]["start"]) if k < len(words) else total), False
        elif anchor == k:
            t, guess = float(heard[matched[k]]["start"]), False
        elif anchor is not None:
            h = matched[anchor]
            t, guess = float(heard[h]["start"]), True
            for i in range(h, prev_h + 1, -1):  # the owner resumed speaking after the last pause before it
                if i > 0 and float(heard[i]["start"]) - float(heard[i - 1]["end"]) >= PAUSE:
                    t = float(heard[i]["start"])
                    break
        else:
            t, guess = (float(words[k]["start"]) if k < len(words) else total), True
        if anchor is not None:
            prev_h = max(matched[j] for j in range(k, k + n) if j in matched)
        starts.append(max(t - LEAD, 0.0))
        guessed.append(guess and len(starts) > 1)
        k += n
    return clean_starts(starts, total), guessed


def clean_starts(starts: list[float], total: float) -> list[float]:
    """First slide at 0, non-decreasing, each slide on screen at least MIN_SLIDE (when the recording allows)."""
    if not starts:
        return []
    n = len(starts)
    out = [0.0]
    for i, s in enumerate(starts[1:], 1):
        latest = total - MIN_SLIDE * (n - i)  # leave room for the slides after this one
        out.append(min(max(float(s), out[-1] + MIN_SLIDE), max(latest, out[-1])))
    return out


def even_starts(n: int, total: float) -> list[float]:
    return [total * i / n for i in range(n)] if n else []


# ------------------------------------------------------------------ slides → frames

def fit_frame(src: str | Path, out: Path, w: int, h: int, bg: tuple[int, int, int] = (0, 0, 0)) -> Path:
    """A slide picture letterboxed onto the video frame (a 4:3 deck is not cropped)."""
    im = ImageOps.exif_transpose(Image.open(src))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        flat = Image.new("RGB", im.size, (255, 255, 255))
        flat.paste(im, mask=im.split()[3])
        im = flat
    im = ImageOps.contain(im.convert("RGB"), (w, h), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def pdf_pages(pdf: str | Path, out_dir: Path, w: int, h: int) -> list[Path]:
    import pypdfium2 as pdfium  # BSD-3 / Apache-2.0 (PyMuPDF is AGPL — rejected)

    doc = pdfium.PdfDocument(str(pdf))
    pages = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            pw, ph = page.get_size()
            scale = max(w / pw, h / ph)  # render at least at frame size, fit_frame scales down
            raw = out_dir / f"page_{i + 1:02d}.raw.png"
            page.render(scale=scale).to_pil().save(raw)
            pages.append(fit_frame(raw, out_dir / f"slide_{i + 1:02d}.png", w, h))
            raw.unlink(missing_ok=True)
    finally:
        doc.close()
    return pages


def load_slides(p: dict[str, Any], out_dir: Path, w: int, h: int) -> tuple[list[Path], list[str]]:
    """The deck as frame-sized PNGs, plus the per-slide narration of a slide-markdown deck ([] otherwise)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if p.get("slides_markdown"):
        md = SL.parse_markdown_slides(p["slides_markdown"])
        if not md:
            raise ValueError("slide markdown has no slides (each slide starts with `# Title`)")
        frames = [SL.render_slide(out_dir / f"slide_{i + 1:02d}.png", s["title"], s["bullets"], width=w, height=h,
                                  theme=p.get("theme", "midnight")) for i, s in enumerate(md)]
        # Only narration the owner wrote (`> …`) is a script; parse_markdown_slides invents one from the bullets.
        explicit = any(ln.strip().startswith(">") for ln in p["slides_markdown"].splitlines())
        return [Path(f) for f in frames], [s["narration"] for s in md] if explicit else []
    src = p.get("slides")
    if not src:
        raise ValueError("provide slides (a PDF, pictures or a folder of pictures) or slides_markdown")
    items = [src] if isinstance(src, (str, Path)) else list(src)
    files: list[Path] = []
    for it in items:
        f = Path(it).expanduser()
        if f.is_dir():
            files += sorted((x for x in f.iterdir() if x.suffix.lower() in IMAGE_EXT + (".pdf",)),
                            key=lambda x: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", x.name)])
        elif f.is_file():
            files.append(f)
        else:
            raise FileNotFoundError(f"slides: {f} not found")
    frames: list[Path] = []
    for f in files:
        ext = f.suffix.lower()
        if ext == ".pdf":
            sub = out_dir / f"pdf_{len(frames):02d}"
            sub.mkdir(exist_ok=True)
            frames += pdf_pages(f, sub, w, h)
        elif ext in IMAGE_EXT:
            frames.append(fit_frame(f, out_dir / f"slide_{len(frames) + 1:02d}.png", w, h))
        elif ext in (".pptx", ".ppt", ".key", ".odp"):
            raise ValueError(f"{f.name}: export the deck to PDF first (PowerPoint/Keynote/Canva → Save as PDF)")
        else:
            raise ValueError(f"{f.name}: slides must be .pdf, .png, .jpg or .webp")
    if not frames:
        raise ValueError("the slides contain no pages")
    return frames, []


# ------------------------------------------------------------------ the recording

def probe(path: str | Path) -> dict[str, Any]:
    out = subprocess.run([settings.ffprobe, "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,start_time,duration:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    info = json.loads(out or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video" and s.get("width")), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    has_audio = a is not None
    # A cover-art picture in an .m4a/.mp3 is a "video" stream too; a real camera has a duration-long stream.
    is_video = bool(v) and Path(path).suffix.lower() in VIDEO_EXT
    # Where the sound starts relative to the picture: a phone/camera file often starts its audio 0.05–0.2 s late,
    # and decoding the audio alone drops that gap — the voice would run ahead of the lips.
    offset = _num((a or {}).get("start_time")) - _num((v or {}).get("start_time")) if is_video and a else 0.0
    # A browser (MediaRecorder) webm often has no container duration: fall back to the longest stream.
    dur = _num((info.get("format") or {}).get("duration")) or max(
        [_num(s.get("duration")) + _num(s.get("start_time")) for s in info.get("streams", [])] or [0.0])
    return {"video": is_video, "audio": has_audio, "width": (v or {}).get("width"), "height": (v or {}).get("height"),
            "duration": dur, "audio_offset": offset}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):  # "N/A" or missing
        return 0.0


def present_fx(fx: dict[str, Any] | None) -> VoiceFX:
    """The owner's sliders for a REAL recording: pace is always 1 (lip-sync); with no fx given, gentle cleanup only
    (the voice-profile fx were tuned for TTS and are not inherited)."""
    v = VoiceFX.from_dict({"clarity": 0.35, **(fx or {})})
    v.pace = 1.0
    return v


def process_voice(src: Path, out: Path, *, fx: VoiceFX, denoise: bool, seconds: float, offset: float = 0.0) -> Path:
    """The recording's voice: denoised, shaped, loudness-normalised — and exactly `seconds` long (the recording's
    length: its video stream often runs a little past its audio, and the mix must not stop before the picture).
    offset: seconds the audio starts after the picture (negative = before); applied so the voice stays on the lips."""
    raw = A.to_wav(src, out.with_name("voice_raw.wav"))
    x = A.load(raw)
    k = int(round(abs(offset) * A.SR))
    if offset > 0:
        x = np.concatenate([np.zeros(k, dtype=np.float32), x])
    elif k:
        x = x[k:]
    n = int(round(seconds * A.SR))
    x = fit_length(x, n)
    if denoise and n:
        import noisereduce as nr

        x = nr.reduce_noise(y=x, sr=A.SR, stationary=False, prop_decrease=0.75).astype(np.float32)
    x = shape(x, fx) if n else x
    finalise(fit_length(x, n), fx, out)
    A.save(out, fit_length(A.load(out), n))  # loudnorm resamples internally: keep the sample count exact
    raw.unlink(missing_ok=True)
    return out


def fit_length(x: np.ndarray, n: int) -> np.ndarray:
    return x[:n] if len(x) >= n else np.concatenate([x, np.zeros(n - len(x), dtype=np.float32)])


# ------------------------------------------------------------------ composition

def bubble_masks(size: int, ring: int, tmp: Path) -> tuple[Path, Path]:
    """Anti-aliased round mask for the camera, and a white ring drawn around it (drawn at 4× then scaled down)."""
    k = 4
    m = Image.new("L", (size * k, size * k), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size * k - 1, size * k - 1), fill=255)
    mask = tmp / "mask.png"
    m.resize((size, size), Image.LANCZOS).save(mask)
    full = size + 2 * ring
    r = Image.new("RGBA", (full * k, full * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(r)
    d.ellipse((0, 0, full * k - 1, full * k - 1), fill=(255, 255, 255, 235))
    d.ellipse((ring * k, ring * k, (ring + size) * k - 1, (ring + size) * k - 1), fill=(0, 0, 0, 0))
    ring_png = tmp / "ring.png"
    r.resize((full, full), Image.LANCZOS).save(ring_png)
    return mask, ring_png


def bubble_box(w: int, h: int, *, corner: str, size_frac: float, subtitles_on: bool) -> tuple[int, int, int]:
    """(x, y, size) of the camera bubble; a bottom corner sits above the burned subtitle band."""
    size = int(min(w, h) * min(max(size_frac, 0.12), 0.45)) // 2 * 2
    margin = int(min(w, h) * 0.035)
    st = subtitles.STYLES[subtitles.style_name(w, h)]
    ref_h = 1920 if h > w else 1080
    band = int((st["margin_v"] + 2.4 * st["size"]) * h / ref_h) if subtitles_on else margin
    x = margin if corner.endswith("left") else w - size - margin
    y = margin if corner.startswith("top") else h - size - max(band, margin)
    return x, max(y, margin), size


def render(frames: list[Path], starts: list[float], total: float, out_path: Path, *, recording: Path,
           voice_wav: Path, layout: str, aspect: str, corner: str, size_frac: float, ass: str | None,
           music_file: str | None, music_volume: float, fps: int = 30,
           progress: Progress | None = None) -> Path:
    progress = progress or (lambda p, m: None)
    w, h = compose.RESOLUTIONS.get(aspect, compose.RESOLUTIONS["16:9"])
    with tempfile.TemporaryDirectory(prefix="mf-present-") as td:
        tdp = Path(td)
        inputs: list[str] = []
        fc: list[str] = []
        if layout == "recording":
            inputs += ["-i", str(recording)]
            fc.append(f"[0:v]setpts=PTS-STARTPTS,scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,format=yuv420p[base]")
            nxt = 1
        else:
            n = len(frames)
            T = FADE if n > 1 else 0.0
            ends = starts[1:] + [total]
            for i, f in enumerate(frames):
                seg = ends[i] - starts[i] + (T if i < n - 1 else 0.0)
                inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{max(seg, 2 / fps):.3f}", "-i", str(f)]
                fc.append(f"[{i}:v]scale={w}:{h},fps={fps},setsar=1,format=yuv420p[s{i}]")
            last = "[s0]"
            for i in range(1, n):
                # xfade into slide i starts at its start time (compose.py's timeline contract)
                fc.append(f"{last}[s{i}]xfade=transition=fade:duration={T}:offset={starts[i]:.3f}[x{i}]")
                last = f"[x{i}]"
            nxt = n
            if layout != "bubble":
                fc.append(f"{last}null[base]")
            else:
                x, y, size = bubble_box(w, h, corner=corner, size_frac=size_frac, subtitles_on=bool(ass))
                ring = max(size // 60, 3)
                mask, ring_png = bubble_masks(size, ring, tdp)
                cam, mk, rg = nxt, nxt + 1, nxt + 2
                inputs += ["-i", str(recording), "-loop", "1", "-i", str(mask), "-loop", "1", "-i", str(ring_png)]
                nxt += 3
                fc += [
                    f"[{cam}:v]setpts=PTS-STARTPTS,fps={fps},crop='min(iw,ih)':'min(iw,ih)',scale={size}:{size},"
                    f"setsar=1,format=yuva420p[cam]",
                    f"[{mk}:v]scale={size}:{size},format=gray[mask]",
                    "[cam][mask]alphamerge[camr]",
                    # the ring is looped forever: the slides decide the length (shortest=1)
                    f"{last}[{rg}:v]overlay={x - ring}:{y - ring}:shortest=1[b1]",
                    # a camera that stops early freezes on its last frame
                    f"[b1][camr]overlay={x}:{y}:eof_action=repeat[base]",
                ]
        vlabel = "[base]"
        if ass:
            shutil.copy(ass, tdp / "subs.ass")
            fc.append(f"{vlabel}ass=filename=subs.ass[vout]")
            vlabel = "[vout]"
        voice_idx = nxt
        inputs += ["-i", str(voice_wav)]
        music_idx = None
        if music_file:
            music_idx = voice_idx + 1
            inputs += ["-stream_loop", "-1", "-i", str(music_file)]
        fc += compose.audio_graph(voice_idx, music_idx, music_volume, total=total)
        args = [*inputs, "-filter_complex", ";".join(fc), "-map", vlabel, "-map", "[aout]", "-t", f"{total:.3f}",
                "-c:v", "h264_videotoolbox", "-b:v", "14M" if max(w, h) >= 1920 else "8M", "-profile:v", "high",
                "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(out_path.resolve())]
        progress(0.1, "đang dựng video")
        proc = subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                              capture_output=True, text=True, cwd=td)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg present failed: {proc.stderr[-2000:]}")
    progress(1.0, "done")
    return out_path


# ------------------------------------------------------------------ the job

def run(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    from mediaforge.pipelines import _out_dir, _sub, auto_music

    t0 = time.time()
    out = _out_dir("presentations", job_id)
    rec = Path(str(p.get("recording") or "")).expanduser()
    if not rec.is_file():
        raise ValueError("recording: the owner's recording of the talk (video or audio file) is required")
    name = re.sub(r"^\d+-[0-9a-f]{6}-", "", rec.stem)  # /api/uploads prefixes time-hex-
    info = probe(rec)
    if info["duration"] <= 0:  # a browser (MediaRecorder) webm has no duration anywhere: remux once, no re-encode
        fixed = out / f"recording{'.mkv' if info['video'] else '.mka'}"
        A.run_ffmpeg(["-i", str(rec), "-map", "0", "-c", "copy", str(fixed)])
        rec, info = fixed, probe(fixed)
    if not info["audio"]:
        raise ValueError(f"{rec.name} has no audio track")
    total = info["duration"]
    if total <= 0:
        raise ValueError(f"{rec.name}: cannot read its length")
    aspect = p.get("aspect") or "16:9"
    if aspect not in compose.RESOLUTIONS:
        raise ValueError(f"aspect must be one of {', '.join(compose.RESOLUTIONS)}")
    W, H = compose.RESOLUTIONS[aspect]
    layout = p.get("layout") or "bubble"
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {', '.join(LAYOUTS)}")
    corner = p.get("camera_pos") or "bottom-right"
    if corner not in CORNERS:
        raise ValueError(f"camera_pos must be one of {', '.join(CORNERS)}")
    warnings: list[str] = []
    if layout != "slides" and not info["video"]:
        warnings.append("bản ghi chỉ có tiếng (không có hình) → chỉ hiện slide, không có khung camera")
        layout = "slides"
    music_id = p.get("music") or None
    music_file = None if music_id == "auto" else music.resolve(music_id)  # fail fast on a missing track

    # ---- slides
    frames: list[Path] = []
    md_script: list[str] = []
    if layout != "recording":
        progress(0.02, "đọc slide")
        frames, md_script = load_slides(p, out / "slides", W, H)

    # ---- the voice: denoise + the owner's tone sliders, same length as the recording
    progress(0.08, "xử lý giọng")
    fx = present_fx(p.get("fx"))
    voice_wav = process_voice(rec, out / "voice.wav", fx=fx, denoise=p.get("denoise", True), seconds=total,
                              offset=info["audio_offset"])

    # ---- what was said, and when: Whisper word timings, aligned to the script when there is one
    script = p.get("script") or ""
    parts = split_script(script) if script.strip() else md_script
    lang = p.get("lang") or "auto"
    if lang == "auto" and parts:
        lang = detect_lang(" ".join(parts))
    progress(0.25, "nhận dạng lời nói")
    wav16 = A.to_wav(voice_wav, out / "voice16k.wav", sr=16_000)
    tr = asr.transcribe(wav16, lang=None if lang == "auto" else lang)
    wav16.unlink(missing_ok=True)
    lang = lang if lang != "auto" else tr["language"]
    heard = [w.as_dict() for w in tr["words"]]
    words = [w.as_dict() for w in asr.align_script(" ".join(parts), tr["words"], total)] if parts else heard

    # ---- slide timing
    n = len(frames)
    timing = "none"
    guessed = [False] * n
    if n:
        if p.get("slide_times"):
            times = [float(t) for t in p["slide_times"]][:n]
            if len(times) < n:
                warnings.append(f"slide_times có {len(times)} mốc cho {n} slide: các slide còn lại chia đều phần cuối")
                rest = total - (times[-1] if times else 0)
                times += [(times[-1] if times else 0) + rest * k / (n - len(times) + 1)
                          for k in range(1, n - len(times) + 1)]
            starts, timing = clean_starts(times, total), "manual"
        elif parts and len(parts) == n:
            starts, guessed = slide_starts(parts, words, total, heard)
            timing = "script"
            if any(guessed):
                warnings.append("slide " + ", ".join(str(i + 1) for i, g in enumerate(guessed) if g)
                                + ": phần đầu lời thoại không khớp lời nói trong bản ghi → mốc chuyển slide là ước "
                                "đoán (ở khoảng lặng gần nhất), hãy kiểm tra trong bảng mốc chuyển slide")
        else:
            if parts:
                warnings.append(f"lời thoại có {len(parts)} đoạn nhưng có {n} slide → chia đều thời gian; tách lời "
                                "thoại theo slide bằng dòng `---` (hoặc truyền slide_times) để chuyển slide đúng lúc")
            elif n > 1:
                warnings.append("không có lời thoại theo slide → chia đều thời gian; gửi lời thoại tách bằng `---` "
                                "hoặc slide_times để chuyển slide đúng lúc")
            starts, timing = even_starts(n, total), "even"
    else:
        starts = []

    # ---- subtitles
    burn = p.get("burn_subtitles", True) and p.get("subtitles", True) and bool(words)
    subs = subtitles.write_all(words, out / "subtitles", width=W, height=H, karaoke=p.get("karaoke")) if words else {}

    # ---- music
    if music_id == "auto":
        music_id = auto_music(None, kind="present", seed=str(job_id or name))
        music_file = music.resolve(music_id) if music_id else None

    progress(0.45, "dựng video")
    final = render(frames, starts, total, out / "final.mp4", recording=rec, voice_wav=voice_wav, layout=layout,
                   aspect=aspect, corner=corner, size_frac=float(p.get("camera_size", 0.3)),
                   ass=subs.get("ass") if burn else None, music_file=music_file,
                   music_volume=float(p.get("music_volume", 0.12)), progress=_sub(progress, 0.45, 0.97))
    thumb = compose.thumbnail(final, out / "thumbnail.jpg", at=min(1.5, total / 2))
    ends = starts[1:] + [total]
    slide_rows = []
    for i, f in enumerate(frames):
        said = [w["text"] for w in words if starts[i] <= w["start"] < ends[i]]
        slide_rows.append({"slide": i + 1, "image": str(f), "start": round(starts[i], 2), "end": round(ends[i], 2),
                           "first_words": " ".join(said[:8]), "check": bool(guessed[i]) if timing == "script" else False})
    report = {"kind": "present", "aspect": aspect, "layout": layout, "slide_timing": timing, "warnings": warnings,
              "music_id": music_id, "music_credit": music.credit(music_id), "fx": fx.as_dict(),
              "denoise": bool(p.get("denoise", True)), "elapsed": round(time.time() - t0, 1)}
    result = {"video": str(final), "thumbnail": str(thumb), "title": p.get("title") or name,
              "duration": round(A.probe_duration(final), 2), "voice": str(voice_wav), "lang": lang,
              "subtitles": subs, "words": words, "slides": slide_rows, "report": report}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return result
