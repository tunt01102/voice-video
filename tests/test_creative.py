"""Creative direction (docs/SDD-review-upgrade.md §5.4): art direction, mood-driven motion, cards, auto music."""

from pathlib import Path

from mediaforge import lessons, music, pipelines as P
from mediaforge.video import compose, slides


def test_art_direction_reaches_every_prompt():
    ad = {"motif": "a small chibi fox in a red scarf", "camera": "low angle, shallow depth of field",
          "palette": ["deep navy", "amber"]}
    out = P.art_prompt("a fox locking a giant padlock.", ad)
    assert out.startswith("a fox locking a giant padlock") and "red scarf" in out and "deep navy, amber" in out
    assert P.art_prompt("plain", None) == "plain"


def test_motion_follows_mood_and_never_repeats():
    scenes = [compose.Scene(f"{i}.png", 3.0) for i in range(6)]
    compose.assign_styles(scenes, ["tense", "tense", "tense", "calm", None, "triumphant"])
    motions = [s.motion for s in scenes]
    assert all(a != b for a, b in zip(motions, motions[1:]))
    assert scenes[1].transition == "fadeblack" and scenes[0].transition is None
    assert scenes[3].motion in compose.MOOD_STYLE["calm"][0]
    gentle = [compose.Scene(f"{i}.png", 3.0) for i in range(4)]
    compose.assign_styles(gentle, ["urgent"] * 4, gentle=True)
    assert {s.transition for s in gentle[1:]} <= {"fade", "dissolve"}


def test_owner_motion_override_is_kept():
    scenes = [compose.Scene("a.png", 3, motion="static", transition="slideup"), compose.Scene("b.png", 3)]
    compose.assign_styles(scenes, ["calm", "calm"])
    assert scenes[0].motion == "static"


def test_cards_on_for_16_9_and_end_card_needs_cta_or_credit(monkeypatch):
    monkeypatch.setattr(music, "credit", lambda m: "Music: X (CC BY)" if m else None)
    c = P.card_plan({}, {"title": "T", "cta": ""}, kind="narrated", aspect="16:9", music_id=None)
    assert c["intro"] == 2.5 and c["outro"] == 0.0
    assert P.card_plan({}, {"title": "T", "cta": "Theo dõi kênh"}, kind="narrated", aspect="16:9", music_id=None)["outro"] == 3.0
    assert P.card_plan({}, {"title": "T"}, kind="narrated", aspect="16:9", music_id="m")["outro"] == 3.0
    assert P.card_plan({}, {"title": "T"}, kind="short", aspect="9:16", music_id="m")["intro"] == 0.0
    assert P.card_plan({"cards": False}, {"title": "T"}, kind="narrated", aspect="16:9", music_id=None)["intro"] == 0.0


def test_add_cards_wraps_the_scenes(tmp_path, monkeypatch):
    monkeypatch.setattr(slides, "render_card", lambda out, *a, **k: Path(out))
    plan = {"cards": {"intro": 2.5, "outro": 3.0, "title": "T", "cta": "Hẹn gặp lại", "credit": ""}}
    vis = P.add_cards({}, plan, tmp_path, [compose.Scene("a.png", 4.0), compose.Scene("b.png", 4.0)], width=1920, height=1080)
    assert [v.card for v in vis] == ["title", None, None, "end"] and vis[0].duration == 2.5
    board = P.storyboard({"scenes": [{"narration": "a"}, {"narration": "b"}]}, tmp_path, [v.as_dict() for v in vis])
    assert [b["image"] for b in board] == ["a.png", "b.png"]  # cards are not storyboard scenes


def test_auto_music_matches_mood_and_skips_disliked(monkeypatch):
    tracks = [{"id": "p1", "mood": "piano", "available": True}, {"id": "p2", "mood": "piano", "available": True},
              {"id": "a1", "mood": "ambient", "available": True}]
    monkeypatch.setattr(music, "list_tracks", lambda: tracks)
    assert P.auto_music("ambient", kind="narrated", seed="x") == "a1"
    for jid in ("m1", "m2"):
        lessons.rate({"id": jid, "type": "narrated", "params": {"music": "p1"}, "result": {"report": {"kind": "narrated", "music_id": "p1"}}},
                     1, ["nhạc"])
    picks = {P.auto_music("piano", kind="narrated", seed=s) for s in "abcdefgh"}
    assert picks == {"p2"}


def test_chapters_are_overlaid_after_the_title_card(tmp_path):
    ass = tmp_path / "subtitles.ass"
    ass.write_text("[Script Info]\n\n[V4+ Styles]\nFormat: Name\nStyle: Default,Arial\n\n[Events]\nFormat: Layer\n")
    plan = {"cards": {"intro": 2.5}, "scenes": [{"chapter": "Kiểm tra người gửi"}, {}, {"chapter": "Gọi xác nhận"}]}
    out = P.chapter_ass(str(ass), plan, [5.0, 4.0, 6.0], tmp_path, height=1080)
    text = Path(out).read_text()
    assert "Style: Chapter" in text and "0:00:02.70" in text and "0:00:11.70" in text and "Gọi xác nhận" in text
    assert "Chapter" not in ass.read_text()  # exported subtitles untouched
    assert P.chapter_ass(str(ass), {"scenes": [{}]}, [3.0], tmp_path, height=1080) == str(ass)


def test_old_style_render_drops_the_art_direction(monkeypatch):
    monkeypatch.setattr(P, "plan", lambda brief, **kw: {"title": "T", "art_direction": {"motif": "a red fox"},
                                                        "scenes": [{"narration": "Một.", "visual_prompt": "a hill"}]})
    data, _ = P.make_plan({"brief": "x", "lang": "vi", "art_direction": False}, "narrated", style="cinematic", seconds=20)
    assert data["art_direction"] == {} and P.art_prompt("a hill", data["art_direction"]) == "a hill"
    data, _ = P.make_plan({"brief": "x", "lang": "vi"}, "narrated", style="cinematic", seconds=20)
    assert data["art_direction"]["motif"] == "a red fox"
