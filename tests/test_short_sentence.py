"""Câu rất ngắn ("Chào bạn.") is judged together with a neighbouring sentence; only its own words are scored."""

import numpy as np

from mediaforge import asr
from mediaforge import audio as A
from mediaforge.voice import profiles
from mediaforge.voice import speak as S

CTX = "Hôm nay chúng ta nói về trí tuệ nhân tạo."


def test_focus_scores_only_the_short_sentence():
    ok = asr.error_rate(f"Chào bạn. {CTX}", "Chào bạn, hôm nay chúng tôi nói về trí tuệ", "vi", focus=(0, 2))
    assert ok["score"] == 0 and ok["heard_focus"] == "chào bạn"     # the context's own mistakes do not count
    bad = asr.error_rate(f"Chào bạn. {CTX}", f"Chào bà! {CTX}", "vi", focus=(0, 2))
    assert bad["score"] > 0.3 and bad["missing"] == ["bạn"]          # a real mistake still fails
    extra = asr.error_rate(f"Chào bạn. {CTX}", f"Xin chào bạn. {CTX}", "vi", focus=(0, 2))
    assert extra["score"] > 0 and extra["extra"] == ["xin"]          # words added at the outer edge count
    after = asr.error_rate(f"{CTX} Chào bạn.", f"{CTX} Chào bà.", "vi", focus=(10, 12))
    assert after["score"] > 0.3 and after["heard_focus"] == "chào bà"
    seam = asr.error_rate(f"{CTX} Chào bạn.", f"{CTX} à chào bạn", "vi", focus=(10, 12))
    assert seam["score"] == 0                                        # a sound at the seam belongs to neither


class Rec:
    sample_rate = A.SR
    name = "vieneu"
    calls: list = []

    def synth(self, text, lang, ref, *, seed, temperature, prompt=None):
        Rec.calls.append(text)
        return np.full(int(A.SR * 0.15 * len(text.split())), 0.1, dtype=np.float32)


def _shorty():
    try:
        profiles.get_voice("shorty")
    except KeyError:
        profiles.create_voice("Shorty", "shorty")
    profiles.update_voice("shorty", {"engines": {"vi": "vieneu", "en": "vieneu"}})
    return "shorty"


def _hearing(monkeypatch, alone: str):
    """Whisper hears `alone` on a clip under a second, and every longer clip correctly."""
    def transcribe(x, **kw):
        sec = len(x) / A.SR
        if sec < 1.0:
            return {"text": alone, "words": []}
        if abs(sec - 1.5) < 0.05:
            return {"text": CTX, "words": []}
        return {"text": f"Chào bạn. {CTX}" if sec < 2.1 else f"{CTX} Cảm ơn nhé.", "words": []}

    monkeypatch.setattr(S.asr, "transcribe", transcribe)


def test_garbage_alone_is_heard_again_with_a_neighbour(monkeypatch, tmp_path):
    Rec.calls = []
    monkeypatch.setattr(S, "get_engine", lambda n: Rec())
    _hearing(monkeypatch, "Hãy subscribe cho kênh Ghiền Mì Gõ")   # the classic short-clip hallucination
    r = S.speak(f"Chào bạn. {CTX} Cảm ơn nhé.", voice=_shorty(), lang="vi", out_dir=tmp_path, timings=False)
    assert [c.split()[0] for c in Rec.calls] == ["Hôm", "Chào", "Cảm"]   # the next one first, and only once
    first, last = r["sentences"][0], r["sentences"][2]
    assert first["passed"] and first["context"]["used"] and first["context"]["side"] == "after"
    assert last["passed"] and last["context"]["used"] and last["context"]["side"] == "before"
    assert first["heard"] == "chào bạn"


def test_a_near_miss_alone_is_not_rescued_by_context(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "get_engine", lambda n: Rec())
    _hearing(monkeypatch, "Chào bà!")                             # one syllable off: a real mistake, maybe
    _, rep = S.synth_sentence("Chào bạn.", "vi", _shorty(), cache=False, learn=False,
                              context=S.Context(CTX, np.full(int(A.SR * 1.5), 0.1, dtype=np.float32), "after"))
    assert not rep["passed"] and rep["context"]["used"] is False


def test_a_lone_short_sentence_is_padded(monkeypatch):
    lens = []
    monkeypatch.setattr(S, "get_engine", lambda n: Rec())
    monkeypatch.setattr(S.asr, "transcribe", lambda x, **kw: lens.append(len(x) / A.SR) or {"text": "Chào bạn"})
    S.synth_sentence("Chào bạn.", "vi", None, engine="vieneu", cache=False, learn=False)
    assert abs(lens[0] - (0.3 + 2 * S.SHORT_PAD)) < 0.01
