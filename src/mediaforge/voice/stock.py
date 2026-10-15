"""Giọng mẫu (stock voices): ready-made Vietnamese presenter voices with an instant pre-rendered preview.

docs/SDD-stock-voices.md. A stock voice has no recording of its own: it is either a preset speaker shipped with
VieNeu-TTS (Apache-2.0) or a VoxCPM2 voice designed from an English description. Profiles are named by
gender · region · style, never after a person: the VieNeu preset id is an internal field only, and no stock voice
imitates a real presenter. Previews are rendered once (job `stock_previews`) and served as files, so "▶ Nghe thử"
plays without waiting for a job.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from mediaforge.config import settings
from mediaforge.voice import profiles

Progress = Callable[[float, str], None]

# A presenter's opening: greeting, a date, a number, a pause at commas, a falling close.
PREVIEW_TEXT = ("Xin chào quý vị và các bạn. Chào mừng quý vị đến với bản tin sáng nay, thứ Năm, ngày mười lăm "
                "tháng mười. Trong số hôm nay, chúng ta sẽ cùng điểm lại ba sự kiện đáng chú ý nhất của tuần qua.")

CONSENT_PRESET = {"kind": "synthetic", "note": "VieNeu-TTS preset speaker (Apache-2.0)",
                  "verify": "preset speaker provenance not stated by VieNeu [VERIFY: owner/compliance]"}
CONSENT_DESIGN = {"kind": "synthetic", "note": "VoxCPM2 voice design from a text description (no person)"}

# The catalogue. fx is tuned by the review (docs/SDD-stock-voices.md §4); pace 1.0 = the engine's own rate.
CATALOGUE: list[dict[str, Any]] = [
    {"id": "stock-nu-bac-tin-tuc", "name": "Nữ · Bắc · Tin tức", "gender": "female", "region": "Bắc",
     "style": "tin_tuc", "engine": "vieneu", "preset": "Mai Anh"},
    {"id": "stock-nu-nam-tin-tuc", "name": "Nữ · Nam · Tin tức", "gender": "female", "region": "Nam",
     "style": "tin_tuc", "engine": "vieneu", "preset": "Thùy Dung"},
    {"id": "stock-nu-bac-tu-nhien", "name": "Nữ · Bắc · Tự nhiên", "gender": "female", "region": "Bắc",
     "style": "tu_nhien", "engine": "vieneu", "preset": "Trúc Ly"},
    {"id": "stock-nu-bac-ke-chuyen", "name": "Nữ · Bắc · Kể chuyện", "gender": "female", "region": "Bắc",
     "style": "doc_truyen", "engine": "vieneu", "preset": "Ngọc Linh"},
    {"id": "stock-nam-bac-tin-tuc", "name": "Nam · Bắc · Tin tức", "gender": "male", "region": "Bắc",
     "style": "tin_tuc", "engine": "vieneu", "preset": "Minh Đức"},
    {"id": "stock-nam-nam-tin-tuc", "name": "Nam · Nam · Tin tức", "gender": "male", "region": "Nam",
     "style": "tin_tuc", "engine": "vieneu", "preset": "Minh Triết"},
    {"id": "stock-nam-bac-tu-nhien", "name": "Nam · Bắc · Tự nhiên", "gender": "male", "region": "Bắc",
     "style": "tu_nhien", "engine": "vieneu", "preset": "Hải Đăng"},
    {"id": "stock-nam-bac-ke-chuyen", "name": "Nam · Bắc · Kể chuyện", "gender": "male", "region": "Bắc",
     "style": "doc_truyen", "engine": "vieneu", "preset": "Thiện Minh"},
    {"id": "stock-nu-mc-thiet-ke", "name": "Nữ · MC (thiết kế)", "gender": "female", "region": "Bắc",
     "style": "mc", "engine": "voxcpm",
     "instruct": "A professional Vietnamese female television presenter, clear and confident, warm bright voice, "
                 "standard northern accent, measured pace, crisp articulation"},
    {"id": "stock-nam-mc-thiet-ke", "name": "Nam · MC (thiết kế)", "gender": "male", "region": "Bắc",
     "style": "mc", "engine": "voxcpm",
     "instruct": "A professional Vietnamese male television presenter, deep warm authoritative voice, clear "
                 "articulation, standard northern accent, steady measured pace"},
]
STYLE_LABEL = {"tin_tuc": "Tin tức", "tu_nhien": "Tự nhiên", "doc_truyen": "Kể chuyện", "mc": "MC"}
DEFAULT_FX = {"pitch": 0.0, "pace": 1.0, "warmth": 0.2, "clarity": 0.6}


def ensure_profiles(catalogue: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Create or refresh the stock profiles. Owner edits to fx are kept; catalogue fields are authoritative."""
    out = []
    for c in catalogue or CATALOGUE:
        try:
            p = profiles.get_voice(c["id"])
        except KeyError:
            p = {"id": c["id"], "created_at": time.time(), "fx": dict(c.get("fx") or DEFAULT_FX), "samples": [],
                 "refs": {}}
        p.update({k: c.get(k) for k in ("name", "gender", "region", "style", "engine", "preset", "instruct")})
        p.update({"kind": "stock", "engines": {"vi": c["engine"], "en": c["engine"]},
                  "description": f"Giọng mẫu {STYLE_LABEL.get(c['style'], c['style'])}",
                  "consent": CONSENT_DESIGN if c.get("instruct") else CONSENT_PRESET})
        out.append(profiles.save_voice(p))
    return out


def stock_voices() -> list[dict[str, Any]]:
    return [v for v in profiles.list_voices() if v.get("kind") == "stock"]


def preview_key(p: dict[str, Any], text: str = PREVIEW_TEXT) -> str:
    src = json.dumps([p.get("engine"), p.get("preset"), p.get("instruct"), p.get("fx"), text], ensure_ascii=False,
                     sort_keys=True)
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def preview_file(voice_id: str) -> str | None:
    """Absolute path of the pre-rendered preview, if it is current."""
    try:
        p = profiles.get_voice(voice_id)
    except KeyError:
        return None
    pv = p.get("preview") or {}
    f = settings.data_dir / pv["file"] if pv.get("file") else None
    return str(f) if f and f.exists() and pv.get("key") == preview_key(p) else None


def build_previews(p: dict[str, Any], progress: Progress, job_id: str | None = None) -> dict[str, Any]:
    """Job `stock_previews`: render the preview of every stock voice whose preview is missing or stale."""
    from mediaforge.voice import speak as S

    voices = ensure_profiles()
    if p.get("voices"):
        voices = [v for v in voices if v["id"] in set(p["voices"])]
    built, kept = [], []
    for i, v in enumerate(voices):
        progress(i / max(1, len(voices)), f"{v['name']}")
        if preview_file(v["id"]) and not p.get("force"):
            kept.append(v["id"])
            continue
        d = settings.sub("voices", v["id"], "preview")
        r = S.speak(p.get("text") or PREVIEW_TEXT, voice=v["id"], lang="vi", fx=v.get("fx"), out_dir=d, name="preview",
                    timings=False)
        v = profiles.get_voice(v["id"])
        v["preview"] = {"file": str(settings.data_dir.joinpath(r["mp3"]).relative_to(settings.data_dir)),
                        "key": preview_key(v), "duration": r.get("duration"),
                        "passed": all(s.get("passed", True) for s in r.get("sentences", []))}
        profiles.save_voice(v)
        built.append(v["id"])
    progress(1.0, "done")
    return {"built": built, "kept": kept}
