"""Learning from mistakes, safely (docs/SDD-review-upgrade.md §5.1–5.2). No models are loaded."""

import json

import numpy as np
import pytest

from mediaforge import asr, lessons
from mediaforge import audio as A
from mediaforge.text import lint as L
from mediaforge.text import normalize as N
from mediaforge.text import pronounce as P
from mediaforge.voice import profiles
from mediaforge.voice import speak as S


# ---------------------------------------------------------------- the reading gate (§5.1.4)

@pytest.mark.parametrize("term,say,rule", [
    ("niềm tin", "niềm tin", "1:"),                                     # identity
    ("đổ đi kiểm định rồi bán lại", "đổ đi, kiểm định, rồi bán lại", "2:"),  # whole clause
    ("tốc độ của AI", "tốc độ của ây ai", "2:"),
    ("độ", "đô", "3:"),                                                 # correct Vietnamese is never respelled
    ("kiểm định", "kiểm đinh", "3:"),
    ("lung lay", "lung lai", "3:"),
    ("đổ", "đỗ", "3:"),
    ("Ét", "ét xì", "3:"),
])
def test_gate_rejects_the_polluted_entries_found_in_review(term, say, rule):
    ok, why = lessons.reading_gate(term, say, "vi")
    assert not ok and why.startswith(rule), why


def test_gate_rejects_contradicting_or_duplicate_owner_readings():
    P.remember("vi", {"SME": "Ét Em I"}, [])
    assert lessons.reading_gate("SME", "ét em ây", "vi") == (False, "5: trái với cách đọc bạn đã đặt")
    assert lessons.reading_gate("SME", "ét em i", "vi")[1] == "5: đã có trong từ điển"


def test_gate_accepts_foreign_terms_and_acronyms():
    assert lessons.reading_gate("KPI", "ca pê i", "vi") == (True, "ok")
    assert lessons.reading_gate("Kubernetes", "ku bơ nét tít", "vi") == (True, "ok")
    assert lessons.reading_gate("the", "thee", "en")[0] is False
    assert lessons.reading_gate("Nguyen", "Win", "en") == (True, "ok")


# ---------------------------------------------------------------- promotion, use, retirement

def test_reading_needs_two_different_sentences_to_become_active():
    l1 = lessons.propose_reading("v1", "vi", "vieneu", "OKR", "ô ca a", "Mục tiêu OKR quý này.")
    assert l1["status"] == "candidate"
    l1 = lessons.propose_reading("v1", "vi", "vieneu", "OKR", "ô ca a", "Mục tiêu OKR quý này.")  # same sentence again
    assert l1["status"] == "candidate" and l1["evidence"] == 1
    l2 = lessons.propose_reading("v1", "vi", "vieneu", "OKR", "ô ca a", "Chúng ta chốt OKR hôm nay.")
    assert l2["status"] == "active" and l2["evidence"] == 2
    assert lessons.readings("v1", "vi", "vieneu")["OKR"][0] == "ô ca a"
    assert "OKR" not in lessons.readings("v1", "vi", "voxcpm")  # engine-scoped
    assert "OKR" not in lessons.readings("v2", "vi", "vieneu")  # voice-scoped


def test_gate_failures_never_become_lessons():
    assert lessons.propose_reading("v1", "vi", "vieneu", "độ", "đô", "Tốc độ rất cao.") is None
    assert not [l for l in lessons.list_lessons(kind="reading") if l["key"] == "độ"]


def test_a_lesson_that_does_not_help_is_retired():
    for s in ("Câu một có SLA.", "Câu hai có SLA."):
        l = lessons.propose_reading("v3", "vi", "vieneu", "SLA", "ét eo a", s)
    assert l["status"] == "active"
    for k in range(lessons.RETIRE_AFTER_USES):
        lessons.record_use([l["id"]], won=k == 0)
    assert lessons.get(l["id"])["status"] == "retired"


