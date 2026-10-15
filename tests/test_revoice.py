"""Revoice: a finished video read again in another voice — only the voice and compose stages run
(docs/SDD-review-upgrade.md §11.5). Models and FFmpeg are faked; build_visuals, cards, chapters and subtitles are real.
"visuals=0" means no picture or clip is *generated*: build_visuals still runs (reuse=True) to rebuild the scenes
with the new durations."""

import json
import re
import shutil
import wave
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from mediaforge import asr, db, jobs, llm, music, pipelines as P, revoice
from mediaforge import audio as A
from mediaforge import takes as T
from mediaforge.config import settings
from mediaforge.text import normalize as N
from mediaforge.text import pronounce
from mediaforge.video import clips, compose, slides
from mediaforge.voice import profiles

SECONDS = {"old": 0.5, "stock-x": 1.0, "blendy": 1.0, "friend": 1.0}  # per scene, so durations move


def wav_seconds(f) -> float:
    with wave.open(str(f)) as w:
        return w.getnframes() / w.getframerate()


@pytest.fixture
def env(monkeypatch):
    calls = Counter()
    composed = []
    clip_len = {}

    def plan(p, kind, *, style, seconds):
        calls["plan"] += 1
        data = {"title": "T", "description": "D", "cta": "Theo dõi nhé",
                "scenes": [{"narration": "Câu một về PNJ.", "visual_prompt": "a", "mood": "calm"},
                           {"narration": "Câu hai hay hơn.", "visual_prompt": "b", "mood": "tense", "chapter": "Phần hai"}]}
        P.lint_plan(data, "vi")
        return data, "vi"

    def speak(text, *, voice, out_dir, name, fx=None, engine=None, overrides=None, takes=None, regen=None, **kw):
        calls[f"speak:{voice}"] += 1
        calls["speak_with_edits"] += bool(overrides or takes or regen)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        wav = A.save(Path(out_dir) / f"{name}.wav", np.zeros(int(A.SR * SECONDS[voice]), dtype=np.float32))
        return {"audio": str(wav), "sentences": [{"text": text, "passed": True, "lang": "vi"}],
                "qa_failed_sentences": [], "voice": voice, "fx": {"pitch": (fx or {}).get("pitch", 0)}, "engine": engine}

    def gen_image(prompt, *, out_path, **kw):
        calls["image"] += 1
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"png")
        return {"image": str(out_path)}

    def gen_clip(image, prompt, out, *, seconds=4.0, **kw):
        calls["clip"] += 1
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"mp4")
        clip_len[str(out)] = 1.0  # LTX clips are fixed length
        return Path(out)

    def card(out, *a, **k):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"png")
        return Path(out)

    def comp(scenes, voice, out_path, *, subtitles_ass=None, music=None, **kw):
        calls["compose"] += 1
        composed.append({"scenes": [s.as_dict() for s in scenes], "voice": str(voice),
                         "ass": Path(subtitles_ass).read_text() if subtitles_ass else ""})
        Path(out_path).write_bytes(b"mp4")
        return Path(out_path)

    def probe(f):
        f = str(f)
        if f in clip_len:
            return clip_len[f]
        if f.endswith(".wav"):
            return wav_seconds(f)
        return 9.0

    def no_llm(*a, **k):
        calls["llm"] += 1
        raise AssertionError("no LLM call expected")

    monkeypatch.setattr(P, "make_plan", plan)
    monkeypatch.setattr(P.S, "speak", speak)
    monkeypatch.setattr(P.image, "generate", gen_image)
    monkeypatch.setattr(clips, "image_to_video", gen_clip)
    monkeypatch.setattr(clips, "available", lambda: True)
    monkeypatch.setattr(slides, "render_card", card)
    monkeypatch.setattr(llm, "complete_json", no_llm)
    def transcribe(wav, **k):  # words between the title and end cards, like Whisper hears them
        toks = "Câu một về PNJ. Câu hai hay hơn.".split()
        a, b = 2.5, wav_seconds(wav) - 3.0
        step = (b - a) / len(toks)
        return {"words": [asr.Word(t, a + i * step, a + (i + 1) * step) for i, t in enumerate(toks)]}

    monkeypatch.setattr(asr, "transcribe", transcribe)
    monkeypatch.setattr(compose, "compose", comp)
    monkeypatch.setattr(compose, "thumbnail", lambda v, o, at=1.0: Path(o))
    monkeypatch.setattr(A, "probe_duration", probe)
    monkeypatch.setattr(music, "resolve", lambda m: None)
    monkeypatch.setattr(P, "_gentle_motion", lambda kind: False)

    db.ensure()
    for vid, extra in (("old", {}), ("friend", {}), ("partner", {}),
                       ("stock-x", {"kind": "stock", "engine": "vieneu", "engines": {"vi": "vieneu", "en": "vieneu"}})):
        shutil.rmtree(settings.data_dir / "voices" / vid, ignore_errors=True)
        profiles.save_voice({"id": vid, "name": vid, "fx": {}, "engines": {"vi": "best"}, "samples": [{"id": "s"}],
                             "refs": {}, **extra})
    profiles.save_voice({"id": "blendy", "name": "blendy", "kind": "blend", "fx": {}, "engines": {"vi": "vieneu"},
                         "samples": [], "refs": {}, "blend": {"owner": "old", "partner": "partner"}})
    profiles.set_default_voice("old")
    return {"calls": calls, "composed": composed, "clip_len": clip_len}


