"""Hát & Ngâm thơ: a poem or lyrics → sung/chanted track in the owner's cloned voice (+ accompaniment, + video).

  1. interpret  the LLM reads the poem (text only): mood, the poet's spirit, one image prompt per line
  2. speak      each line in the owner's voice (VieNeu/VoxCPM with ASR QA — the usual speak pipeline)
  3. melody     tone-aware melody (melody.py) in a key fitted to the owner's own speaking pitch
  4. sing       WORLD resynthesis of every syllable onto its note (resynth.py)
  5. accompany  đàn tranh / sáo / drone / percussion locked to the voice (accompany.py), a library track, or none
  6. mix        vocal EQ + hall reverb + accompaniment, mastered to -14 LUFS (wav + mp3)
  7. video      (optional) "hoạt cảnh": one image per line, title card, karaoke lyrics per syllable
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from mediaforge import asr, image, llm, music, subtitles
from mediaforge import audio as A
from mediaforge.config import settings
from mediaforge.sing import accompany, ace, melody, poems, resynth, tones
from mediaforge.video import compose
from mediaforge.voice import fx as FX
from mediaforge.voice import profiles
from mediaforge.voice import speak as S

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
VISUAL_STYLE = ("cinematic, classical East Asian ink-wash painting blended with photoreal night light, "
                "deep indigo and gold palette, volumetric moonlight, epic scale, no text, no letters")


def _sub(progress: Progress, lo: float, hi: float) -> Progress:
    return lambda p, m: progress(lo + (hi - lo) * p, m)


def note_name(hz: float) -> str:
    midi = 69 + 12 * math.log2(hz / 440.0)
    return f"{NOTE_NAMES[int(round(midi)) % 12]}{int(round(midi)) // 12 - 1}"


def snap_hz(hz: float) -> float:
    """Nearest equal-tempered pitch, so the accompaniment and any library music share one tuning."""
    midi = round(69 + 12 * math.log2(hz / 440.0))
    return 440.0 * 2 ** ((midi - 69) / 12)


# ------------------------------------------------------------------ 1. interpretation

def interpret(text: str, *, title: str | None, author: str | None, spirit: str | None, n_lines: int,
              style: str) -> dict[str, Any]:
    """Mood + spirit + one English image prompt per line, from the LLM (text only; audio never leaves the Mac)."""
    lines = tones.split_lines(text)
    numbered = "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines))
    hint = f"\nThe author describes the spirit they want conveyed: {spirit}" if spirit else ""
    prompt = (
        f"A Vietnamese poem to be {'chanted (ngâm thơ)' if style == 'ngam' else 'sung'} in the author's own voice "
        f"and illustrated as a short animated film.\nTitle: {title or '(untitled)'}; author: {author or 'the speaker'}."
        f"{hint}\n\nPoem:\n{numbered}\n\n"
        "Understand the poem's stance and character (khí khái) — e.g. proud solitude, heroic freedom, nostalgia — "
        "and return JSON with:\n"
        '  "mood": one of "hung-trang" (majestic/proud/heroic), "tram-mac" (calm/contemplative), '
        '"u-hoai" (melancholy/longing), "bi-ai" (grief);\n'
        '  "spirit_vi": one Vietnamese sentence naming the poem\'s spirit as the author would want it affirmed;\n'
        '  "title_vi": a short Vietnamese title if none was given (else repeat the given title);\n'
        f'  "opening": an English image prompt for the opening shot (establishing the world of the poem);\n'
        f'  "lines": exactly {n_lines} English image prompts, one per line, each a concrete cinematic scene that '
        "shows the line's imagery AND the speaker's stance (pose, scale against the landscape, light), consistent "
        "character and setting across shots (same lone figure, same era and clothing);\n"
        '  "closing": an English image prompt for the final shot that affirms the spirit.\n'
        "Image prompts: no text, letters, or calligraphy in the image; describe subject, setting, composition, light."
    )
    try:
        data, provider = llm.complete_json(prompt, timeout=240)
        prompts = [str(p) for p in data.get("lines", [])]
        if len(prompts) != n_lines:
            raise ValueError(f"expected {n_lines} line prompts, got {len(prompts)}")
        mood = data.get("mood") if data.get("mood") in melody.MOODS else "hung-trang"
        return {"mood": mood, "spirit_vi": str(data.get("spirit_vi") or spirit or ""),
                "title": title or str(data.get("title_vi") or lines[0]), "opening": str(data.get("opening") or prompts[0]),
                "lines": prompts, "closing": str(data.get("closing") or prompts[-1]), "llm": provider}
    except Exception as e:  # noqa: BLE001 — the song must not depend on the LLM
        log.warning("poem interpretation failed (%s); using defaults", e)
        base = "a lone scholar in flowing robes on a mountain ridge at night under a vast starry sky"
        return {"mood": "hung-trang", "spirit_vi": spirit or "", "title": title or lines[0],
                "opening": f"{base}, wide establishing shot", "lines": [f"{base}, evoking: {l}" for l in lines],
                "closing": f"{base}, raising a cup to the stars, triumphant", "llm": None}


# ------------------------------------------------------------------ 2–4. voice → song

def clarity(text: str, heard: str) -> dict[str, Any]:
    """How much of the line a listener (Whisper) catches: syllables right ignoring tones, and with tones."""
    exp, got = tones.syllables(text), tones.syllables(heard)
    match = lambda a, b: sum(m.size for m in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_matching_blocks())
    base = match([tones.base_letters(x) for x in exp], [tones.base_letters(x) for x in got])
    full = match([x.lower() for x in exp], [x.lower() for x in got])
    n = max(1, len(exp))
    return {"heard": heard, "syllables": f"{base}/{len(exp)}", "with_tones": f"{full}/{len(exp)}",
            "score": round((base + full) / (2 * n), 3)}


# Speech candidates per line (engine, seed offset): the sung result of each is heard back and the clearest kept.
CANDIDATES = [(None, 0), ("vieneu", 101), ("voxcpm", 202), (None, 303), ("vieneu", 404), ("voxcpm", 505)]


def sing_vocal(lines: list[list[tones.Syllable]], mel: melody.Melody, *, voice: str | None, engine: str | None,
               seed: int, out: Path, progress: Progress, key_shift: float = 0.0, tries: int = 4) -> dict[str, Any]:
    """Speak each line in the owner's voice, re-sing it on the melody, and listen back.

    Self-repair: when Whisper does not catch every syllable of the sung line, the line is spoken again
    (another seed / engine) and re-sung; the clearest take wins. The speech is always the owner's cloned voice.
    """
    sung: list[tuple[float, np.ndarray]] = []
    reports, tonic = [], None
    for li, line in enumerate(lines):
        text = " ".join(s.text for s in line)
        notes = [n for n in mel.notes if n.line == li]
        best: dict[str, Any] | None = None
        plan = [(engine, 0)] + [c for c in CANDIDATES[1:]] if engine else CANDIDATES
        for k, (eng, off) in enumerate(plan[:max(1, tries)]):
            progress((li + k / tries) / len(lines), f"line {li + 1}/{len(lines)}: take {k + 1}")
            audio, rep = S.synth_sentence(text, "vi", voice, engine=eng, seed=seed + li + off)
            audio = A.trim_silence(audio)
            words = asr.align_script(text, asr.transcribe(audio, lang="vi")["words"], len(audio) / A.SR)
            an = resynth.analyse(audio)
            if tonic is None:
                # Key: the level tone ('home') sits two semitones above the owner's own speaking pitch.
                home = melody.ladder_to_semitones(melody.MOODS[mel.mood]["home"], mel.scale)
                tonic = snap_hz(resynth.median_f0(an) * 2 ** ((2 + key_shift - home) / 12))
            bounds = resynth.refine_bounds(an, [(w.start, w.end) for w in words], [w.text for w in words])
            y = resynth.sing_line(an, bounds, notes, tonic, seed=seed + li)
            cl = clarity(text, asr.transcribe(y, lang="vi")["text"])
            take = {"y": y, "audio": audio, "clarity": cl, "engine": rep.get("engine"), "tts_score": rep.get("score"),
                    "take": k + 1}
            if best is None or cl["score"] > best["clarity"]["score"]:
                best = take
            if cl["score"] >= 1.0:
                break
        assert best is not None
        A.save(out / "speech" / f"line_{li + 1:02d}.wav", best["audio"])
        sung.append((notes[0].start, best["y"]))
        reports.append({"line": li, "text": text, "engine": best["engine"], "tts_score": best["tts_score"],
                        "take": best["take"], "takes_tried": k + 1, **{f"sung_{a}": b for a, b in best["clarity"].items()}})
    return {"vocal": resynth.place(sung, mel.length), "tonic": float(tonic or 110.0), "lines": reports}


# ------------------------------------------------------------------ 6. mix

def _stereo(x: np.ndarray) -> np.ndarray:
    return np.vstack([x, x]) if x.ndim == 1 else x


def mix(vocal: np.ndarray, backing: np.ndarray | None, *, style: str, fxp: FX.VoiceFX, out: Path) -> dict[str, str]:
    from pedalboard import Pedalboard, Reverb

    shaped = FX.shape(vocal, FX.VoiceFX(pitch=0, pace=1, warmth=fxp.warmth, clarity=fxp.clarity))
    shaped = shaped / (np.abs(shaped).max() + 1e-9) * 0.8
    hall = Reverb(room_size=0.78 if style == "ngam" else 0.6, damping=0.45, wet_level=0.24 if style == "ngam" else 0.17,
                  dry_level=0.82, width=1.0)
    voc = Pedalboard([hall])(_stereo(shaped).astype(np.float32), A.SR)
    _save_stereo(out / "vocal.wav", voc)
    total = voc
    if backing is not None:
        back = Pedalboard([Reverb(room_size=0.6, wet_level=0.15, dry_level=0.9, width=1.0)])(_stereo(backing), A.SR)
        _save_stereo(out / "accompaniment.wav", back)
        n = max(voc.shape[1], back.shape[1])
        total = np.zeros((2, n), dtype=np.float32)
        total[:, :voc.shape[1]] += voc
        total[:, :back.shape[1]] += back * (0.42 if style == "ngam" else 0.55)
    pre = _save_stereo(out / "song.pre.wav", total / (np.abs(total).max() + 1e-9) * 0.9)
    wav = out / "song.wav"
    A.run_ffmpeg(["-i", str(pre), "-af", "loudnorm=I=-14:TP=-1.0:LRA=11", "-ar", str(A.SR), "-c:a", "pcm_s16le", str(wav)])
    pre.unlink(missing_ok=True)
    mp3 = FX.to_mp3(wav, out / "song.mp3")
    return {"audio": str(wav), "mp3": str(mp3), "vocal": str(out / "vocal.wav"),
            "accompaniment": str(out / "accompaniment.wav") if backing is not None else None}


def _save_stereo(path: Path, x: np.ndarray) -> Path:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(x.T, -1, 1).astype(np.float32), A.SR, subtype="PCM_16")
    return path


def library_backing(track: str, seconds: float) -> np.ndarray:
    """A music-library track (or a local file) faded to the song's length."""
    f = music.resolve(track)
    y = A.load(f)
    n = int(seconds * A.SR)
    y = np.tile(y, int(math.ceil(n / max(1, len(y)))))[:n]
    fade = int(3 * A.SR)
    y[:fade] *= np.linspace(0, 1, fade)
    y[-fade:] *= np.linspace(1, 0, fade)
    return (y / (np.abs(y).max() + 1e-9) * 0.6).astype(np.float32)