def test_owner_pinned_lessons_are_not_auto_retired():
    for s in ("Một CRM tốt.", "Hai CRM tốt."):
        l = lessons.propose_reading("v4", "vi", "vieneu", "CRM", "xi a em", s)
    lessons.set_status(l["id"], "active")  # owner approves → pinned
    for _ in range(lessons.RETIRE_AFTER_USES + 2):
        lessons.record_use([l["id"]], won=False)
    assert lessons.get(l["id"])["status"] == "active"


def test_owner_retired_lesson_is_not_resurrected_by_new_evidence():
    l = lessons.propose_reading("v5", "vi", "vieneu", "ROI", "rô i", "ROI quý này.")
    lessons.set_status(l["id"], "retired")
    for s in ("ROI quý sau.", "ROI năm nay."):
        lessons.propose_reading("v5", "vi", "vieneu", "ROI", "rô i", s)
    assert lessons.get(l["id"])["status"] == "retired"


# ---------------------------------------------------------------- owner judgement (✓ Nghe đúng rồi)

def test_misheard_terms_map_back_to_the_written_words():
    terms = lessons.misheard_terms("Nhân viên trẻ dùng AI thoăn thoắt mỗi ngày.", ["thoăn thoắt"], "vi")
    assert terms == ["thoăn thoắt"]


def test_accepted_terms_become_lenient_for_that_voice():
    lessons.accept_judgement("v6", "vi", ["thoăn thoắt"], "Nhân viên trẻ dùng AI thoăn thoắt mỗi ngày.")
    assert lessons.judge_terms("v6", "vi") == {"thoăn thoắt"}
    assert lessons.judge_terms("v7", "vi") == set()
    qa = asr.error_rate("Nhân viên trẻ dùng AI thoăn thoắt mỗi ngày.", "Nhân viên trẻ dùng AI thoán thoát mỗi ngày.",
                        "vi", asr.lenient_terms("x", {"thoăn thoắt"}))
    assert qa["score"] == 0.0


# ---------------------------------------------------------------- migration of old learned entries

def test_migration_retires_polluted_entries_and_backs_up(monkeypatch):
    profiles.create_voice("Mig", "mig")
    data = {"vi": {"độ": {"say": "đô", "source": "learned", "hits": 1},
                   "KPI": {"say": "ca pê i", "source": "learned", "hits": 1},
                   "PNJ": {"say": "Pi En Di", "source": "manual", "hits": 0}}, "en": {}}
    N.save_lexicon(data, "mig")
    rep = {r["term"]: r for r in lessons.migrate_lexicons() if r["voice"] == "mig"}
    assert rep["độ"]["status"] == "retired" and rep["KPI"]["status"] == "candidate"
    left = N.load_lexicon("mig")["vi"]
    assert set(left) == {"PNJ"}  # manual kept, learned moved out
    from mediaforge.config import settings
    assert list(settings.sub("backups").glob("lexicon-mig-*.json"))
    assert "KPI" not in N.merged_lexicon("mig", "vi")  # a candidate is not applied


# ---------------------------------------------------------------- lint (§5.2.1)

def test_lint_finds_and_fixes():
    t = ("Bạn sẽ không còn là nút thắt, mà trở thành\nngười dẫn đường cho cả đội. Học AI tại AI SME, "
         "truy cập aisme.vn 胸.")
    codes = {f["code"] for f in L.check(t, "vi")}
    assert {"L1", "L3", "L4", "L5"} <= codes
    fixed, _ = L.fix_text(t, "vi")
    assert "trở thành người dẫn đường" in fixed and "胸" not in fixed and "aisme.vn." in fixed
    assert L.fix_text(fixed, "vi")[0] == fixed  # idempotent


def test_lint_does_not_flag_normal_endings():
    assert not [f for f in L.check("Mùa xuân đã đến. Anh ấy đã về. Come in.", "auto") if f["code"] == "L1"]


