"""Lessons: what MediaForge learned from its mistakes and from the owner's judgement.

docs/SDD-review-upgrade.md §5.1. A lesson is a durable, text-only fact with provenance and a status:
candidate (shown for review, does nothing) → active (applied to later jobs) → retired (kept, restorable).
Promotion is deterministic Python; the LLM never decides what is learned.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import unicodedata
from typing import Any, Iterable

from mediaforge import db
from mediaforge.config import settings

log = logging.getLogger(__name__)

KINDS = ("reading", "judge_accept", "lint", "engine_route", "visual_pref", "motion_pref", "music_pref", "plan_rule", "ops")
STATUSES = ("candidate", "active", "retired")
# A reading must win in this many different sentences (same voice, language, engine) before it is applied.
READING_EVIDENCE = 2
# Retire an applied lesson that does not help: after this many uses with a win rate below RETIRE_WIN_RATE.
RETIRE_AFTER_USES = 6
RETIRE_WIN_RATE = 0.5
CLAUSE_PUNCT = set(",;:.!?…–")
# Common English words a respelling must not hijack (EN gate rule 3); capitalised mid-sentence words are names.
EN_COMMON = set("""a an the and or but for nor so yet of to in on at by with from up as is are was were be been being
it its this that these those i you he she we they me him her us them my your his our their not no do does did have
has had will would can could should may might must shall""".split())


def _now() -> float:
    return time.time()


def _scope_json(scope: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in sorted(scope.items()) if v not in (None, "")}, ensure_ascii=False)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip()


def lesson_id(kind: str, scope: dict[str, Any], key: str) -> str:
    return hashlib.sha1(f"{kind}|{_scope_json(scope)}|{_nfc(key)}".encode()).hexdigest()[:12]


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["scope"] = json.loads(d["scope"])
    d["value"] = json.loads(d["value"])
    return d


# ------------------------------------------------------------------ storage

def get(lid: str) -> dict[str, Any] | None:
    db.ensure()
    with db.connect() as c:
        r = c.execute("SELECT * FROM lessons WHERE id=?", (lid,)).fetchone()
    return _row(r) if r else None


def list_lessons(status: str | None = None, kind: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    db.ensure()
    q, args = "SELECT * FROM lessons WHERE 1=1", []
    if status:
        q += " AND status=?"
        args.append(status)
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with db.connect() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def _matches(scope: dict[str, Any], want: dict[str, Any]) -> bool:
    """A lesson applies when every field it is scoped to equals the request's value."""
    return all(want.get(k) == v for k, v in scope.items())


def active(lesson_kind: str, **want: Any) -> list[dict[str, Any]]:
    return [l for l in list_lessons("active", lesson_kind) if _matches(l["scope"], want)]


def upsert(kind: str, scope: dict[str, Any], key: str, value: dict[str, Any], *, origin: str, status: str = "candidate",
           evidence: int = 1, contra: int = 0, note: str | None = None, merge=None) -> dict[str, Any]:
    """Create a lesson or add evidence to it. merge(old_value, new_value) → value; default: keep the new value."""
    if kind not in KINDS:
        raise ValueError(f"unknown lesson kind {kind!r}")
    db.ensure()
    lid, now = lesson_id(kind, scope, key), _now()
    old = get(lid)
    if old:
        val = merge(old["value"], value) if merge else {**old["value"], **value}
        # Never resurrect what the owner retired, and never demote what the owner pinned.
        new_status = old["status"] if old["status"] == "retired" or old.get("origin") == "owner" else status
        if old["status"] == "active" and new_status == "candidate":
            new_status = "active"
        with db.connect() as c:
            c.execute("UPDATE lessons SET value=?, status=?, evidence=evidence+?, contra=contra+?, note=COALESCE(?, note), "
                      "updated_at=? WHERE id=?",
                      (json.dumps(val, ensure_ascii=False), new_status, evidence, contra, note, now, lid))
    else:
        with db.connect() as c:
            c.execute("INSERT INTO lessons(id,kind,scope,key,value,status,evidence,contra,origin,note,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (lid, kind, _scope_json(scope), _nfc(key), json.dumps(value, ensure_ascii=False), status, evidence,
                       contra, origin, note, now, now))
    return get(lid)  # type: ignore[return-value]