# ------------------------------------------------------------------ 7. video

def lyric_words(mel: melody.Melody) -> list[dict[str, Any]]:
    """Syllable timings straight from the melody — exact, no ASR needed."""
    return [{"text": n.syllable, "start": round(n.start, 3), "end": round(n.end, 3), "line": n.line} for n in mel.notes]


def write_lyrics(mel: melody.Melody, out_base: Path, *, width: int, height: int, title: str | None,
                 author: str | None) -> dict[str, str]:
    """One cue per poem line (kept whole: a verse line is never split), karaoke per syllable, plus a title card."""
    layout = subtitles.Layout.for_frame(width, height, karaoke=True)
    cues = []
    for line in mel.lines():
        ws = [{"text": n.syllable, "start": n.start, "end": n.end} for n in line]
        wrapped = subtitles.wrap_words([w["text"] for w in ws], layout) or [" ".join(w["text"] for w in ws)]
        end = line[-1].end + min(1.0, line[-1].rest_after * 0.8)
        cues.append(subtitles.Cue(max(0.0, line[0].start - 0.5), end, ws, wrapped))
    for a, b in zip(cues, cues[1:]):  # one verse line on screen at a time
        a.end = min(a.end, b.start - 0.05)
    srt = subtitles.write_srt(cues, out_base.with_suffix(".srt"))
    vtt = subtitles.write_vtt(cues, out_base.with_suffix(".vtt"))
    ass = subtitles.write_ass(cues, out_base.with_suffix(".ass"), width=width, height=height, karaoke=True,
                              highlight="#FFD36A")
    if title:
        # Title card over the prelude: large, centred, slow fade in/out.
        txt = Path(ass).read_text("utf-8")
        size = int((96 if height <= width else 120) * height / (1080 if height <= width else 1920))
        style = (f"Style: Title,Georgia,{size},&H00F2E6C8,&H00F2E6C8,&H00000000,&H64000000,-1,0,0,0,100,100,2,0,1,"
                 f"3,2,5,60,60,0,1\n")
        txt = txt.replace("\n[Events]", style + "\n[Events]", 1)
        end = max(3.0, mel.notes[0].start - 0.4)
        ts = lambda t: f"0:{int(t // 60):02d}:{t % 60:05.2f}"
        sub = f"\\N{{\\fs{int(size * 0.45)}}}{author}" if author else ""
        txt += f"Dialogue: 1,{ts(0.3)},{ts(end)},Title,,0,0,0,,{{\\fad(900,700)}}{title}{sub}\n"
        Path(ass).write_text(txt, "utf-8")
    return {"srt": str(srt), "vtt": str(vtt), "ass": str(ass)}


