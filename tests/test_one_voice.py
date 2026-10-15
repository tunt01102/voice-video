"""Một giọng cho cả video: every sentence of a job on one engine, chosen once, never switched during repair."""

import numpy as np

from mediaforge import audio as A
from mediaforge import pipelines as P
from mediaforge.voice import profiles
from mediaforge.voice import speak as S


class Rec:
    sample_rate = A.SR
    calls: list = []

    def synth(self, text, lang, ref, *, seed, temperature, prompt=None):
        Rec.calls.append(self.name)
        return np.zeros(A.SR // 2, dtype=np.float32)


def _engines(monkeypatch, *, fail: bool):
    Rec.calls = []
    engines = {n: type(n, (Rec,), {"name": n})() for n in ("vieneu", "voxcpm")}
    monkeypatch.setattr(S, "get_engine", lambda n: engines[n])
    monkeypatch.setattr(S.asr, "transcribe", lambda x, **kw: {"text": "heard", "words": []})
    monkeypatch.setattr(S.asr, "error_rate", lambda *a, **k: {"score": 0.5 if fail else 0.0, "missing": [],
                                                               "extra": []})
    monkeypatch.setattr(S, "_respell_with_llm", lambda *a, **k: {})


def _voice(route="best"):
    try:
        profiles.get_voice("onev")
    except KeyError:
        profiles.create_voice("One", "onev")
    profiles.update_voice("onev", {"engines": {"vi": route, "en": route}})
    return "onev"


def test_job_engine_best_is_vieneu_for_vietnamese():
    vid = _voice("best")
    assert S.job_engine(vid, "vi") == "vieneu"
    assert S.job_engine(vid, "vi", "voxcpm") == "voxcpm"     # an explicit engine wins
    assert S.job_engine(_voice("voxcpm"), "vi") == "voxcpm"  # the voice's own route wins over the default


def test_locked_sentence_never_switches_engine(monkeypatch):
    vid = _voice("best")
    _engines(monkeypatch, fail=True)  # every take fails: the repair loop runs to its budget
    _, rep = S.synth_sentence("Xin chào các bạn thân mến.", "vi", vid, engine="vieneu", lock_engine=True,
                              cache=False, learn=False)
    assert set(Rec.calls) == {"vieneu"} and len(Rec.calls) > 1 and rep["locked"]
    Rec.calls = []
    S.synth_sentence("Xin chào các bạn thân mến.", "vi", vid, cache=False, learn=False)  # unlocked "best"
    assert set(Rec.calls) == {"vieneu", "voxcpm"}


def test_speak_one_voice_uses_one_engine_for_every_sentence(monkeypatch, tmp_path):
    vid = _voice("best")
    _engines(monkeypatch, fail=True)
    r = S.speak("Xin chào các bạn. This sentence is English. Hẹn gặp lại nhé.", voice=vid, lang="auto",
                out_dir=tmp_path, timings=False, one_voice=True)
    assert r["one_voice"] == "vieneu" and set(Rec.calls) == {"vieneu"}
    assert {s["engine"] for s in r["sentences"]} == {"vieneu"}


def test_one_voice_default_on_for_vietnamese_videos():
    assert P.one_voice({}, "vi") and not P.one_voice({}, "en")
    assert not P.one_voice({"one_voice": False}, "vi") and P.one_voice({"one_voice": True}, "en")


class _Stop(Exception):
    pass


def _rerender_engine(monkeypatch, params, sent_engine):
    from mediaforge import takes as T

    job = {"id": "j", "type": "narrated", "params": params,
           "result": {"lang": "vi", "sentences": [{"text": "Câu một.", "lang": "vi", "engine": sent_engine}]}}
    monkeypatch.setattr(T, "load_done_job", lambda jid: job)
    monkeypatch.setattr(T, "job_voice", lambda j: _voice("best"))
    seen = {}

    def synth(text, lang, voice, *, engine=None, lock_engine=False, **kw):
        seen.update(engine=engine, lock=lock_engine)
        raise _Stop

    monkeypatch.setattr(T.S, "synth_sentence", synth)
    try:
        T.rerender({"job": "j", "index": 0}, lambda *a: None)
    except _Stop:
        pass
    return seen


def test_rerender_keeps_the_jobs_engine(monkeypatch):
    assert _rerender_engine(monkeypatch, {"one_voice": True}, "voxcpm") == {"engine": "vieneu", "lock": True}
    # a video made before one_voice existed keeps the engine this sentence was read with
    assert _rerender_engine(monkeypatch, {}, "voxcpm") == {"engine": "voxcpm", "lock": True}
    assert _rerender_engine(monkeypatch, {"one_voice": False}, "voxcpm") == {"engine": None, "lock": False}
