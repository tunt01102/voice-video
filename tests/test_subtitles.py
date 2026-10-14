"""Subtitle layout: every line fits the frame (docs/SDD-subtitle-fit-pronunciation.md §3). No models are loaded."""

import re

import pytest

from mediaforge import subtitles as S

# Real-world shapes that used to overflow the 16:9 frame (one 84-char line), plus spoken numbers and acronyms.
SENTENCES = [
    "Trước hết, xin thừa nhận PNJ có số liệu thật, và nhóm không muốn đánh giá một chiều.",
    "Nhưng vết nứt nằm ở hai tầng dưới, nơi tầng pháp lý đã xuất hiện những dấu hiệu đáng chú ý, được nhóm ký hiệu là V1, V2 và V5.",
    "Về môi trường, phát thải năm hai nghìn không trăm hai mươi lăm là mười bảy nghìn năm trăm chín mươi tấn CO2 tương đương, chưa giảm so với năm gốc hai nghìn không trăm hai mươi ba.",
    "Theo chuẩn GRI bốn một bảy, hai năm liền công ty báo cáo không phát hiện vụ việc nào.",
    "WWW MMM NHNN GRI KOL CSR WWW MMM NHNN GRI KOL CSR WWW MMM NHNN GRI KOL CSR.",
    "Ngắn thôi.",
    "Xem https://example.com/a-very-long-path/that-cannot-be-broken-anywhere-at-all-because-it-is-one-token ngay.",
]
FRAMES = [(1920, 1080), (1080, 1920), (1080, 1080)]


def timed(sentences, wps=3.2):
    words, t = [], 0.0
    for s in sentences:
        for w in s.split():
            d = 0.12 + 0.03 * len(w)
            words.append({"text": w, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + 0.05
        t += 0.4
    return words


@pytest.mark.parametrize("width,height", FRAMES)
@pytest.mark.parametrize("karaoke", [False, True])
def test_every_line_fits(width, height, karaoke):
    words = timed(SENTENCES)
    layout = S.Layout.for_frame(width, height, karaoke=karaoke)
    cues = S.build_cues(words, layout=layout)
    assert [w["text"] for c in cues for w in c.words] == [w["text"] for w in words]  # nothing lost or reordered
    for c in cues:
        assert 1 <= len(c.lines) <= layout.max_lines
        assert " ".join(c.lines).split() == [w["text"] for w in c.words]
        for ln in c.lines:
            toks = ln.split()
            if len(toks) == 1:
                continue  # a lone over-wide word is shrunk at draw time (see test_long_word_is_shrunk)
            assert len(ln) <= layout.max_chars, ln
            assert layout.line_px(toks) <= layout.safe_px, ln


def test_long_sentence_is_split_not_left_on_one_line():
    cues = S.build_cues(timed([SENTENCES[0]]), layout=S.Layout.for_frame(1920, 1080))
    assert all(len(ln) <= 42 for c in cues for ln in c.lines)
    # breaks at the comma, not mid-phrase
    assert any(ln.endswith("thật,") for c in cues for ln in c.lines)


def test_no_orphan_one_word_cues():
    cues = S.build_cues(timed(SENTENCES[:4]), layout=S.Layout.for_frame(1920, 1080))
    assert not [c for c in cues if len(c.words) == 1]


def test_cues_never_overlap():
    cues = S.build_cues(timed(SENTENCES), layout=S.Layout.for_frame(1920, 1080))
    for a, b in zip(cues, cues[1:]):
        assert a.end < b.start


def test_ass_karaoke_and_safety_net(tmp_path):
    words = timed(SENTENCES[:2])
    files = S.write_all(words, tmp_path / "subs", width=1920, height=1080)
    ass = (tmp_path / "subs.ass").read_text("utf-8")
    assert "WrapStyle: 0" in ass  # libass wraps instead of clipping if a line were ever too wide
    dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert dialogue and all("\\t(" in d and "\\1c&H00D4FF&" in d for d in dialogue)  # per-word highlight
    # every \t window is inside its cue
    for d in dialogue:
        h0, m0, s0 = d.split(",")[1].split(":")
        h1, m1, s1 = d.split(",")[2].split(":")
        dur_ms = ((int(h1) - int(h0)) * 3600 + (int(m1) - int(m0)) * 60 + float(s1) - float(s0)) * 1000
        starts = [int(a) for a, _ in re.findall(r"\\t\((\d+),(\d+),\\1c&H00D4FF", d)]
        assert starts == sorted(starts) and max(starts) < dur_ms
    srt = open(files["srt"], encoding="utf-8").read()
    assert "\\t(" not in srt and "{" not in srt  # SRT stays plain text


def test_static_ass_when_karaoke_off(tmp_path):
    S.write_all(timed(SENTENCES[:1]), tmp_path / "subs", karaoke=False)
    ass = (tmp_path / "subs.ass").read_text("utf-8")
    assert "\\t(" not in ass


def test_long_word_is_shrunk(tmp_path):
    words = timed([SENTENCES[-1]])
    S.write_all(words, tmp_path / "subs", width=1080, height=1920, karaoke=False)
    ass = (tmp_path / "subs.ass").read_text("utf-8")
    assert re.search(r"\\fscx(\d+)", ass) and int(re.search(r"\\fscx(\d+)", ass).group(1)) < 100


def test_wrap_words_never_returns_an_overflowing_line():
    layout = S.Layout.for_frame(1920, 1080)
    toks = SENTENCES[0].split()
    lines = S.wrap_words(toks, layout)
    assert lines is None or all(layout.line_fits(ln.split()) for ln in lines)