def make_video(mel: melody.Melody, song_wav: str, interp: dict[str, Any], out: Path, *, aspect: str, seed: int,
               title: str | None, author: str | None, progress: Progress) -> dict[str, Any]:
    W, H = compose.RESOLUTIONS[aspect]
    lines = mel.lines()
    # Shots: opening (prelude), one per line, closing (from the last line's end to the outro's end).
    prompts = [interp["opening"], *interp["lines"], interp["closing"]]
    starts = [0.0] + [l[0].start - 0.3 for l in lines] + [lines[-1][-1].end - 0.6]
    total = A.probe_duration(song_wav)
    durs = [max(1.0, b - a) for a, b in zip(starts, starts[1:] + [total])]
    img_aspect = aspect if aspect in image.ASPECTS else "16:9"
    scenes = []
    motions = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "zoom_in_tl"]
    for i, (pr, d) in enumerate(zip(prompts, durs)):
        progress(i / len(prompts) * 0.8, f"image {i + 1}/{len(prompts)}")
        img = image.generate(pr, out_path=out / "images" / f"shot_{i + 1:02d}.png", aspect=img_aspect, seed=seed + i,
                             style=VISUAL_STYLE, quality="standard")
        scenes.append(compose.Scene(img["image"], d, motion="zoom_in" if i == 0 else motions[i % len(motions)]))
    subs = write_lyrics(mel, out / "lyrics", width=W, height=H, title=title, author=author)
    progress(0.82, "composing")
    final = compose.compose(scenes, song_wav, out / "final.mp4", aspect=aspect, subtitles_ass=subs["ass"],
                            transition=1.0, transition_style="fade", progress=_sub(progress, 0.82, 0.99))
    thumb = compose.thumbnail(final, out / "thumbnail.jpg", at=min(2.0, total / 2))
    return {"video": str(final), "thumbnail": str(thumb), "subtitles": subs, "images": [s.visual for s in scenes]}


