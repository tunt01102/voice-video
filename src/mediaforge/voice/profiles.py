"""Voice profiles: the owner's recordings, cleaned references and default voice settings.

Layout: data/voices/<id>/profile.json, samples/<sid>.wav (cleaned), raw/<sid>.<ext>, lexicon.json
Voice recordings are biometric personal data: they stay under data/ and are never sent
to any LLM (only text is).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from mediaforge import audio as A
from mediaforge.config import settings
from mediaforge.text.normalize import detect_lang
from mediaforge.tts.base import DEFAULT_ROUTE, VoiceRef

MAX_REF_SECONDS = 11.0

# Suggested enrolment script: varied phonemes and tones, natural sentences.
ENROL_PROMPTS = {
    "vi": [
        "Xin chào, tôi là người đang thu âm giọng nói mẫu này để tạo video bằng giọng của chính mình.",
        "Hôm nay trời nắng đẹp, chúng ta cùng nhau tìm hiểu những điều thú vị về công nghệ.",
        "Bảo mật thông tin là trách nhiệm của mỗi người, từ mật khẩu cho tới thói quen hằng ngày.",
        "Những con số như mười lăm, hai trăm ba mươi tư và một nghìn chín trăm chín mươi chín cần được đọc rõ ràng.",
        "Bạn có biết vì sao bầu trời lại có màu xanh, còn hoàng hôn thì đỏ rực như vậy không?",
    ],
    "en": [
        "Hello, I am recording this sample so that my videos can be narrated in my own voice.",
        "The quick brown fox jumps over the lazy dog while the sun sets behind the hills.",
        "Security is everyone's responsibility, from strong passwords to careful daily habits.",
        "Numbers like fifteen, two hundred and thirty-four, and nineteen ninety-nine should sound clear.",
        "Have you ever wondered why the sky is blue, but sunsets glow orange and red?",
    ],
}


def _dir(voice_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", voice_id):
        raise ValueError("voice id must be lowercase letters, digits, - or _")
    return settings.data_dir / "voices" / voice_id


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "voice")[:30]


def list_voices() -> list[dict[str, Any]]:
    root = settings.sub("voices")
    out = []
    for p in sorted(root.glob("*/profile.json")):
        out.append(json.loads(p.read_text("utf-8")))
    return out


def get_voice(voice_id: str) -> dict[str, Any]:
    p = _dir(voice_id) / "profile.json"
    if not p.exists():
        raise KeyError(f"voice {voice_id!r} not found")
    return json.loads(p.read_text("utf-8"))


def save_voice(profile: dict[str, Any]) -> dict[str, Any]:
    d = _dir(profile["id"])
    d.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = time.time()
    (d / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2), "utf-8")
    return profile


def create_voice(name: str, voice_id: str | None = None, description: str = "") -> dict[str, Any]:
    vid = voice_id or _slug(name)
    if (_dir(vid) / "profile.json").exists():
        raise ValueError(f"voice {vid!r} already exists")
    return save_voice({
        "id": vid, "name": name, "description": description, "created_at": time.time(),
        "fx": {"pitch": 0.0, "pace": 1.0, "warmth": 0.0, "clarity": 0.5},
        "engines": dict(DEFAULT_ROUTE), "samples": [], "refs": {},
    })


def update_voice(voice_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    p = get_voice(voice_id)
    for k in ("name", "description", "fx", "engines", "refs"):
        if k in patch and patch[k] is not None:
            p[k] = {**p[k], **patch[k]} if isinstance(p.get(k), dict) and isinstance(patch[k], dict) else patch[k]
    return save_voice(p)


def delete_voice(voice_id: str) -> None:
    shutil.rmtree(_dir(voice_id), ignore_errors=True)


def _snr_db(x: np.ndarray, sr: int) -> float:
    frame = int(0.03 * sr)
    n = len(x) // frame
    if n < 4:
        return 0.0
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    noise, speech = np.percentile(rms, 10), np.percentile(rms, 90)
    return float(20 * np.log10(speech / max(noise, 1e-6)))


def add_sample(voice_id: str, src: str | Path, *, lang: str | None = None, text: str | None = None, denoise: bool = True) -> dict[str, Any]:
    """Clean a recording, transcribe it, store it and re-pick the best references."""
    from mediaforge import asr

    profile = get_voice(voice_id)
    d = _dir(voice_id)
    sid = uuid.uuid4().hex[:8]
    raw = d / "raw" / f"{sid}{Path(src).suffix or '.wav'}"
    raw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, raw)
    wav = A.to_wav(raw, d / "raw" / f"{sid}.48k.wav")
    x = A.trim_silence(A.load(wav))
    if denoise:
        import noisereduce as nr

        x = nr.reduce_noise(y=x, sr=A.SR, stationary=False, prop_decrease=0.75).astype(np.float32)
    peak = float(np.max(np.abs(x)) or 1.0)
    x = x / peak * 0.89
    tr = asr.transcribe(x, lang=lang)
    lang = lang or tr["language"] or detect_lang(tr["text"])
    words = tr["words"]
    # Keep the reference short and end it on a word boundary.
    if len(x) / A.SR > MAX_REF_SECONDS and words:
        cut_word = max((w for w in words if w.end <= MAX_REF_SECONDS), key=lambda w: w.end, default=words[0])
        x = x[: int((cut_word.end + 0.15) * A.SR)]
        heard = " ".join(w.text for w in words if w.end <= cut_word.end)
        text = None  # the user's full text no longer matches the cropped clip
    else:
        heard = tr["text"]
    out = A.save(d / "samples" / f"{sid}.wav", x)
    sample = {
        "id": sid, "file": str(out.relative_to(settings.data_dir)), "lang": lang,
        "text": (text or heard).strip(), "asr_text": heard.strip(),
        "duration": round(len(x) / A.SR, 2), "snr_db": round(_snr_db(x, A.SR), 1), "created_at": time.time(),
    }
    profile["samples"].append(sample)
    profile["refs"] = pick_refs(profile["samples"])
    save_voice(profile)
    return sample


def remove_sample(voice_id: str, sample_id: str) -> dict[str, Any]:
    p = get_voice(voice_id)
    keep = []
    for s in p["samples"]:
        if s["id"] == sample_id:
            (settings.data_dir / s["file"]).unlink(missing_ok=True)
        else:
            keep.append(s)
    p["samples"] = keep
    p["refs"] = pick_refs(keep)
    return save_voice(p)


def _quality(s: dict[str, Any]) -> float:
    dur = s["duration"]
    dur_score = 1.0 if 5 <= dur <= MAX_REF_SECONDS else max(0.0, 1 - abs(dur - 8) / 8)
    return dur_score * 2 + min(s.get("snr_db", 0), 40) / 20


def pick_refs(samples: list[dict[str, Any]]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for lang in ("vi", "en"):
        cands = [s for s in samples if s["lang"] == lang]
        if cands:
            refs[lang] = max(cands, key=_quality)["id"]
    # Cross-lingual cloning works: fall back to the other language's best clip.
    if samples:
        best = max(samples, key=_quality)["id"]
        refs.setdefault("vi", refs.get("en", best))
        refs.setdefault("en", refs.get("vi", best))
    return refs


def voice_ref(voice_id: str | None, lang: str) -> VoiceRef | None:
    if not voice_id:
        return None
    p = get_voice(voice_id)
    sid = p.get("refs", {}).get(lang)
    sample = next((s for s in p["samples"] if s["id"] == sid), None)
    if sample is None:
        return None
    return VoiceRef(audio_path=str(settings.data_dir / sample["file"]), text=sample["text"], lang=sample["lang"])


def create_demo_voice(voice_id: str = "demo") -> dict[str, Any]:
    """Placeholder voice from macOS `say`, to prove the pipeline before real enrolment."""
    try:
        get_voice(voice_id)
        return get_voice(voice_id)
    except KeyError:
        pass
    create_voice("Demo (macOS say)", voice_id, "Placeholder voice; replace with your own recordings.")
    tmp = settings.sub("tmp")
    for lang, sys_voice in (("vi", "Linh"), ("en", "Samantha")):
        text = ENROL_PROMPTS[lang][0]
        aiff = tmp / f"demo_{lang}.aiff"
        subprocess.run(["say", "-v", sys_voice, "-o", str(aiff), text], check=True)
        add_sample(voice_id, aiff, lang=lang, text=text, denoise=False)
    return get_voice(voice_id)


def default_voice() -> str | None:
    """The voice used when a request names none: the explicit default, else the first real (non-demo) voice."""
    marker = settings.sub("voices") / "DEFAULT"
    if marker.exists():
        vid = marker.read_text().strip()
        if (_dir(vid) / "profile.json").exists():
            return vid
    voices = [v for v in list_voices() if v.get("samples")]
    real = [v for v in voices if v["id"] != "demo"]
    return (real or voices or [{}])[0].get("id")


def set_default_voice(voice_id: str) -> None:
    get_voice(voice_id)
    (settings.sub("voices") / "DEFAULT").write_text(voice_id)