def prog(p, m):
    pass


def source_video(params=None, *, jid=None) -> dict:
    """A finished narrated 16:9 video (title + end card, a chapter) in voice "old", stored as a done job."""
    p = {"kind": "narrated", "voice": "old", "review": [], **(params or {})}
    job = db.create_job("narrated", p)
    result = P.run_video(p, prog, job_id=job["id"])
    db.update_job(job["id"], status="done", result=result)
    return db.get_job(job["id"])


def run_revoice(src, voice="stock-x", **extra):
    job = db.create_job("revoice", {"job": src["id"], "voice": voice, **extra})
    return P.run("revoice", job["params"], prog, job_id=job["id"]), job["id"]


def srt_starts(f) -> list[str]:
    return re.findall(r"^(\d\d:\d\d:\d\d,\d\d\d) -->", Path(f).read_text(), re.M)


def test_revoice_runs_only_voice_and_compose(env):
    src = source_video()
    calls, composed = env["calls"], env["composed"]
    out = Path(src["result"]["video"]).parent
    before = dict(calls)
    old_subs = srt_starts(out / "subtitles.srt")
    res, rid = run_revoice(src)
    new = Counter({k: calls[k] - before.get(k, 0) for k in calls})
    assert new["plan"] == 0 and new["image"] == 0 and new["clip"] == 0 and new["llm"] == 0
    assert new["speak:stock-x"] == 2 and new["compose"] == 1 and new["speak:old"] == 0

    # Durations follow the new narration; cards keep their length and the end card moves to the new end.
    old_sc, new_sc = composed[0]["scenes"], composed[1]["scenes"]
    assert [s["card"] for s in new_sc] == ["title", None, None, "end"]
    assert [round(s["duration"], 2) for s in old_sc] == [2.5, 0.85, 0.5, 3.0]
    assert [round(s["duration"], 2) for s in new_sc] == [2.5, 1.35, 1.0, 3.0]
    assert composed[1]["voice"].endswith("voice.wav") and ".revoice-" in composed[1]["voice"]
    assert round(wav_seconds(out / "voice.wav"), 2) == 2.5 + 1.0 + 0.35 + 1.0 + 3.0
    assert [s["motion"] for s in new_sc[1:3]] == [s["motion"] for s in old_sc[1:3]]  # same moods → same moves

    # Chapter lower-third starts at intro + new scene-1 length; subtitles/karaoke use the new word timings.
    chap = lambda ass: re.search(r"Dialogue: \d+,(\d:\d\d:\d\d\.\d\d),[^\n]*Phần hai", ass).group(1)  # noqa: E731
    assert chap(composed[0]["ass"]) == "0:00:03.55" and chap(composed[1]["ass"]) == "0:00:04.05"
    new_subs = srt_starts(out / "subtitles.srt")
    assert new_subs != old_subs and new_subs[0] == "00:00:02,500"
    fresh = db.get_job(src["id"])
    words = fresh["result"]["words"]
    assert words[0]["start"] == pytest.approx(2.5) and words[-1]["end"] == pytest.approx(4.85)  # was 3.85
    assert fresh["result"]["voice_id"] == "stock-x" and fresh["params"]["voice"] == "stock-x"
    assert len(fresh["result"]["sentence_spans"]) == 2

    # The old render, voice and timings are in trash; the revoice job points to its source.
    trash = Path(res["trash"])
    assert (trash / "final.mp4").exists() and (trash / "voice.wav").exists() and (trash / "voice").is_dir()
    assert (trash / "subtitles.srt").exists() and (trash / "voice.json").exists()
    assert res["source_job"] == src["id"] and res["to"]["engine"] == "vieneu" and res["from"]["voice"] == "old"
    assert not list(out.glob(".revoice-*")) and fresh["result"]["report"]["revoiced"][-1]["revoice_job"] == rid
    assert "resubtitle" not in fresh["result"]["report"]