def set_status(lid: str, status: str, note: str | None = None, *, by_owner: bool = True) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if not get(lid):
        raise KeyError(f"lesson {lid} not found")
    with db.connect() as c:
        c.execute("UPDATE lessons SET status=?, note=COALESCE(?, note), updated_at=?" +
                  (", origin='owner'" if by_owner and status != "retired" else "") + " WHERE id=?",
                  (status, note, _now(), lid))
    return get(lid)  # type: ignore[return-value]


def record_use(ids: Iterable[str], won: bool) -> None:
    """An active lesson was applied; won = the sentence/scene passed on its first attempt. Retires lessons that do
    not help (wins/uses below RETIRE_WIN_RATE after RETIRE_AFTER_USES uses), unless the owner pinned them."""
    ids = list(ids)
    if not ids:
        return
    now = _now()
    with db.connect() as c:
        for lid in ids:
            c.execute("UPDATE lessons SET uses=uses+1, wins=wins+?, last_used_at=? WHERE id=?", (int(won), now, lid))
        c.execute(f"UPDATE lessons SET status='retired', note='không giúp được: thắng < {int(RETIRE_WIN_RATE * 100)} %', "
                  f"updated_at=? WHERE status='active' AND COALESCE(origin,'')<>'owner' AND uses>=? "
                  f"AND CAST(wins AS REAL)/uses < ? AND id IN ({','.join('?' * len(ids))})",
                  (now, RETIRE_AFTER_USES, RETIRE_WIN_RATE, *ids))


def signal(kind: str, payload: dict[str, Any], job_id: str | None = None) -> None:
    """Raw evidence (QA failure, repair outcome, incident, rating, …). Never fails the caller."""
    try:
        db.ensure()
        with db.connect() as c:
            c.execute("INSERT INTO signals(job_id,kind,payload,created_at) VALUES(?,?,?,?)",
                      (job_id, kind, json.dumps(payload, ensure_ascii=False, default=str), _now()))
    except Exception as e:  # noqa: BLE001
        log.warning("could not record signal %s: %s", kind, e)


def signals(kind: str | None = None, since: float = 0.0, limit: int = 5000) -> list[dict[str, Any]]:
    db.ensure()
    q, args = "SELECT * FROM signals WHERE created_at>=?", [since]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db.connect() as c:
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in c.execute(q, args).fetchall()]


# ------------------------------------------------------------------ readings (pronunciation)

def _ascii(s: str) -> str:
    from mediaforge.text.pronounce import _ascii as a

    return " ".join(a(s).split())


def reading_gate(term: str, say: str, lang: str, voice: str | None = None) -> tuple[bool, str]:
    """May `term → say` be learned as a reading? (SDD-review-upgrade §5.1.4, rules 1–5; rule 6 is the
    counterfactual synthesis done by the caller, rule 7 is the scope.) Returns (ok, reason)."""
    from mediaforge.text import normalize as N
    from mediaforge.text.pronounce import TOKEN, is_vi_syllable

    t, s = _nfc(term), _nfc(say)
    if not t or not s:
        return False, "trống"
    if t.casefold() == s.casefold():
        return False, "1: cách đọc giống hệt từ gốc"
    tokens = t.split()
    if len(tokens) > 3 or any(c in CLAUSE_PUNCT for c in t):
        return False, "2: khoá dài như một mệnh đề, sẽ không khớp lại"
    words = [w for w in TOKEN.findall(t) if any(ch.isalpha() for ch in w)]
    if lang == "vi":
        if words and all(is_vi_syllable(w) and not w.isupper() for w in words):
            return False, "3: từ tiếng Việt viết đúng không bao giờ phải đọc lại — lỗi là của engine/ASR"
        if _ascii(t) == _ascii(s) and not t.isupper():  # "SAP → sáp" is a real reading, "đổ → đỗ" is not
            return False, "4: chỉ đổi dấu thanh"
    elif lang == "en":
        if all(w.lower() in EN_COMMON for w in words):
            return False, "3: từ tiếng Anh thông dụng"
    for scope_voice in {None, voice}:
        entry = N.load_lexicon(scope_voice).get(lang, {}).get(t)
        if entry and entry.get("source") in ("manual", "verified", "default"):
            if _nfc(entry["say"]).casefold() == s.casefold():
                return False, "5: đã có trong từ điển"
            return False, "5: trái với cách đọc bạn đã đặt"
    return True, "ok"