def test_tts_readings_keep_subtitle_text():
    r = L.tts_readings("Học AI SME tại aisme.vn nhé", "vi")
    assert r == {"aisme.vn": "aisme chấm vi en", "AI SME": "AI, SME"}
    assert N.apply_lexicon("Học AI SME tại aisme.vn nhé", {**r, "AI": "ây ai", "SME": "ét em i"}) == \
        "Học ây ai, ét em i tại aisme chấm vi en nhé"


def test_long_unpunctuated_sentence_is_split_before_conjunctions():
    s = " ".join(["một"] * 20 + ["nhưng"] + ["hai"] * 20)
    assert L.is_long(s, "vi") and len(L.conj_phrases(s, "vi")) == 2


# ---------------------------------------------------------------- judge (§5.2.3)

def test_repetition_and_judge_error_detectors():
    assert asr.repetition("lưu lịch sử mã số", "lưu lưu lưu hòi", "vi") == "lưu"
    assert asr.repetition("Báo cáo bền vững 2025 phát hành", "Báo cáo bền vững 2025 andra 2025 phát hành", "vi")
    assert asr.repetition("rất rất tốt", "rất rất tốt", "vi") is None
    assert asr.judge_error("Công kẹp胸 Trang chủ", "vi") and not asr.judge_error("Trang chủ", "vi")


# ---------------------------------------------------------------- the repair loop learns only what helped

