"""Pha giọng (voice blending): the owner's voice stays dominant, with a set share of a second, consenting voice.

Mechanism (docs/SDD-artistry-voice-blend.md §6.2): VieNeu conditions on a 192-d speaker vector plus the reference's
codec codes. A blend keeps the owner's codes as the prompt and slides the vector toward the partner's (slerp by an
internal weight w). The codes pull hard toward the owner, so w is not what a listener hears: the response is an
S-curve (measured gi-ng-t × demo, 2026-10-15: w 0.25 → ~7 %, 0.5 → ~37 %, 0.75 → ~91 %). Each blend profile is
therefore calibrated once: render probes over a w grid, measure where each output sits between the two voices with
VieNeu's own speaker encoder, and invert that curve so `amount` (the perceived share) maps to w.

A blend profile is a voice profile with `kind: "blend"`; it has no samples of its own and points at both voices.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

# Who may be the second voice. "none"/missing → refused: a voice is biometric data of a real person.
CONSENT_KINDS = {
    "self": "giọng của chính chủ",
    "written": "người này đã đồng ý bằng văn bản",
    "synthetic": "giọng tổng hợp / không phải người thật",
}
MAX_AMOUNT = 0.5          # past half, the partner would dominate: that is not a blend of *my* voice any more
RECOMMENDED = (0.2, 0.3)
GRID = (0.0, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0)
PROBES = (
    "Mình ta với cả trời sao sáng, nâng chén sương đêm cạn hết sầu.",
    "Hôm nay trời trong xanh, gió nhẹ thổi qua hàng tre đầu làng.",
)
ENGINE = "vieneu"


# ------------------------------------------------------------------------------------------------ maths (pure)
def slerp(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """Spherical interpolation of two speaker vectors, keeping the first vector's norm (the encoder's scale)."""
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    t = float(np.clip(t, 0.0, 1.0))
    na, nb = a / np.linalg.norm(a), b / np.linalg.norm(b)
    omega = float(np.arccos(np.clip(na @ nb, -1.0, 1.0)))
    if omega < 1e-6:
        v = na
    else:
        v = (np.sin((1 - t) * omega) * na + np.sin(t * omega) * nb) / np.sin(omega)
    return (v * np.linalg.norm(a)).astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, dtype=np.float64).reshape(-1), np.asarray(b, dtype=np.float64).reshape(-1)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def position(e: np.ndarray, e_a: np.ndarray, e_b: np.ndarray) -> float:
    """Projection of an output's speaker vector on the owner→partner axis (0 = owner's reference, 1 = partner's)."""
    e, e_a, e_b = (np.asarray(x, dtype=np.float64).reshape(-1) for x in (e, e_a, e_b))
    d = e_b - e_a
    return float((e - e_a) @ d / (d @ d))


def perceived_curve(points: list[dict[str, float]]) -> list[dict[str, float]]:
    """Raw positions → perceived share, normalised by the w=0 and w=1 renders (cloning is never exact, so a pure
    owner render does not sit at 0), then made monotone in w. Adds `share` to each point."""
    pts = sorted(points, key=lambda p: p["w"])
    lo = next(p["pos"] for p in pts if p["w"] == 0.0)
    hi = next(p["pos"] for p in pts if p["w"] == 1.0)
    span = hi - lo
    if span < 0.1:
        raise ValueError("the two voices are too similar for the engine to tell apart: nothing to blend")
    run = 0.0
    out = []
    for p in pts:
        run = max(run, float(np.clip((p["pos"] - lo) / span, 0.0, 1.0)))
        out.append({**p, "share": round(run, 4)})
    return out


def weight_for(curve: list[dict[str, float]], amount: float) -> float:
    """Invert the calibrated curve: the internal weight w whose render a listener hears as `amount` of the partner."""
    amount = float(np.clip(amount, 0.0, 1.0))
    ws = np.array([p["w"] for p in curve], dtype=np.float64)
    ss = np.array([p["share"] for p in curve], dtype=np.float64)
    # Flat stretches (equal shares) would make the inverse ambiguous: keep the first w of each share level.
    keep = np.concatenate([[True], np.diff(ss) > 1e-6])
    ws, ss = ws[keep], ss[keep]
    if amount <= ss[0]:
        return float(ws[0])
    if amount >= ss[-1]:
        return float(ws[-1])
    return float(np.interp(amount, ss, ws))


# ------------------------------------------------------------------------------------------------ consent
def consent_of(profile: dict[str, Any]) -> str | None:
    c = profile.get("consent") or {}
    kind = c.get("kind") if isinstance(c, dict) else None
    return kind if kind in CONSENT_KINDS else None