# ------------------------------------------------------------------ job entry

def run(p: dict[str, Any], progress: Progress, job_id: str | None = None, *, kind: str = "recite") -> dict[str, Any]:
    """kind 'recite' (ngâm thơ, default style ngam) | 'sing' (hát, default style hat)."""
    t0 = time.time()
    out = settings.sub("outputs", "songs", job_id or time.strftime("%Y%m%d-%H%M%S"))
    style = p.get("style") or ("hat" if kind == "sing" else "ngam")
    seed = int(p.get("seed", 2026))
    title, author = p.get("title"), p.get("author")
    text = (p.get("text") or "").strip()
    if p.get("poem_id"):
        poem = poems.get(p["poem_id"])
        text = text or poems.text_for(poem, p.get("version"))
        title = title or poem["title_hv"]
        author = author or poem["author_hv"]
    if not text:
        raise ValueError("provide text (the poem or lyrics) or poem_id")
    lines = tones.parse(text)
    if not lines:
        raise ValueError("no Vietnamese syllables found in text")
    if sum(len(l) for l in lines) > 400:
        raise ValueError("text too long for one song (max ~400 syllables)")
    voice = p.get("voice") or profiles.default_voice()
    vprof = profiles.get_voice(voice) if voice else None
    fxp = FX.VoiceFX.from_dict({**((vprof or {}).get("fx") or {}), **(p.get("fx") or {})})

    progress(0.01, "reading the poem")
    want_video = bool(p.get("video", False))
    mood = p.get("mood") or "auto"
    interp: dict[str, Any] | None = None
    if mood == "auto" or want_video:
        interp = interpret(text, title=title, author=author, spirit=p.get("spirit"), n_lines=len(lines), style=style)
        title = title or interp["title"]
        if mood == "auto":
            mood = interp["mood"]
    mood = mood if mood in melody.MOODS else "hung-trang"
    mel = melody.plan(lines, style=style, mood=mood, bpm=p.get("bpm"), tempo=float(p.get("tempo", 1.0)))
    (out / "melody.json").write_text(json.dumps(mel.as_dict(), ensure_ascii=False, indent=1))

    vo = sing_vocal(lines, mel, voice=voice, engine=p.get("engine"), seed=seed, out=out,
                    progress=_sub(progress, 0.05, 0.55), key_shift=float(p.get("key_shift", fxp.pitch)))
    progress(0.57, "accompaniment")
    acc = p.get("accompaniment", "auto")
    backing = None
    if acc in ("auto", "traditional", None):
        backing = accompany.arrange(mel, vo["tonic"], seed=seed)
    elif acc in ("ace", "ace+traditional"):
        # AI arrangement bed (ACE-Step sidecar) under the key-locked traditional instruments.
        progress(0.58, "accompaniment: ACE-Step")
        bed = ace.bed(mood=mood, style=style, seconds=mel.length + 1, tonic_hz=vo["tonic"], scale=mel.scale,
                      bpm=mel.bpm, seed=seed)
        trad = accompany.arrange(mel, vo["tonic"], seed=seed)
        n = max(bed.shape[1], trad.shape[1])
        backing = np.zeros((2, n), dtype=np.float32)
        backing[:, :bed.shape[1]] += bed * (0.55 if acc == "ace+traditional" else 1.0)
        if acc == "ace+traditional":
            backing[:, :trad.shape[1]] += trad * 0.75
        backing /= np.abs(backing).max() + 1e-9
    elif acc not in ("none", ""):
        backing = library_backing(acc, mel.length + 1)
    progress(0.62, "mixing")
    files = mix(vo["vocal"], backing, style=style, fxp=fxp, out=out)
    words = lyric_words(mel)
    result: dict[str, Any] = {
        **files, "kind": kind, "style": style, "mood": mood, "title": title, "author": author, "text": text,
        "lines": [" ".join(s.text for s in l) for l in lines], "form": mel.form,
        "key": f"{note_name(vo['tonic'])} {'pentatonic ' + mel.scale}", "tonic_hz": round(vo["tonic"], 2),
        "bpm": mel.bpm, "duration": round(A.probe_duration(files["audio"]), 2), "voice_id": voice,
        "words": words, "melody": str(out / "melody.json"),
        "report": {"lines": vo["lines"], "spirit": (interp or {}).get("spirit_vi"), "llm": (interp or {}).get("llm"),
                   "accompaniment": acc,
                   "music_credit": music.credit(acc) if acc not in ("auto", "none", "traditional", "ace", "ace+traditional") else None},
    }
    if want_video:
        aspect = p.get("aspect") or "16:9"
        vid = make_video(mel, files["audio"], interp or {}, out, aspect=aspect, seed=seed, title=title, author=author,
                         progress=_sub(progress, 0.64, 0.99))
        result.update(vid)
        result["report"]["visual_prompts"] = [interp["opening"], *interp["lines"], interp["closing"]] if interp else None
    result["elapsed"] = round(time.time() - t0, 1)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    progress(1.0, "done")
    return result