def _fake_loop(monkeypatch, scorer):
    """synth_sentence with a fake engine: scorer(att) → (score, heard)."""
    monkeypatch.setattr(profiles, "voice_ref", lambda v, l: None)
    monkeypatch.setattr(profiles, "guide_for", lambda v, s: None)
    monkeypatch.setattr(profiles, "base_voice", lambda v: v)
    monkeypatch.setattr(profiles, "get_voice", lambda v: {"engines": {}})
    monkeypatch.setattr(S, "_respell_with_llm", lambda *a: {"SAP": "sáp"})
    calls = []

    def fake_run(att, sentence, lang, ref, lenient):
        att.score, att.heard = scorer(att)
        att.qa = {"score": att.score, "missing": ["sap"] if att.score else [], "extra": [], "dur_ratio": 1.0}
        att.audio = np.zeros(A.SR // 10, dtype=np.float32)
        calls.append(att)
        return att

    monkeypatch.setattr(S, "_run_attempt", fake_run)
    return calls


def test_respelling_is_learned_only_if_the_counterfactual_fails(monkeypatch):
    calls = _fake_loop(monkeypatch, lambda a: (0.0, "ok") if a.respell.get("SAP") else (0.4, "xáp"))
    _, rep = S.synth_sentence("Chúng tôi dùng SAP cho kế toán.", "vi", "vx", engine="vieneu", cache=False)
    assert rep["passed"] and rep["respell"] == {"SAP": "sáp"}
    assert calls[-1].strategy == "counterfactual" and calls[-1].score > 0
    cand = [l for l in lessons.list_lessons(kind="reading") if l["key"] == "SAP" and l["scope"]["voice"] == "vx"]
    assert cand and cand[0]["status"] == "candidate"


def test_respelling_is_not_learned_when_the_new_seed_fixed_it(monkeypatch):
    # Passes whenever temperature dropped (a reseed), with or without the respelling.
    _fake_loop(monkeypatch, lambda a: (0.0, "ok") if a.respell.get("SAP") and a.temperature < 0.8 or a.seed == 1234 + 7
               else (0.4, "xáp"))
    _, rep = S.synth_sentence("Hệ thống SAP mới.", "vi", "vy", engine="vieneu", cache=False)
    assert rep["passed"]
    assert not [l for l in lessons.list_lessons(kind="reading") if l["key"] == "SAP" and l["scope"]["voice"] == "vy"]


def test_repair_stops_when_nothing_improves(monkeypatch):
    calls = _fake_loop(monkeypatch, lambda a: (0.5, "sai"))
    _, rep = S.synth_sentence("Không bao giờ đúng.", "vi", "vz", engine="vieneu", cache=False, max_attempts=6)
    assert not rep["passed"] and len(calls) == 1 + S.STALL_LIMIT
    sig = [s for s in lessons.signals("qa_fail") if s["payload"]["text"] == "Không bao giờ đúng."]
    assert sig and sig[0]["payload"]["class"] in ("other", "terms")


# ---------------------------------------------------------------- ratings → preferences, plan rules; incidents

def _job(jid, **params):
    return {"id": jid, "type": "narrated", "params": {"style": "cinematic", "music": "calm-1", **params},
            "result": {"report": {"kind": "narrated"}}}


def test_rating_needs_two_consistent_votes_before_a_preference_applies():
    r = lessons.rate(_job("r1"), 2, ["hình"], "ảnh tối quá")
    assert r["rating"]["stars"] == 2 and lessons.preference("visual_pref", "narrated", "cinematic") is None
    lessons.rate(_job("r2"), 1, ["hình"])
    assert lessons.preference("visual_pref", "narrated", "cinematic") == "dislike"
    lessons.rate(_job("r3"), 5, ["hình"])  # opinion swings back → no longer applied
    lessons.rate(_job("r4"), 5, ["hình"])
    assert lessons.preference("visual_pref", "narrated", "cinematic") != "dislike"


def test_owner_note_on_bad_content_becomes_a_plan_rule():
    lessons.rate(_job("r5"), 2, ["nội dung"], "Mở đầu bằng một câu hỏi, đừng chào hỏi dài dòng")
    assert "Mở đầu bằng một câu hỏi, đừng chào hỏi dài dòng" in lessons.plan_rules("narrated")
    from mediaforge import pipelines
    assert "đừng chào hỏi" in pipelines.lessons_note("narrated", "cinematic")


def test_rating_validation_and_unknown_tags():
    with pytest.raises(ValueError):
        lessons.rate(_job("r6"), 7)
    assert lessons.rate(_job("r7"), 4, ["hình", "xyz"])["rating"]["tags"] == ["hình"]


def test_incidents_are_counted_per_error_kind():
    lessons.incident({"id": "j1", "type": "enrol"}, "RuntimeError: ffmpeg failed: EBML")
    lessons.incident({"id": "j2", "type": "enrol"}, "RuntimeError: ffmpeg failed: EBML")
    ops = [l for l in lessons.list_lessons("active", "ops") if l["scope"].get("type") == "enrol"]
    assert len(ops) == 1 and ops[0]["evidence"] == 2
    assert lessons.insights()["incidents"] >= 2


def test_spelled_acronyms_are_judged_by_their_letters():
    q = asr.error_rate("Bắt đầu học AI tại Ây Ai Ét Em I chấm vi en.", "Bắt đầu học AI tại AISME.vn", "vi")
    assert q["score"] == 0.0 and q["ignored"]
    assert asr.error_rate("Học AI tại Ây Ai, Ét Em I, chấm vi en.", "Học AI tại AI. SMI.VN", "vi")["score"] == 0.0
    assert asr.error_rate("S, M, E.", "F M E", "vi")["score"] > 0  # a wrong letter still fails
    assert asr.error_rate("Hà Nội đẹp lắm", "Hà Lội đẹp lắm", "vi")["score"] > 0.08  # real words untouched


def test_evaluation_runs_do_not_feed_lessons(monkeypatch):
    _fake_loop(monkeypatch, lambda a: (0.5, "sai"))
    S.synth_sentence("Câu đánh giá không học.", "vi", "ve", engine="vieneu", cache=False, learn=False)
    assert not [s for s in lessons.signals("qa_fail") if s["payload"]["text"] == "Câu đánh giá không học."]