def test_old_voice_edits_are_dropped_and_readings_kept(env):
    pronounce.remember("vi", {"PNJ": "Pê En Giây"}, voice="old")
    src = source_video({"overrides": {"Câu hai hay hơn.": "/x/rec.wav"}, "pronunciations": {"PNJ": "Pê En Giây"},
                        "lexicon_voice": "old"})
    r = src["result"]
    r["sentences"][0]["takes"] = [{"id": "t1", "passed": True, "readings": {"PNJ": "Pê En Giây"}}]
    T.save_result(src["id"], r)
    out = Path(r["video"]).parent
    (out / "takes" / "000").mkdir(parents=True)
    before = env["calls"]["speak_with_edits"]
    res, _ = run_revoice(db.get_job(src["id"]), voice="stock-x")
    assert env["calls"]["speak_with_edits"] == before  # no take or recording of the old voice reaches the new one
    assert res["dropped"]["takes"] == [{"index": 0, "text": "Câu một về PNJ.", "takes": 1}]
    assert res["dropped"]["recordings"] == ["Câu hai hay hơn."]
    assert res["readings_kept"] == {"PNJ": "Pê En Giây"} and res["readings_saved"] == {"PNJ": "Pê En Giây"}
    assert N.merged_lexicon("stock-x", "vi")["PNJ"] == "Pê En Giây"
    fresh = db.get_job(src["id"])
    assert "overrides" not in fresh["params"] and not any(s.get("takes") for s in fresh["result"]["sentences"])
    assert (Path(res["trash"]) / "takes" / "000").is_dir() and not (out / "takes").exists()