def propose_reading(voice: str | None, lang: str, engine: str, term: str, say: str, sentence: str,
                    job_id: str | None = None) -> dict[str, Any] | None:
    """A respelling fixed a sentence (and the counterfactual without it failed): add evidence. Becomes active once
    it has won in READING_EVIDENCE different sentences."""
    ok, why = reading_gate(term, say, lang, voice)
    signal("reading_win", {"voice": voice, "lang": lang, "engine": engine, "term": term, "say": say,
                           "sentence": sentence, "gate": why}, job_id)
    if not ok:
        return None
    from mediaforge.voice.profiles import sentence_key

    scope = {"voice": voice, "lang": lang, "engine": engine}
    sk = sentence_key(sentence)

    def merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        sents = list(dict.fromkeys([*old.get("sentences", []), *new["sentences"]]))[-20:]
        return {"say": new["say"] if old.get("say") != new["say"] else old["say"], "sentences": sents}

    lesson = upsert("reading", scope, term, {"say": say, "sentences": [sk]}, origin="auto", merge=merge, evidence=0)
    distinct = len(lesson["value"].get("sentences", []))
    with db.connect() as c:
        c.execute("UPDATE lessons SET evidence=? WHERE id=?", (distinct, lesson["id"]))
    if lesson["status"] == "candidate" and distinct >= READING_EVIDENCE:
        lesson = set_status(lesson["id"], "active", f"thắng ở {distinct} câu khác nhau", by_owner=False)
    return get(lesson["id"])


def readings(voice: str | None, lang: str, engine: str) -> dict[str, tuple[str, str]]:
    """Active reading lessons for this voice/language/engine: {term: (say, lesson id)}."""
    out: dict[str, tuple[str, str]] = {}
    for l in list_lessons("active", "reading"):
        sc = l["scope"]
        if sc.get("lang") == lang and sc.get("engine") in (None, engine) and sc.get("voice") in (None, voice):
            out[l["key"]] = (l["value"]["say"], l["id"])
    return out


def judge_terms(voice: str | None, lang: str) -> set[str]:
    """Terms the owner said were spoken right although ASR heard them wrong (✓ Nghe đúng rồi)."""
    return {l["key"] for l in list_lessons("active", "judge_accept")
            if l["scope"].get("lang") == lang and l["scope"].get("voice") in (None, voice)}


def accept_judgement(voice: str | None, lang: str, terms: Iterable[str], sentence: str, job_id: str | None = None) -> list[dict]:
    """The owner heard the sentence and it is right: ASR was wrong about these terms. Applied at once (owner-pinned)."""
    out = []
    for t in {_nfc(x) for x in terms if _nfc(x)}:
        out.append(upsert("judge_accept", {"voice": voice, "lang": lang}, t, {"sentence": sentence},
                          origin="owner", status="active"))
    signal("judge_accept", {"voice": voice, "lang": lang, "terms": sorted({_nfc(x) for x in terms}), "sentence": sentence},
           job_id)
    return out


# ------------------------------------------------------------------ migration of the old `learned` lexicon entries

