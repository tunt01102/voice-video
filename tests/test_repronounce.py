"""Fix pronunciation of a finished job (no models are loaded: rerender and export are stubbed)."""

import unicodedata

import pytest

from mediaforge import asr, db, export, repronounce as R, takes as T
from mediaforge.text import normalize as N

SENTS = [
    {"text": "Câu chuyện của PNJ hôm nay.", "lang": "vi"},
    {"text": "Theo chuẩn GRI bốn một bảy.", "lang": "vi"},
    {"text": "Pháp lý là tầng dưới.", "lang": "vi"},
    {"text": "Kết luận pháp lý, và PNJs không phải PNJ.", "lang": "vi"},
    {"text": "Không có gì ở đây, pnj viết thường.", "lang": "vi"},
]


def test_occurrences_follow_the_tts_matcher():
    hits = R.occurrences(SENTS, ["PNJ", "GRI", "pháp lý", "XYZ"])
    # Capitalised terms are case-sensitive and whole-word ("PNJs" and "pnj" are not PNJ);
    # a lower-case term matches any case ("Pháp lý" at the start of a sentence).
    assert hits == {"PNJ": [0, 3], "GRI": [1], "pháp lý": [2, 3], "XYZ": []}
    for t, idx in hits.items():
        for i, s in enumerate(SENTS):
            assert (N.apply_lexicon(s["text"], {t: "@@"}) != s["text"]) == (i in idx)


def test_occurrences_match_decomposed_input():
    nfd = unicodedata.normalize("NFD", "pháp lý")
    assert R.occurrences(SENTS, [nfd])[nfd] == [2, 3]


def test_clean_readings_drops_blanks_and_noops():
    assert R.clean_readings({" PNJ ": " Pi En Di ", "GRI": "", "": "x", "KOL": "KOL"}) == {"PNJ": "Pi En Di"}


def test_lenient_terms_keep_the_sentence_case():
    # QA must forgive "pháp lí" for "Pháp lý" even though the lexicon term is lower-case.
    lex = {"pháp lý": "pháp lí"}
    s = SENTS[2]["text"]
    found = {m.group(0) for k in N.terms_in(s, lex) for m in N.term_pattern(k).finditer(s)}
    assert found == {"Pháp lý"}
    qa = asr.error_rate(s, "pháp lí là tầng dưới", "vi", asr.lenient_terms(s, found))
    assert qa["score"] == 0.0


def test_save_readings_overrides_manual_and_voice_shadow(tmp_path):
    from mediaforge.text import pronounce

    pronounce.remember("vi", {"PNJ": "Pi Ên Di"})                       # an older manual reading
    vid = "voice-x"
    pronounce.remember("vi", {"PNJ": "pê en giây"}, voice=vid)         # would shadow the global one
    R.save_readings({"PNJ": "Pi En Di"}, {"vi"}, vid)
    assert N.load_lexicon(None)["vi"]["PNJ"]["say"] == "Pi En Di"
    assert N.load_lexicon(None)["vi"]["PNJ"]["origin"] == "repronounce"
    assert N.merged_lexicon(vid, "vi")["PNJ"] == "Pi En Di"
    R.save_readings({"GRI": "Gi A AI"}, {"vi"}, vid)                   # no voice entry → voice file untouched
    assert "GRI" not in N.load_lexicon(vid)["vi"]


@pytest.fixture
def finished_job(tmp_path):
    db.init()
    job = db.create_job("narrated", {"voice": None})
    sents = [dict(s, takes=[]) for s in SENTS]
    sents[4]["takes"] = [{"id": "t1-old", "passed": True}]
    result = {"video": str(tmp_path / "final.mp4"), "voice": str(tmp_path / "voice.wav"), "sentences": sents}
    db.update_job(job["id"], status="done", result=result)
    return job["id"]


def test_run_rereads_every_occurrence_and_exports(finished_job, monkeypatch):
    calls, exported = [], {}

    def fake_rerender(p, progress, job_id=None):
        calls.append(p)
        progress(0.5, "x")
        progress(1.0, "done")
        take = {"id": f"t-{p['index']}", "score": 0.3 if p["index"] == 1 else 0.0, "passed": p["index"] != 1,
                "heard": "…", "source": p["source"], "readings": p["readings"]}
        job = T.load_done_job(p["job"])
        r = job["result"]
        r["sentences"][p["index"]]["takes"].append(take)
        T.save_result(p["job"], r)
        return {"take": take}

    def fake_export(p, progress, job_id=None):
        exported.update(p)
        return {"file": "/x/out.mp4", "export": {"id": "e1", "path": "remux"}}

    monkeypatch.setattr(T, "rerender", fake_rerender)
    monkeypatch.setattr(export, "run", fake_export)
    seen = []
    res = R.run({"job": finished_job, "readings": {"PNJ": "Pi En Di", "GRI": "Gi A AI", "KOL": "Cây Ô En"}},
                lambda f, m: seen.append(f))

    assert [c["index"] for c in calls] == [0, 1, 3]
    assert calls[2]["readings"] == {"PNJ": "Pi En Di"} and calls[0]["source"] == "repronounce"
    # New takes are selected even when QA is unsure (sentence 1); earlier passed takes are kept.
    assert exported["selection"] == {0: "t-0", 1: "t-1", 3: "t-3", 4: "t1-old"}
    assert exported["format"] == "mp4"
    assert res["not_found"] == ["KOL"] and res["path"] == "remux"
    assert seen == sorted(seen) and seen[-1] == 1.0
    stored = T.load_done_job(finished_job)["result"]["repronounced"][-1]
    assert [s["index"] for s in stored["sentences"]] == [0, 1, 3]
    assert "KOL" not in N.load_lexicon(None)["vi"]          # a term that is not in the job is not saved


def test_run_rejects_terms_not_in_the_job(finished_job):
    with pytest.raises(ValueError):
        R.run({"job": finished_job, "readings": {"XYZ": "ích"}}, lambda f, m: None)
    with pytest.raises(ValueError):
        R.run({"job": finished_job, "readings": {}}, lambda f, m: None)