def test_fixed_clips_are_cut_held_or_looped_with_a_warning(env, monkeypatch):
    src = source_video({"clips": True})
    assert env["calls"]["clip"] == 2
    monkeypatch.setattr(clips, "available", lambda: False)  # LTX gone: the clips made earlier are reused
    env["clip_len"].update({k: v for k, v in zip(sorted(env["clip_len"]), (1.5, 0.4))})
    res, _ = run_revoice(src)
    assert env["calls"]["clip"] == 2
    scenes = env["composed"][-1]["scenes"]
    assert [s["kind"] for s in scenes] == ["image", "video", "video", "image"]
    # Narration scene 0: 1.35 s of voice on a 1.5 s clip is within tolerance; scene 1: 1.0 s on 0.4 s → hold.
    assert [(f["scene"], f["mode"]) for f in res["clip_fit"]] == [(1, "hold")]
    assert scenes[2]["fill"] == "hold"
    env["clip_len"].update({k: v for k, v in zip(sorted(env["clip_len"]), (2.0, 0.4))})
    res, _ = run_revoice(db.get_job(src["id"]), voice="old")  # back to the shorter voice: 0.85 s on a 2 s clip
    assert [(f["scene"], f["mode"]) for f in res["clip_fit"]] == [(0, "cut")]  # scene 1: 0.5 s on 0.4 s is within tolerance
    assert res["warnings"] and "cắt" in res["warnings"][0]
    fresh = db.get_job(src["id"])["result"]
    assert fresh["report"]["clip_fit"] == res["clip_fit"]


def test_auto_music_keeps_the_track_run_video_picked(env, monkeypatch):
    monkeypatch.setattr(P, "auto_music", lambda mood, **k: "track-1")
    resolved = []
    monkeypatch.setattr(music, "resolve", lambda m: resolved.append(m) or None)
    monkeypatch.setattr(music, "credit", lambda m: None)
    src = source_video({"music": "auto"})
    assert src["result"]["report"]["music_id"] == "track-1"
    run_revoice(src)
    assert resolved[-1] == "track-1"  # not the literal "auto" (ffmpeg: "Error opening input file auto")


def test_legacy_video_without_words_or_cards(env):
    """Shorts made before word timings and cards were stored: revoice must not add cards or re-plan."""
    src = source_video({"kind": "short", "cards": False})
    r = src["result"]
    plan = json.loads(Path(r["plan"]).read_text())
    plan.pop("cards")
    Path(r["plan"]).write_text(json.dumps(plan))
    r.pop("words")
    T.save_result(src["id"], r)
    res, _ = run_revoice(db.get_job(src["id"]))
    assert env["calls"]["plan"] == 1 and [s["card"] for s in env["composed"][-1]["scenes"]] == [None, None]
    assert db.get_job(src["id"])["result"]["words"] and "cards" not in json.loads(Path(r["plan"]).read_text())
    assert res["duration"]["after"] is not None


def test_fit_clips_modes(monkeypatch):
    monkeypatch.setattr(A, "probe_duration", lambda f: 3.0)
    vis = [compose.Scene("a.mp4", 2.0, kind="video"), compose.Scene("b.mp4", 4.0, kind="video"),
           compose.Scene("c.mp4", 8.0, kind="video"), compose.Scene("d.mp4", 3.1, kind="video"),
           compose.Scene("e.png", 9.0)]
    report = {}
    P.fit_clips(vis, report)
    assert [(f["scene"], f["mode"]) for f in report["clip_fit"]] == [(0, "cut"), (1, "hold"), (2, "loop")]
    assert [v.fill for v in vis[:4]] == ["loop", "hold", "loop", "loop"] and len(report["warnings"]) == 3


