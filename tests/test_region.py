"""Giọng Trung / Nam: QA does not count a regional tone merge as a mistake, and never "fixes" it the Northern way."""

from mediaforge import asr
from mediaforge.voice import stock_review as R
from mediaforge.voice.region import REGION_TONES, regional_variant, strip_tone, tone_groups

NAM, TRUNG = REGION_TONES["Nam"], REGION_TONES["Trung"]


def test_only_a_same_letter_swap_inside_a_group_is_forgiven():
    assert strip_tone("sửa") == "sưa" and strip_tone("Số") == "sô"
    assert regional_variant("tuổi", "tuỗi", NAM) and regional_variant("chữ", "chử", NAM)
    assert not regional_variant("tuổi", "tuỗi", ())                  # a Northern voice keeps every tone
    assert not regional_variant("số", "sổ", NAM)                      # sắc → hỏi is not a Southern merge
    assert regional_variant("số", "sổ", TRUNG) and regional_variant("sự", "sử", TRUNG)
    assert not regional_variant("thời", "thơi", TRUNG)                # huyền → ngang (bằng) still counts
    assert not regional_variant("nội", "lội", TRUNG)                  # other letters: always a mistake
    assert not regional_variant("mới", "mơi", TRUNG)                  # sắc → ngang: not a merge
    assert tone_groups({"region": "Nam"}) == NAM and tone_groups({"region": "Bắc"}) == () and tone_groups(None) == ()


def test_error_rate_forgives_regional_swaps_and_nothing_else():
    exp, heard = "Chữ nghĩa và tuổi trẻ ở Hà Nội", "Chử nghĩa và tuỗi trẻ ở Hà Lội"
    north, south = asr.error_rate(exp, heard, "vi"), asr.error_rate(exp, heard, "vi", tone_groups=NAM)
    assert north["missing"] == ["chữ", "tuổi", "nội"]
    assert south["missing"] == ["nội"] and south["score"] < north["score"] and south["score"] > 0
    assert any("thanh vùng miền" in x for x in south["ignored"])


def test_stock_review_reports_raw_and_regional_tone_errors():
    raw, regional, swaps = R.tone_errors("tuổi trẻ và chữ nghĩa", "tuỗi trẻ và chử nghĩa", NAM)
    assert raw > 0 and regional == 0 and swaps == ["tuổi→tuỗi", "chữ→chử"]
    raw2, regional2, _ = R.tone_errors("tuổi trẻ và chữ nghĩa", "tuỗi trẻ và chử nghĩa")
    assert raw2 == regional2 == raw



def test_a_regional_voice_passes_its_own_tones_and_is_never_respelled_northern(monkeypatch):
    import numpy as np

    from mediaforge import audio as A
    from mediaforge.voice import profiles
    from mediaforge.voice import speak as S

    calls, asked = [], []

    class Eng:
        name, sample_rate = "vieneu", A.SR

        def synth(self, text, lang, ref, *, seed, temperature, prompt=None):
            calls.append(text)
            return np.zeros(A.SR, dtype=np.float32)

    monkeypatch.setattr(S, "get_engine", lambda n: Eng())
    monkeypatch.setattr(S.asr, "transcribe", lambda x, **kw: {"text": "Chương trình thời sử hôm nay có sổ liệu mới"})
    monkeypatch.setattr(S, "_respell_with_llm", lambda *a, **k: asked.append(1) or {"số": "sóa", "sự": "xự"})
    for vid, region in (("reg-trung", "Trung"), ("reg-bac", "Bắc")):
        profiles.save_voice({"id": vid, "name": vid, "kind": "stock", "engine": "vieneu", "preset": "x",
                             "region": region, "engines": {"vi": "vieneu"}, "fx": {}, "samples": [], "refs": {}})
    text = "Chương trình thời sự hôm nay có số liệu mới."
    _, rep = S.synth_sentence(text, "vi", "reg-trung", cache=False, learn=False)
    assert rep["passed"] and len(calls) == 1 and rep["region"] == "Trung"   # sự→sử, số→sổ: the Central reading
    calls.clear()
    _, rep = S.synth_sentence(text, "vi", "reg-bac", cache=False, learn=False)
    assert not rep["passed"] and len(calls) > 1                             # a Northern voice is still held to it


def test_native_words_are_never_respelled_for_a_regional_voice():
    from mediaforge.voice import speak as S

    assert S.native("số liệu") and S.native("sự") and not S.native("PNJ") and not S.native("Claude")