def check_partner(profile: dict[str, Any]) -> None:
    if profile.get("kind") == "blend":
        raise ValueError("giọng pha không thể là một giọng pha khác")
    if profile.get("kind") == "stock":
        raise ValueError("giọng mẫu chưa pha được (không có bản ghi tham chiếu)")
    if not consent_of(profile):
        raise PermissionError(
            f"Giọng '{profile.get('name', profile.get('id'))}' chưa có ghi nhận đồng ý. Giọng nói là dữ liệu sinh trắc "
            "của một người thật: chỉ pha với giọng của chính bạn, giọng tổng hợp, hoặc người đã đồng ý bằng văn bản "
            "(đánh dấu ở tab Giọng nói).")


def normalise_consent(c: dict[str, Any] | None) -> dict[str, Any] | None:
    if not c:
        return None
    kind = c.get("kind")
    if kind in (None, "", "none"):
        return None
    if kind not in CONSENT_KINDS:
        raise ValueError(f"consent.kind must be one of {sorted(CONSENT_KINDS)} or 'none'")
    if kind == "written" and not str(c.get("by") or "").strip():
        raise ValueError("consent 'written' needs 'by' (who agreed)")
    return {"kind": kind, "by": str(c.get("by") or "").strip(), "note": str(c.get("note") or "").strip(),
            "date": c.get("date") or time.strftime("%Y-%m-%d")}


# ------------------------------------------------------------------------------------------------ calibration
def _measure(owner_id: str, partner_id: str, weights: tuple[float, ...], *, lang: str, seed: int,
             progress: Callable[[float, str], None] | None = None) -> list[dict[str, float]]:
    """Render PROBES at each internal weight; mean position/cosines of the renders between the two voices."""
    from dataclasses import replace

    from mediaforge.tts.base import BlendSpec, get_engine
    from mediaforge.voice import profiles

    eng = get_engine(ENGINE)
    ref_a = profiles.voice_ref(owner_id, lang)
    ref_b = profiles.voice_ref(partner_id, lang)
    if ref_a is None or ref_b is None:
        raise ValueError("cả hai giọng cần ít nhất một mẫu thu âm")
    e_a = eng.reference_embedding(ref_a.audio_path)
    e_b = eng.reference_embedding(ref_b.audio_path)
    points = []
    n = len(weights) * len(PROBES)
    for i, w in enumerate(weights):
        pos, ca, cb = [], [], []
        for j, text in enumerate(PROBES):
            if progress:
                progress((i * len(PROBES) + j) / n, f"đo pha giọng: w={w:.2f}")
            ref = ref_a if w == 0.0 else replace(ref_a, blend=BlendSpec(ref_b.audio_path, w, partner_id))
            y = eng.synth(text, lang, ref, seed=seed + j, temperature=0.7)
            e = eng.speaker_embedding(y, eng.sample_rate)
            pos.append(position(e, e_a, e_b))
            ca.append(cosine(e, e_a))
            cb.append(cosine(e, e_b))
        points.append({"w": w, "pos": round(float(np.mean(pos)), 4), "cos_a": round(float(np.mean(ca)), 4),
                       "cos_b": round(float(np.mean(cb)), 4)})
        log.info("blend %s×%s w=%.2f pos=%.3f cosA=%.3f cosB=%.3f", owner_id, partner_id, w,
                 points[-1]["pos"], points[-1]["cos_a"], points[-1]["cos_b"])
    return points


