"""Đọc tiếng Anh: a term the owner marks "English" is read as written, in the same voice and engine — never
respelled (lexicon, lint, lessons, LLM) and never moved to another engine mid-video."""

import numpy as np

from mediaforge import audio as A
from mediaforge.text import normalize as N
from mediaforge.text import pronounce
from mediaforge.voice import profiles
from mediaforge.voice import speak as S


class Rec:
    sample_rate = A.SR
    calls: list = []

    def synth(self, text, lang, ref, *, seed, temperature, prompt=None):
        Rec.calls.append((self.name, text))
        return np.zeros(A.SR // 2, dtype=np.float32)


def _engines(monkeypatch, *, fail: bool):
    Rec.calls = []
    engines = {n: type(n, (Rec,), {"name": n})() for n in ("vieneu", "voxcpm")}
    monkeypatch.setattr(S, "get_engine", lambda n: engines[n])
    monkeypatch.setattr(S.asr, "transcribe", lambda x, **kw: {"text": "heard"})
    score = 0.5 if fail else 0.0
    monkeypatch.setattr(S.asr, "error_rate", lambda *a, **k: {"score": score, "missing": ["claude"] if fail else [],
                                                               "extra": []})
    monkeypatch.setattr(S, "_respell_with_llm", lambda *a, **k: {"Claude": "clốt", "review": "ri viu"})


def _voice(route="best"):
    try:
        profiles.get_voice("mixed")
    except KeyError:
        profiles.create_voice("Mixed", "mixed")
    profiles.update_voice("mixed", {"engines": {"vi": route, "en": route}})
    return "mixed"


SENT = "Hôm nay mình dùng Claude Code để review pull request."


def test_remember_english_and_learning_never_overwrites_it():
    r = pronounce.remember("vi", {}, [], english=["Claude Code"], voice=None)
    assert r["english"] == ["Claude Code"] and "Claude Code" in N.english_terms(None)
    N.learn(None, "vi", "Claude Code", "clốt cốt")
    assert N.load_lexicon(None)["vi"]["Claude Code"] == {**N.load_lexicon(None)["vi"]["Claude Code"],
                                                        "say": "Claude Code", "source": "english"}
    # a voice's own answer overrides the global mark
    vid = _voice()
    pronounce.remember("vi", {"Claude Code": "clốt cốt"}, voice=vid)
    assert "Claude Code" not in N.english_terms(vid) and "Claude Code" in N.english_terms(None)


def test_english_term_is_kept_and_the_engine_never_switches(monkeypatch):
    vid = _voice("best")
    _engines(monkeypatch, fail=True)  # every take "fails": the repair loop runs to its budget
    _, rep = S.synth_sentence(SENT, "vi", vid, english=["Claude Code", "review", "pull request"], cache=False,
                              learn=True)
    assert {n for n, _ in Rec.calls} == {"vieneu"}           # "best" and engine repair are off for this sentence
    assert all("Claude Code" in t and "clốt" not in t and "ri viu" not in t for _, t in Rec.calls)
    assert rep["english"] == ["Claude Code", "pull request", "review"] and len(Rec.calls) > 1


def test_without_the_mark_the_default_lexicon_still_respells(monkeypatch):
    vid = _voice("vieneu")
    _engines(monkeypatch, fail=False)
    S.synth_sentence("Mình dùng Claude mỗi ngày.", "vi", vid, cache=False, learn=False)
    assert Rec.calls and "clốt" in Rec.calls[-1][1]           # DEFAULT_LEXICON: Claude → clốt
    Rec.calls.clear()
    S.synth_sentence("Mình dùng Claude mỗi ngày.", "vi", vid, english=["Claude"], cache=False, learn=False)
    assert "Claude" in Rec.calls[-1][1] and "clốt" not in Rec.calls[-1][1]


def test_lexicon_mark_applies_without_passing_terms(monkeypatch):
    vid = _voice("best")
    pronounce.remember("vi", {}, english=["GitHub"], voice=vid)
    _engines(monkeypatch, fail=False)
    _, rep = S.synth_sentence("Repo này nằm trên GitHub.", "vi", vid, cache=False, learn=False)
    assert rep["english"] == ["GitHub"] and {n for n, _ in Rec.calls} == {"vieneu"}
    assert "GitHub" in Rec.calls[-1][1] and "gít háp" not in Rec.calls[-1][1]