def migrate_lexicons(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """Move every `learned` lexicon entry into lessons: entries failing the gate are retired (restorable), the rest
    become candidates for review. The lexicon files are backed up first and only lose their `learned` entries."""
    from mediaforge.text import normalize as N
    from mediaforge.voice import profiles

    report = []
    scopes: list[str | None] = [None] + [v["id"] for v in profiles.list_voices() if v.get("kind") != "blend"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for vid in scopes:
        path = N._lexicon_path(vid)
        if not path.exists():
            continue
        data = N.load_lexicon(vid)
        learned = [(lang, term, e) for lang, table in data.items() for term, e in table.items()
                   if isinstance(e, dict) and e.get("source") == "learned"]
        if not learned:
            continue
        if not dry_run:
            bdir = settings.sub("backups")
            shutil.copy(path, bdir / f"lexicon-{vid or 'global'}-{stamp}.json")
        for lang, term, e in learned:
            # Judge against the lexicon without this very entry (otherwise rule 5 always fires on itself).
            data[lang].pop(term)
            if not dry_run:
                N.save_lexicon(data, vid)
            ok, why = reading_gate(term, e["say"], lang, vid)
            status = "candidate" if ok else "retired"
            item = {"voice": vid, "lang": lang, "term": term, "say": e["say"], "status": status, "reason": why}
            report.append(item)
            if not dry_run:
                upsert("reading", {"voice": vid, "lang": lang}, term, {"say": e["say"], "sentences": [],
                                                                         "migrated_hits": e.get("hits", 0)},
                       origin="migration", status=status, evidence=0,
                       note=("cổng: " + why) if not ok else "từ từ điển learned cũ — chờ duyệt")
        if dry_run:
            data = N.load_lexicon(vid)  # untouched on disk
    return report


def misheard_terms(sentence: str, missing: Iterable[str], lang: str) -> list[str]:
    """The sentence's own words behind a QA diff's `missing` blocks (consecutive words stay together)."""
    from mediaforge.text.normalize import qa_tokens

    bad = {t for m in missing for t in qa_tokens(m, lang)}
    out, run = [], []
    for w in sentence.split():
        toks = qa_tokens(w, lang)
        if toks and all(t in bad for t in toks):
            run.append(w.strip(",.;:!?\"'()…"))
        elif run:
            out.append(" ".join(run))
            run = []
    if run:
        out.append(" ".join(run))
    return [t for t in dict.fromkeys(out) if t]


def accept_sentence(job: dict[str, Any], index: int) -> dict[str, Any]:
    """✓ Nghe đúng rồi on sentence `index` of a finished job: mark it passed and teach QA that ASR mishears its
    terms for this voice. Returns the updated sentence."""
    from mediaforge import takes
    from mediaforge.voice import profiles

    result = job["result"]
    s = result["sentences"][index]
    voice = profiles.base_voice(takes.job_voice(job))
    terms = misheard_terms(s["text"], s.get("missing") or [], s.get("lang", "vi"))
    accept_judgement(voice, s.get("lang", "vi"), terms, s["text"], job["id"])
    s.update({"passed": True, "accepted": "owner", "accepted_terms": terms})
    result["qa_failed_sentences"] = [x["text"] for x in result["sentences"] if not x.get("passed")]
    if "qa_passed" in result:
        result["qa_passed"] = not result["qa_failed_sentences"]
    if isinstance(result.get("report"), dict):
        result["report"]["qa_failed_sentences"] = result["qa_failed_sentences"]
    takes.save_result(job["id"], result)
    return s


def insights(days: int = 30) -> dict[str, Any]:
    """Trends for the Kinh nghiệm tab and `mediaforge lessons report`."""
    since = time.time() - days * 86400
    fails = signals("qa_fail", since)
    by_class: dict[str, int] = {}
    by_text: dict[str, int] = {}
    for f in fails:
        p = f["payload"]
        by_class[p.get("class", "other")] = by_class.get(p.get("class", "other"), 0) + 1
        by_text[p.get("text", "")] = by_text.get(p.get("text", ""), 0) + 1
    ls = list_lessons(limit=2000)
    count = {st: sum(l["status"] == st for l in ls) for st in STATUSES}
    with db.connect() as c:
        rated = [dict(r) for r in c.execute("SELECT * FROM ratings WHERE created_at>=? ORDER BY created_at DESC", (since,))]
    stars = sorted(r["stars"] for r in rated)
    return {
        "days": days, "qa_failures": len(fails), "by_class": by_class,
        "recurring": sorted(([t, n] for t, n in by_text.items() if n > 1), key=lambda x: -x[1])[:10],
        "lessons": count, "new_lessons": sum(l["created_at"] >= since for l in ls),
        "retired_recently": sum(l["status"] == "retired" and l["updated_at"] >= since for l in ls),
        "incidents": len(signals("incident", since)), "ratings": len(rated),
        "median_rating": stars[len(stars) // 2] if stars else None,
        "judge_retries": len(signals("judge_accept", since)),
    }


# ------------------------------------------------------------------ owner ratings → preferences and plan rules

RATING_TAGS = ("giọng", "phát âm", "nhịp", "hình", "chuyển cảnh", "phụ đề", "nhạc", "nội dung")
PREF_EVIDENCE = 2  # the same direction twice before a preference is applied


def get_rating(job_id: str) -> dict[str, Any] | None:
    db.ensure()
    with db.connect() as c:
        r = c.execute("SELECT * FROM ratings WHERE job_id=?", (job_id,)).fetchone()
    return {**dict(r), "tags": json.loads(r["tags"])} if r else None


def rate(job: dict[str, Any], stars: int, tags: Iterable[str] = (), note: str = "") -> dict[str, Any]:
    """The owner's 1–5 rating of a finished job. Low/high ratings with a tag feed preference lessons; a note on a
    low `nội dung` rating becomes a plan rule straight away (the owner wrote it)."""
    stars = int(stars)
    if not 1 <= stars <= 5:
        raise ValueError("stars must be 1..5")
    tags = [t for t in dict.fromkeys(tags) if t in RATING_TAGS]
    note = (note or "").strip()[:500]
    db.ensure()
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO ratings(job_id,stars,tags,note,created_at) VALUES(?,?,?,?,?)",
                  (job["id"], stars, json.dumps(tags, ensure_ascii=False), note, _now()))
    p, r = job.get("params") or {}, job.get("result") or {}
    kind = (r.get("report") or {}).get("kind") or p.get("kind") or job["type"]
    style = p.get("style")
    music_id = (r.get("report") or {}).get("music_id") or (p.get("music") if isinstance(p.get("music"), str) else None)
    music_id = None if music_id in ("auto", "") or (music_id and "/" in music_id) else music_id  # library ids only
    signal("rating", {"stars": stars, "tags": tags, "note": note, "kind": kind, "style": style, "music": music_id},
           job["id"])
    direction = "like" if stars >= 4 else "dislike" if stars <= 2 else None
    made = []
    if direction:
        # A rating without tags speaks about the whole look; tags narrow it down.
        prefs = {"hình": ("visual_pref", style), "chuyển cảnh": ("motion_pref", kind), "nhạc": ("music_pref", music_id)}
        for tag, (lkind, key) in prefs.items():
            if key and (tag in tags or (not tags and lkind == "visual_pref")):
                made.append(_pref(lkind, {"kind": kind}, key, direction, job["id"], note))
        if direction == "dislike" and "nội dung" in tags and note:
            made.append(upsert("plan_rule", {"kind": kind}, note, {"rule": note, "job": job["id"]},
                               origin="owner", status="active", note="ghi chú của bạn khi chấm điểm"))
    return {"rating": get_rating(job["id"]), "lessons": made}


def _pref(kind: str, scope: dict[str, Any], key: str, direction: str, job_id: str, note: str) -> dict[str, Any]:
    lid = lesson_id(kind, scope, key)
    old = get(lid)
    votes = dict((old or {}).get("value", {}).get("votes", {}))
    votes[direction] = votes.get(direction, 0) + 1
    other = "dislike" if direction == "like" else "like"
    lead = direction if votes[direction] >= votes.get(other, 0) else other
    status = "active" if votes[lead] - votes.get(other, 0) >= PREF_EVIDENCE else "candidate"
    l = upsert(kind, scope, key, {"votes": votes, "direction": lead, "last_note": note or None, "job": job_id},
               origin="rating", status=status, merge=lambda o, n: n)
    if l["status"] == "active" and status == "candidate" and l.get("origin") != "owner":
        l = set_status(lid, "candidate", "ý kiến đã đổi chiều", by_owner=False)
    return l


def plan_rules(kind: str) -> list[str]:
    """Rules for the script writer from earlier videos (owner notes + recurring lint defects), newest first."""
    return [l["value"].get("rule") or l["key"] for l in active("plan_rule", kind=kind)][:8]


def preference(kind: str, scope_kind: str, key: str | None) -> str | None:
    """'like' | 'dislike' | None for an active visual/motion/music preference."""
    if not key:
        return None
    l = get(lesson_id(kind, {"kind": scope_kind}, key))
    return l["value"].get("direction") if l and l["status"] == "active" else None


# ------------------------------------------------------------------ incidents (ops)

def incident(job: dict[str, Any] | None, error: str) -> None:
    """A job failed or was interrupted: record it, and keep one `ops` lesson per kind of error with a count."""
    first = (error or "").strip().splitlines()[0][:160] if error else "unknown"
    key = first.split(":")[0] if ":" in first else first
    jid = job["id"] if job else None
    signal("incident", {"type": job["type"] if job else None, "error": first}, jid)
    try:
        upsert("ops", {"type": job["type"] if job else None}, key, {"last_error": first, "last_job": jid},
               origin="auto", status="active", note="sự cố — xem tab Hệ thống")
    except Exception as e:  # noqa: BLE001 — never let bookkeeping hide the real failure
        log.warning("could not record incident: %s", e)
