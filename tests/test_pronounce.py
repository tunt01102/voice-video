"""Pronunciation pre-scan (docs/SDD-subtitle-fit-pronunciation.md §5). Heuristics only — no LLM calls."""

from mediaforge.text import normalize as N
from mediaforge.text import pronounce as P

SCRIPT = ("Câu chuyện của PNJ hôm nay bắt đầu từ mô hình kim tự tháp Carroll. Slide này nói về livestream của KOL, "
          "các dấu hiệu V1, V2 và phát thải CO2. Theo chuẩn GRI, Ngân hàng Nhà nước đã thanh tra. "
          "Nguyễn Phương Hằng xuất hiện trên website. Trên slide tiếp theo là năm 2025.")


def terms(items):
    return {c["term"] for c in items}


def test_vietnamese_words_are_not_flagged():
    for w in ["ban", "Hà", "Nguyễn", "khuếch", "nghiêng", "quyền", "Trong", "kim", "thanh", "tra", "2025"]:
        assert P.reasons_for(w, "vi") == [], w


def test_risky_terms_are_flagged():
    assert P.reasons_for("PNJ", "vi") == ["acronym"]
    assert P.reasons_for("V1", "vi") == ["alnum"]
    assert "symbol" in P.reasons_for("T+120", "vi")
    assert P.reasons_for("GitHub", "vi") == ["camel"]
    for w in ["Carroll", "livestream", "website", "Darby", "Dybvig", "Perrow", "greenwashing"]:
        assert P.reasons_for(w, "vi") == ["foreign"], w
    assert P.reasons_for("Nguyễn", "en") == ["diacritics"]


def test_scan_finds_terms_with_context():
    found = P.scan(SCRIPT, "vi", use_llm=False)
    t = terms(found)
    assert {"PNJ", "Carroll", "livestream", "KOL", "V1", "V2", "CO2", "GRI", "website", "slide"} <= t
    assert "Slide" not in t  # merged with "slide"
    assert not t & {"Nguyễn", "Ngân", "hàng", "2025"}
    pnj = next(c for c in found if c["term"] == "PNJ")
    assert "PNJ" in pnj["context"] and pnj["say"] == "" and pnj["known"] is False


def test_answers_are_saved_and_reused():
    P.remember("vi", {"PNJ": "Pi En Di", "KOL": " "}, ["Carroll"])
    lex = N.load_lexicon(None)["vi"]
    assert lex["PNJ"]["say"] == "Pi En Di" and lex["PNJ"]["source"] == "manual" and lex["PNJ"]["origin"] == "prescan"
    assert "KOL" not in lex  # blank answer = no decision
    assert lex["Carroll"]["source"] == "verified" and lex["Carroll"]["say"] == "Carroll"
    found = {c["term"]: c for c in P.scan(SCRIPT, "vi", use_llm=False)}
    assert found["PNJ"]["known"] and found["PNJ"]["say"] == "Pi En Di"  # shown pre-filled
    assert "Carroll" not in found  # confirmed fine → not asked again
    assert N.apply_lexicon("của PNJ hôm nay", N.merged_lexicon(None, "vi")) == "của Pi En Di hôm nay"


def test_learning_never_overwrites_owner_answers():
    P.remember("vi", {"GRI": "Gi A Ai"}, ["website"])
    N.learn(None, "vi", "GRI", "gờ ri")
    N.learn(None, "vi", "website", "guép sai")
    lex = N.load_lexicon(None)["vi"]
    assert lex["GRI"]["say"] == "Gi A Ai" and lex["website"]["say"] == "website"


def test_voice_scope():
    P.remember("vi", {"CSR": "Xi Ét A"}, voice="v-test")
    assert N.load_lexicon("v-test")["vi"]["CSR"]["say"] == "Xi Ét A"
    assert "CSR" not in N.load_lexicon(None)["vi"]


PHRASES = ("Hôm nay mình dùng Claude Code để review pull request. Pull request thứ hai dùng slash command /init. "
           "Claude rất giỏi, còn AI agent thì sao? Gõ PNJ, API nhé.")


def test_multi_word_english_terms_are_one_term():
    found = {c["term"]: c for c in P.scan(PHRASES, "vi", use_llm=False)}
    assert {"Claude Code", "pull request", "slash command", "AI agent"} <= set(found)
    assert found["pull request"]["count"] == 2           # "Pull request" at a sentence start is the same term
    assert found["Claude Code"]["reasons"] == ["phrase"] and "Claude Code" in found["Claude Code"]["context"]
    assert found["Claude"]["count"] == 1                 # only the standalone "Claude", not the one in "Claude Code"
    assert "review" in found and "review pull request" not in found  # cut around the standalone "pull request"
    assert not {"Code", "slash", "command", "agent"} & set(found)


def test_punctuation_and_long_clauses_break_phrases():
    found = terms(P.scan("Dùng Claude, Code và GitHub. Make every simple test pass fast nhé.", "vi", use_llm=False))
    assert {"Claude", "Code", "GitHub"} <= found and "Claude, Code" not in found
    assert "Make" in found and not any(" " in t for t in found)  # a 6-word English clause stays as single words


def test_lexicon_phrase_splits_a_longer_run():
    N.save_lexicon({"vi": {"pull request": {"say": "pull request", "source": "english", "hits": 0}}}, "phr")
    found = terms(P.scan("Hãy review pull request này.", "vi", voice="phr", use_llm=False))
    assert {"review", "pull request"} <= found
    found = terms(P.scan("Hãy review pull request này.", "vi", use_llm=False))
    assert found == {"review pull request"}             # no other evidence: one term, ticked once


def test_job_terms_never_override_owner_answers():
    P.remember("vi", {"PNJ": "Pi En Di"}, ["Carroll"], english=["GitHub"], voice="jt")
    t = P.job_terms("vi", "jt", {"PNJ": "Pê En Giây", "KOL": "cây âu eo", "pnj": "x"}, ["Carroll", "ESG"],
                    ["GitHub", "Carroll", "slash command"])
    assert t["readings"] == {"KOL": "cây âu eo", "ESG": "ESG"} and t["english"] == ["slash command"]
    assert t["kept_owner"] == {"PNJ": {"owner": "Pi En Di", "request": "Pê En Giây"},
                               "pnj": {"owner": "Pi En Di", "request": "x"},
                               "Carroll": {"owner": "Carroll", "request": "tiếng Anh"}}
    assert "KOL" not in N.load_lexicon("jt")["vi"]                       # job-local: nothing saved
