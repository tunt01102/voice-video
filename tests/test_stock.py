"""Giọng mẫu (stock voices): preset/designed speakers reach the right engine; no recordings, no blending."""

import numpy as np
import pytest

from mediaforge import audio as A
from mediaforge.tts import base
from mediaforge.voice import blend, profiles, stock
from mediaforge.voice import speak as S


@pytest.fixture
def stock_profiles():
    return {v["id"]: v for v in stock.ensure_profiles()}


class FakeEngine:
    sample_rate = A.SR
    seen: list = []

    def synth(self, text, lang, ref, *, seed, temperature, prompt=None):
        FakeEngine.seen.append((self.name, ref))
        return np.zeros(A.SR // 2, dtype=np.float32)


def _fake_engines(monkeypatch):
    FakeEngine.seen = []
    engines = {n: type(n, (FakeEngine,), {"name": n})() for n in ("vieneu", "voxcpm")}
    monkeypatch.setattr(S, "get_engine", lambda n: engines[n])
    monkeypatch.setattr(S.asr, "transcribe", lambda x, **kw: {"text": "đúng rồi"})
    monkeypatch.setattr(S.asr, "error_rate", lambda *a, **k: {"score": 0.0, "missing": [], "extra": []})
    return engines


def test_catalogue_is_named_by_style_never_by_person(stock_profiles):
    presets = {c["preset"] for c in stock.CATALOGUE if c.get("preset")}
    for v in stock_profiles.values():
        assert v["kind"] == "stock" and v["samples"] == [] and v["consent"]["kind"] == "synthetic"
        assert not any(name in v["name"] for name in presets)
        assert v["engines"] == {"vi": v["engine"], "en": v["engine"]}
    assert {v["gender"] for v in stock_profiles.values()} == {"male", "female"}


def test_preset_and_design_reach_their_engine_only(monkeypatch, stock_profiles):
    _fake_engines(monkeypatch)
    _, rep = S.synth_sentence("Xin chào quý vị.", "vi", "stock-nu-bac-tin-tuc", engine="best", cache=False, learn=False)
    name, ref = FakeEngine.seen[-1]
    assert name == "vieneu" and ref.preset == "Mai Anh" and not ref.audio_path and rep["passed"]
    assert {n for n, _ in FakeEngine.seen} == {"vieneu"}   # "best" never tries an engine that lacks the preset
    _, rep = S.synth_sentence("Xin chào quý vị.", "vi", "stock-nam-mc-thiet-ke", cache=False, learn=False)
    name, ref = FakeEngine.seen[-1]
    assert name == "voxcpm" and ref.instruct.startswith("A professional Vietnamese male") and rep["ref"].startswith("instruct:")


def test_engines_receive_preset_and_instruct_kwargs(monkeypatch):
    from mediaforge.tts import vieneu, voxcpm

    calls = {}
    v = object.__new__(vieneu.VieNeuEngine)
    v.tts = type("T", (), {"infer": lambda self, text, **kw: calls.setdefault("vieneu", kw) and np.zeros(4)})()
    v.synth("x", "vi", base.VoiceRef("", "", "vi", preset="Mai Anh"), seed=1, temperature=0.8)
    assert calls["vieneu"]["voice"] == "Mai Anh" and "ref_audio" not in calls["vieneu"]
    x = object.__new__(voxcpm.VoxCPMEngine)
    x._params = {"text", "instruct", "ref_audio", "cfg_value"}
    x.model = type("M", (), {"generate": lambda self, **kw: calls.setdefault("voxcpm", kw) and []})()
    x.synth("x", "vi", base.VoiceRef("", "", "vi", instruct="A calm presenter"), seed=1, temperature=0.8)
    assert calls["voxcpm"]["instruct"] == "A calm presenter" and "ref_audio" not in calls["voxcpm"]
    x._params = {"text", "ref_audio"}
    with pytest.raises(RuntimeError):
        x.synth("x", "vi", base.VoiceRef("", "", "vi", instruct="A calm presenter"), seed=1, temperature=0.8)


def test_stock_voices_take_no_recordings_and_do_not_blend(stock_profiles, tmp_path):
    with pytest.raises(ValueError):
        profiles.add_sample("stock-nu-bac-tin-tuc", tmp_path / "x.wav", lang="vi", text="x")
    with pytest.raises(ValueError):
        blend.check_partner(stock_profiles["stock-nu-bac-tin-tuc"])
    assert profiles.default_voice() != "stock-nu-bac-tin-tuc"


def test_preview_is_built_once_and_served(monkeypatch, stock_profiles):
    from fastapi.testclient import TestClient

    from mediaforge.api import create_app

    built = []

    def fake_speak(text, *, voice, out_dir, name, **kw):
        built.append(voice)
        mp3 = out_dir / f"{name}.mp3"
        mp3.write_bytes(b"ID3")
        return {"mp3": str(mp3), "audio": str(mp3), "duration": 9.0, "sentences": [{"passed": True}]}

    monkeypatch.setattr(S, "speak", fake_speak)
    client = TestClient(create_app())
    assert client.get("/api/voices/stock-nu-bac-tin-tuc/preview").status_code == 404
    stock.build_previews({"voices": ["stock-nu-bac-tin-tuc"]}, lambda *a: None)
    stock.build_previews({"voices": ["stock-nu-bac-tin-tuc"]}, lambda *a: None)
    assert built == ["stock-nu-bac-tin-tuc"]                     # the second run reuses the current preview
    r = client.get("/api/voices/stock-nu-bac-tin-tuc/preview")
    assert r.status_code == 200 and r.content == b"ID3"
    profiles.update_voice("stock-nu-bac-tin-tuc", {"fx": {"pace": 1.05}})
    assert stock.preview_file("stock-nu-bac-tin-tuc") is None    # new fx → preview is stale


def test_review_measures_pauses_pitch_and_bands():
    from mediaforge.voice import stock_review as R

    sr = A.SR
    t = np.arange(int(0.4 * sr)) / sr
    tone = lambda hz: (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    gap = np.zeros(int(0.2 * sr), dtype=np.float32)
    x = np.concatenate([tone(150), gap, tone(150), np.zeros(int(0.05 * sr), np.float32), tone(150)])
    assert R.silent_gaps(x) == 1                    # 200 ms counts, 50 ms does not
    f0, st = R.pitch_stats(np.concatenate([tone(150), tone(150)]))
    assert abs(f0 - 150) < 5 and st < 0.5           # a flat tone is monotone
    assert R.band(1.0, 0.92, 1.1, 0.7, 1.35) == 100 and R.band(0.7, 0.92, 1.1, 0.7, 1.35) == 0
    rows = [{"cer": 0.0, "tone_err": 0.0, "first_pass": 1.0, "rate": 5.2, "st_std": 3.0, "spk_cos": 0.95,
             "gaps_per_comma": 1.0}]
    s = R.score(rows, 5.2)
    assert s["composite"] == 100.0
    s = R.score([{**rows[0], "cer": 0.25, "st_std": 0.8}], 5.2)
    assert s["parts"]["clarity"] == 50.0 and s["parts"]["expressive"] < 30 and s["composite"] < 85
