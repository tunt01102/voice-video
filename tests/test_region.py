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
