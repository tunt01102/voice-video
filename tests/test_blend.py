"""Pha giọng: blend maths, calibration curve inversion, consent gating and blend-profile routing (no models)."""

import numpy as np
import pytest

from mediaforge.config import settings
from mediaforge.tts.base import BlendSpec
from mediaforge.voice import blend as B
from mediaforge.voice import profiles

# Measured gi-ng-t × demo response (2026-10-15, VieNeu v3 Turbo, mean of 2 seeds): an S-curve, not a line.
MEASURED = [
    {"w": 0.0, "pos": 0.246, "cos_a": 0.74, "cos_b": 0.22},
    {"w": 0.25, "pos": 0.289, "cos_a": 0.79, "cos_b": 0.35},
    {"w": 0.5, "pos": 0.484, "cos_a": 0.58, "cos_b": 0.49},
    {"w": 0.75, "pos": 0.818, "cos_a": 0.37, "cos_b": 0.77},
    {"w": 1.0, "pos": 0.877, "cos_a": 0.23, "cos_b": 0.84},
]


def test_slerp_endpoints_and_norm():
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=192), rng.normal(size=192) * 3
    assert np.allclose(B.slerp(a, b, 0), a, atol=1e-5)
    end = B.slerp(a, b, 1)
    assert B.cosine(end, b) == pytest.approx(1.0, abs=1e-5)
    assert np.linalg.norm(end) == pytest.approx(np.linalg.norm(a), rel=1e-5)  # keeps the owner's scale
    mid = B.slerp(a, b, 0.5)
    assert B.cosine(mid, a) == pytest.approx(B.cosine(mid, b), abs=1e-5)


def test_position_on_axis():
    a, b = np.zeros(4) + 1, np.array([3.0, 1, 1, 1])
    assert B.position(a, a, b) == pytest.approx(0)
    assert B.position(b, a, b) == pytest.approx(1)
    assert B.position((a + b) / 2, a, b) == pytest.approx(0.5)


def test_curve_normalises_and_inverts():
    curve = B.perceived_curve(MEASURED)
    assert curve[0]["share"] == 0 and curve[-1]["share"] == 1
    assert all(x["share"] <= y["share"] for x, y in zip(curve, curve[1:]))
    w = B.weight_for(curve, 0.25)
    # 25 % perceived needs an internal weight well above 0.25 (the owner's codes pull hard toward the owner).
    assert 0.35 < w < 0.5
    assert B.expected(curve, w)["share"] == pytest.approx(0.25, abs=1e-3)
    assert B.weight_for(curve, 0.0) == 0.0


def test_safe_weight_keeps_owner_nearer():
    curve = B.perceived_curve(MEASURED)
    w = B.safe_weight(curve, 0.5)
    e = B.expected(curve, w)
    assert e["cos_a"] > e["cos_b"]


def test_curve_rejects_indistinguishable_voices():
    flat = [{**p, "pos": 0.5} for p in MEASURED]
    with pytest.raises(ValueError):
        B.perceived_curve(flat)


def test_consent_rules():
    assert B.normalise_consent({"kind": "none"}) is None
    assert B.normalise_consent({"kind": "self"})["kind"] == "self"
    with pytest.raises(ValueError):
        B.normalise_consent({"kind": "written"})          # who agreed?
    with pytest.raises(ValueError):
        B.normalise_consent({"kind": "celebrity"})
    with pytest.raises(PermissionError):
        B.check_partner({"id": "x", "name": "X"})
    B.check_partner({"id": "x", "consent": {"kind": "written", "by": "X"}})
    with pytest.raises(ValueError):
        B._amount(0.6)


def _voice_with_sample(vid: str) -> None:
    profiles.create_voice(vid, vid)
    wav = settings.sub("voices", vid, "samples") / "s1.wav"
    wav.write_bytes(b"RIFF")
    p = profiles.get_voice(vid)
    p["samples"] = [{"id": "s1", "file": f"voices/{vid}/samples/s1.wav", "text": "xin chào", "lang": "vi",
                     "duration": 6.0, "snr_db": 30}]
    p["refs"] = {"vi": "s1", "en": "s1"}
    profiles.save_voice(p)


def test_blend_profile_routes_to_owner_ref_with_partner(monkeypatch):
    _voice_with_sample("own")
    _voice_with_sample("guest")
    monkeypatch.setattr(B, "calibrate", lambda a, b, **kw: {"curve": B.perceived_curve(MEASURED), "engine": "vieneu",
                                                            "lang": "vi"})
    monkeypatch.setattr(B, "verify", lambda a, b, curve, w, **kw: B.expected(curve, w))
    with pytest.raises(PermissionError):
        B.create("own", "guest", 0.25)
    profiles.update_voice("guest", {"consent": {"kind": "written", "by": "Guest", "note": "test"}})
    prof = B.create("own", "guest", 0.25)
    assert prof["kind"] == "blend" and prof["engines"] == {"vi": "vieneu", "en": "vieneu"}
    ref = profiles.voice_ref(prof["id"], "vi")
    assert ref.audio_path.endswith("voices/own/samples/s1.wav")
    assert isinstance(ref.blend, BlendSpec) and ref.blend.partner_audio.endswith("voices/guest/samples/s1.wav")
    assert ref.blend.weight == prof["blend"]["weight"]
    assert profiles.base_voice(prof["id"]) == "own"
    assert prof["id"] != profiles.default_voice()
    # Changing the share re-maps on the stored curve.
    w0 = ref.blend.weight
    changed = B.set_amount(prof["id"], 0.3)
    assert changed["blend"]["weight"] > w0
    assert changed["name"].endswith(" 30%") and changed["description"].startswith("70% own")
    with pytest.raises(ValueError):
        profiles.add_sample(prof["id"], "x.wav")
    with pytest.raises(ValueError):
        B.create(prof["id"], "guest", 0.2)        # a blend cannot be the main voice