def calibrate(owner_id: str, partner_id: str, *, lang: str = "vi", seed: int = 11,
              progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Render the probes over GRID and measure each render's position between the two voices (GPU job)."""
    from mediaforge.voice import profiles

    points = _measure(owner_id, partner_id, GRID, lang=lang, seed=seed, progress=progress)
    return {"curve": perceived_curve(points), "ref_owner": profiles.voice_ref(owner_id, lang).audio_path,
            "ref_partner": profiles.voice_ref(partner_id, lang).audio_path, "engine": ENGINE, "lang": lang,
            "calibrated_at": time.time()}


def verify(owner_id: str, partner_id: str, curve: list[dict[str, float]], w: float, *, lang: str = "vi",
           seed: int = 23) -> dict[str, float]:
    """Fresh renders (new seeds) at the chosen weight: the share a listener would actually get, on the curve's scale."""
    pt = _measure(owner_id, partner_id, (w,), lang=lang, seed=seed)[0]
    lo = next(p["pos"] for p in curve if p["w"] == 0.0)
    hi = next(p["pos"] for p in curve if p["w"] == 1.0)
    return {"share": round(float(np.clip((pt["pos"] - lo) / (hi - lo), 0, 1)), 4), "cos_a": pt["cos_a"],
            "cos_b": pt["cos_b"]}


def safe_weight(curve: list[dict[str, float]], amount: float) -> float:
    """weight_for, then pulled back until the owner is still the nearer voice (cos to owner > cos to partner)."""
    w = weight_for(curve, amount)
    ws = [p["w"] for p in curve]
    ca = np.interp(w, ws, [p["cos_a"] for p in curve])
    cb = np.interp(w, ws, [p["cos_b"] for p in curve])
    while ca <= cb and w > 0:
        w = max(0.0, w - 0.02)
        ca, cb = np.interp(w, ws, [p["cos_a"] for p in curve]), np.interp(w, ws, [p["cos_b"] for p in curve])
    return round(float(w), 4)


def expected(curve: list[dict[str, float]], w: float) -> dict[str, float]:
    ws = [p["w"] for p in curve]
    return {k: round(float(np.interp(w, ws, [p[k] for p in curve])), 4) for k in ("share", "cos_a", "cos_b")}


# ------------------------------------------------------------------------------------------------ profiles
def _amount(amount: float) -> float:
    amount = float(amount)
    if not 0.0 < amount <= MAX_AMOUNT:
        raise ValueError(f"mức pha phải trong (0, {MAX_AMOUNT:.0%}]; khuyên dùng {RECOMMENDED[0]:.0%}–{RECOMMENDED[1]:.0%}")
    return amount


def create(owner_id: str, partner_id: str, amount: float = 0.25, *, name: str | None = None,
           voice_id: str | None = None, progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Calibrate and save a blend profile (usable as `voice` in every job)."""
    from mediaforge.voice import profiles

    amount = _amount(amount)
    owner = profiles.get_voice(owner_id)
    partner = profiles.get_voice(partner_id)
    if owner.get("kind") in ("blend", "stock"):
        raise ValueError("giọng chính phải là một giọng có bản ghi, không phải giọng pha hay giọng mẫu")
    check_partner(partner)
    if owner_id == partner_id:
        raise ValueError("chọn hai giọng khác nhau")
    cal = calibrate(owner_id, partner_id, progress=(lambda f, m: progress(0.9 * f, m)) if progress else None)
    w = safe_weight(cal["curve"], amount)
    if progress:
        progress(0.92, "kiểm tra lại mức pha")
    measured = verify(owner_id, partner_id, cal["curve"], w)
    vid = voice_id or profiles.blend_slug(owner_id, partner_id, amount)
    profile = {
        "id": vid, "kind": "blend",
        "name": name or f"{owner['name']} × {partner['name']} {round(amount * 100)}%",
        "description": f"{round((1 - amount) * 100)}% {owner['name']}, {round(amount * 100)}% {partner['name']}",
        "created_at": time.time(),
        "fx": dict(owner.get("fx") or {}),
        "engines": {"vi": ENGINE, "en": ENGINE},
        "samples": [], "refs": {},
        "parts": [{"voice": owner_id, "share": round(1 - amount, 4)}, {"voice": partner_id, "share": round(amount, 4)}],
        "blend": {"owner": owner_id, "partner": partner_id, "amount": amount, "weight": w,
                  "partner_consent": partner.get("consent"), **cal, "expected": expected(cal["curve"], w),
                  "measured": measured},
    }
    profiles.backup_voices(f"before-blend-{vid}")
    return profiles.save_voice(profile)


def set_amount(voice_id: str, amount: float) -> dict[str, Any]:
    """Change a blend's share without re-calibrating (the curve is per voice pair)."""
    from mediaforge.voice import profiles

    p = profiles.get_voice(voice_id)
    if p.get("kind") != "blend":
        raise ValueError(f"{voice_id!r} is not a blend profile")
    amount = _amount(amount)
    b = p["blend"]
    old = f" {round(b['amount'] * 100)}%"
    if p["name"].endswith(old):  # auto-generated name: keep the share in it current
        p["name"] = p["name"][: -len(old)] + f" {round(amount * 100)}%"
    names = [profiles.get_voice(v)["name"] if (profiles._dir(v) / "profile.json").exists() else v
             for v in (b["owner"], b["partner"])]
    p["description"] = f"{round((1 - amount) * 100)}% {names[0]}, {round(amount * 100)}% {names[1]}"
    b["amount"], b["weight"] = amount, safe_weight(b["curve"], amount)
    b["expected"] = expected(b["curve"], b["weight"])
    p["parts"] = [{"voice": b["owner"], "share": round(1 - amount, 4)}, {"voice": b["partner"], "share": round(amount, 4)}]
    return profiles.save_voice(p)


def run_job(p: dict[str, Any], progress: Callable[..., Any]) -> dict[str, Any]:
    def prog(frac: float, msg: str) -> None:
        progress(frac, msg)

    if p.get("voice") and p.get("amount") is not None and not p.get("partner"):
        prof = set_amount(p["voice"], p["amount"])
    else:
        prof = create(p["owner"], p["partner"], p.get("amount", 0.25), name=p.get("name"), voice_id=p.get("id"),
                      progress=prog)
    progress(1.0, "xong")
    b = prof["blend"]
    return {"voice": prof["id"], "name": prof["name"], "amount": b["amount"], "weight": b["weight"],
            "expected": b["expected"], "measured": b.get("measured"), "curve": b["curve"]}