def test_render_segment_hold_does_not_loop(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(A, "run_ffmpeg", lambda args: seen.append(args))
    compose.render_segment(compose.Scene("c.mp4", 4.0, kind="video", fill="hold"), 4.0, tmp_path / "a.mp4", 64, 64, 30, "zoom_in")
    compose.render_segment(compose.Scene("c.mp4", 4.0, kind="video"), 4.0, tmp_path / "b.mp4", 64, 64, 30, "zoom_in")
    assert "-stream_loop" not in seen[0] and "tpad=stop_mode=clone" in " ".join(seen[0])
    assert seen[1][:2] == ["-stream_loop", "-1"]


def test_interrupted_revoice_resumes_without_resynthesis(env, monkeypatch):
    src = source_video()
    real = P.recompose

    def die(*a, **k):
        raise SystemExit("server died")  # not an Exception: like a killed process, nothing is cleaned up

    monkeypatch.setattr(P, "recompose", die)
    job = db.create_job("revoice", {"job": src["id"], "voice": "stock-x"})
    with pytest.raises(SystemExit):
        P.run("revoice", job["params"], prog, job_id=job["id"])
    out = Path(src["result"]["video"]).parent
    assert (out / f".revoice-{job['id']}" / "voice.json").exists()
    assert db.get_job(src["id"])["result"]["voice_id"] == "old"  # the source is untouched until the swap
    assert "revoice" in jobs.RESUMABLE and jobs.PRIORITY["revoice"] == 1
    monkeypatch.setattr(P, "recompose", real)
    speaks = env["calls"]["speak:stock-x"]
    res = P.run("revoice", job["params"], prog, job_id=job["id"])
    assert env["calls"]["speak:stock-x"] == speaks  # the voice stage was not redone
    assert res["seconds"]["voice"] is not None and db.get_job(src["id"])["result"]["voice_id"] == "stock-x"


def test_failed_compose_leaves_the_source_untouched(env, monkeypatch):
    src = source_video()
    monkeypatch.setattr(compose, "compose", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg")))
    with pytest.raises(RuntimeError):
        run_revoice(src)
    out = Path(src["result"]["video"]).parent
    fresh = db.get_job(src["id"])
    assert fresh["result"]["voice_id"] == "old" and (out / "final.mp4").exists() and (out / "voice").is_dir()
    assert not list(out.glob(".revoice-*"))


def test_consent_and_voice_rules(env):
    assert revoice.check_voice("stock-x")["kind"] == "stock"
    assert revoice.check_voice("old")["id"] == "old"                     # the owner's default voice
    with pytest.raises(PermissionError):
        revoice.check_voice("friend")                                    # someone else, no consent recorded
    with pytest.raises(PermissionError):
        revoice.check_voice("blendy")                                    # blend partner has no consent
    profiles.update_voice("partner", {"consent": {"kind": "written", "by": "Partner"}})
    assert revoice.check_voice("blendy")["kind"] == "blend"
    profiles.update_voice("friend", {"consent": {"kind": "written", "by": "Friend"}})
    assert revoice.check_voice("friend")["id"] == "friend"
    with pytest.raises(KeyError):
        revoice.check_voice("nobody")
    stock = profiles.get_voice("stock-x")
    assert revoice.target_engine(stock, "voxcpm", "vi") == "vieneu"     # a stock voice has one engine, no "best"
    with pytest.raises(ValueError):
        revoice.target_engine(profiles.get_voice("old"), "nope", "vi")


def test_rest_and_estimate(env):
    from fastapi.testclient import TestClient

    from mediaforge.api import create_app

    src = source_video()
    client = TestClient(create_app())
    assert client.post(f"/api/jobs/{src['id']}/revoice", json={"voice": "friend"}).status_code == 403
    assert client.post(f"/api/jobs/{src['id']}/revoice", json={"voice": "nobody"}).status_code == 404
    assert client.post(f"/api/jobs/{src['id']}/revoice", json={}).status_code == 400
    est = client.get(f"/api/jobs/{src['id']}/revoice/estimate", params={"voice": "stock-x"}).json()
    assert est["engine"] == "vieneu" and est["seconds"] > 0
    r = client.post(f"/api/jobs/{src['id']}/revoice", json={"voice": "stock-x", "fx": {"pitch": -1}}).json()
    assert r["job"]["type"] == "revoice" and r["job"]["params"] == {"job": src["id"], "voice": "stock-x", "fx": {"pitch": -1}}
    assert r["estimate"]["seconds"] == est["seconds"]
    db.update_job(r["job"]["id"], status="cancelled")  # nothing runs it here
    img = db.create_job("image", {"prompt": "x"})
    db.update_job(img["id"], status="done", result={"images": []})
    assert client.post(f"/api/jobs/{img['id']}/revoice", json={"voice": "stock-x"}).status_code == 409
