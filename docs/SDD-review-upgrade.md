# SDD — Review and upgrade: Studio UX, staged pipeline, creative direction, learning from mistakes

Status: v1.1 (P6-A…D implemented, see §11; P6-E open) · 2026-10-15 · Owner: tu.dev@block8.ai · Extends [SDD.md](SDD.md) §5, §7, §8, §9, §10, §11

## 1. Problem

The owner's brief (2026-10-15):

> tạo SDD để review và nâng cấp toàn UIUX cũng như hệ thống để làm việc tốt hơn, tạo voice và video tốt hơn,
> sáng tạo hơn, biết học hỏi ghi nhận các lỗi gặp phải để cải thiện hơn ở các video sau.

In short: review and upgrade the whole UI/UX and the system, so that working with it is easier, voice and video
come out better and more creative, and the system records the mistakes it makes and uses them to do better on
later videos.

MediaForge works. In 221 jobs there were only 2 hard failures (§3.1). It still has four structural weaknesses,
which the review in §3 measures:

1. **The "learning" makes the voice worse.** The automatic repair loop stores any respelling that passed ASR once
   as a permanent `learned` rule. The owner's lexicon now holds rules that strip Vietnamese tones (`độ → đô`,
   `kiểm định → kiểm đinh`, `lung lay → lung lai`) and rules that will never match again (§3.3).
2. **Nothing learns across jobs.** Every job writes its own `report.json`. There is no store of mistakes, no
   record of the owner's judgement, and nothing that reads past failures before the next render. The owner fixed
   the same two sentences by hand about 25 times (§3.2).
3. **A video is one opaque 7–15 minute job.** You can only fix it afterwards, sentence by sentence. Images and the
   scene plan cannot be reviewed before compose, and an interrupted job restarts from zero (§3.4).
4. **Visual creativity is index-driven.** Motions and transitions rotate by scene number. The plan comes from a
   single LLM shot. Scenes have no shared art direction and no title or end cards (§3.6).

## 2. Requirements

| # | Requirement |
|---|---|
| R1 | **Learn from mistakes, safely.** Every failure, owner fix and owner judgement is recorded as a *lesson*. Lessons are applied automatically to later jobs, and a lesson can never silently make correct speech wrong. |
| R2 | **The owner's ears are the judge.** The owner can mark a sentence "nghe đúng rồi" (ASR was wrong), rate a finished video, and tag what was wrong. These signals feed R1. |
| R3 | **Catch problems before rendering.** A pre-render check (script lint + pronunciation + lessons) runs before any GPU time is spent. |
| R4 | **Staged, reviewable, resumable videos.** Plan → voice → visuals → compose are separate checkpoints. Each one can be inspected and redone on its own, and a restart resumes from the last finished stage. One-shot MCP calls keep working unchanged. |
| R5 | **More creative output.** Each video gets art direction (palette, motif, camera language) shared by every scene. Motion, transitions and music follow the scene's emotion. Title and end cards are available, plus hook variants for shorts. |
| R6 | **A clearer dashboard.** A Studio flow for creation, a review page per job, a Lessons view and a lexicon review queue. No blocking `confirm()` for reversible actions. |
| R7 | **Measured.** Every phase has a baseline number and a target, and the targets are measured by a regression corpus and stage timings, not by impressions. |
| R8 | **Constraints unchanged.** Localhost only. Voice audio never goes to an LLM. Licences are Apache/MIT/BSD (GPL internal only, per SDD §3). No talking-head avatar (owner's choice). LLM order stays Claude CLI → Ollama. |

## 3. Review — what the data says (baseline, 2026-10-15)

Source: the live store `~/.local/share/mediaforge` (read-only queries): `jobs` table, 10 video `result.json`,
162 TTS `voice.report.json`, the lexicons.

### 3.1 Reliability

| Metric | Value |
|---|---|
| Jobs | 221 (162 tts, 24 enrol, 6 narrated, 6 recite, 4 sing, 4 export, 3 short, …) |
| Hard failures | 2. One enrol: a browser webm that was empty or corrupt (`EBML … End of file`). One short: `interrupted by restart`. |
| Unexplained service shutdowns mid-job | 2 (2026-10-14; see the e2e-testing memory). The job was lost or the API went dead while the worker kept running. |
| Mean / max duration | narrated 421 s / 918 s · short 241 / 291 · sing 508 / 1523 · recite 214 / 307 · image 55 · tts 11 |

Hard reliability is fine. The costly part is that one restart throws away up to 15 minutes, because a job has
no checkpoints (→ §5.3).

### 3.2 Voice quality — sentence-level QA

| Set | Sentences | Failed QA | Note |
|---|---|---|---|
| Video jobs | 141 | 6 (4.3 %) | 112 s of the 861 s synth time went to sentences that still failed |
| TTS jobs | 180 (48 unique) | 54; **25 unique never passed** | almost all are retries of 2 probe sentences: "…thoăn thoắt…" and "…AI tại Ây Ai Ét Em I chấm vi en" |
| Repair budget | 54 sentences used 4 attempts, 14 used 5 | | the loop spends GPU time on input classes it cannot fix |

Failure taxonomy (from intended-vs-heard pairs):

| Class | Example (intended → heard) | Root cause | Countermeasure |
|---|---|---|---|
| **F1 Truncation / babble** | "…bạn sẽ không còn là nút thắt, mà trở thành người dẫn đường cho cả đội." → "…mà trở thành!" (the engine stopped early) | Engine behaviour on long sentences. Separately, one never-passed TTS input is a fragment cut at both ends ("ệu PNJ công bố … năm hai n"), a caller/splitter defect. (Correction, v1.1: v1.0 misread a display truncation as a malformed plan sentence.) | Script lint before synthesis (§5.2.1). Phrase-wise synthesis by default for VI sentences over ~35 syllables. |
| **F2 Acronym / URL / code-switch runs** | "AI tại Ây Ai Ét Em I chấm vi en" → "AI-EMI.VN"; "S.M.E." → "S.M.I." | A run of spelled letters with no pauses. A domain is read as one token. | Acronym-run expansion with pauses. Domain and URL normaliser. Prescan forces a reading (§5.2.2). |
| **F3 Judge error (ASR)** | Whisper writes CJK on VI audio ("Công kẹp胸 Trang chủ…"); "thoăn thoắt" → "thoán thoát" on **both** engines; held sung vowels under-heard | Whisper, not the voice. | Judge hardening plus an owner override that becomes a lesson (§5.2.3). |
| **F4 Hallucinated repetition** | "Báo cáo bền vững 2025 andra 2025 phát hành…" | Engine sampling. | Repetition detector in QA (n-gram repeat ⇒ immediate re-seed, not LLM respelling). |
| **F5 Lexicon pollution** | §3.3 | The learning rule itself. | §5.1.4 |

### 3.3 The learning loop, audited

`voice/speak.py` → `N.learn()` stores **every** respelling of an attempt that passed, as `source: learned`, after
a single success. The owner's lexicon `voices/gi-ng-t/lexicon.json` has 18 entries, 15 of them `learned`. 12 are harmful or useless:

| Learned entry | Problem |
|---|---|
| `độ → đô`, `tốc độ → tốc đô`, `kiểm định → kiểm đinh`, `đổ → đỗ`, `lung lay → lung lai` | Removes or changes the **tone** of correct Vietnamese words. These rules now apply to every future sentence that contains "độ". |
| `niềm tin → niềm tin`, `khi niềm tin đã lung lay → (same)` | Identity rules: no effect, pure noise. |
| `đổ đi kiểm định rồi bán lại → đổ đi, kiểm định, rồi bán lại`, `tốc độ của AI → …` | Whole-clause keys that will practically never match again. |
| `Ét → ét xì`, `SME → ét em ây` (voice) vs `SME → Ét Em I` (global, manual) | Contradictory readings. The learned one wins inside its voice. |
| `for → pho` (vi table) | An English word rewritten in a Vietnamese table, for every context. |

`hits` counts how many times a rule was *learned*, not how many times it was *used* (global manual entries all
show 0). We cannot tell which rules help.

Conclusion: ASR passed after a respelling plus a new seed, and the loop credited the respelling with the success.
Vietnamese is phonemic. When a correctly written Vietnamese word is misheard, the cause is the engine or the
judge, never the spelling. A respelling of such a word should never be learned.

### 3.4 Pipeline and time

- `narrated` runs as a single job: plan, then per-scene speak, then all images, then compose. Only `prepare` (plan
  + pronunciation prescan) is a separate step. There are no stage timings in the report, so where the 421 s goes
  is not measured. Voice synth is a small share: 861 s across 141 sentences in 10 videos.
- Fixing after the render is good (takes, repronounce, resubtitle, export without re-render). Fixing *visuals* is
  not possible: there is no "regenerate scene 4 image", no "swap music" and no "reorder scenes" without a new job.
- Image seeds are `seed + i`. Visual prompts are written per scene with no shared reference, so recurring
  subjects (a character, a product) change appearance from scene to scene.

### 3.5 UI/UX audit (dashboard)

| Finding | Evidence | Impact |
|---|---|---|
| A single "Tạo mới" form serves 6 kinds by toggling `.f-*` classes | `index.html`, `applyKind()` | Many irrelevant fields per kind. The two-step "1 · Viết kịch bản & kiểm tra phát âm" then "Tạo" is easy to skip. |
| No page per job. The result, QA list, takes, export, resubtitle and repronounce all stack inside a job card | `renderOutputs/renderQA/renderExport` | A long video with 30 sentences becomes a very long card. The video is not linked to its sentences (click a sentence ≠ seek). |
| QA offers only 🎙 re-record or 🔁 regenerate | `renderQA` | No "nghe đúng rồi" (judge wrong) and no "đọc chậm hơn / tách câu". This is why the owner iterated by hand ~25 times (§3.2). |
| Lexicon tab is a flat table with `learned/manual/verified` | `renderLex` | No sorting by risk, no "where was this used", no quarantine. The polluted entries in §3.3 went unnoticed. |
| No rating or feedback anywhere | — | The owner's judgement, the stated ground truth, is thrown away. |
| `confirm()` for deletes and resubtitle; `toast()` for 3.5 s | `app.js` | Blocking and not undoable, although the backend already moves data to trash. |
| Jobs tab is a text list with a status filter | `loadJobs` | No thumbnails, no search, no "make another like this". |
| `app.js` is a single 997-line file | — | Hard to extend. A React migration is noted as "later" in SDD §10, but it is not needed for this upgrade (§5.5.4). |

### 3.6 Creativity audit

| Area | Today | Gap |
|---|---|---|
| Script | One LLM call writes the plan. Short = "hook + CTA". | No alternatives. No structure templates (story, list, myth vs. fact, before/after). No self-critique pass. |
| Look | `style` preset + a per-scene prompt | No art direction shared across scenes (palette, recurring character, lens/lighting). No negative prompt from past failures. |
| Motion | `MOTIONS[i % 6]`, `TRANSITIONS[(i-1) % 6]` | Motion is unrelated to content: a fast whip-pan on a sad line, the same rhythm in every video. |
| Typography | Subtitles only | No title card, chapter/lower-third or end card with CTA. Explainer slides exist only in that kind. |
| Music | Owner picks a track or none. Constant volume under ducking. | No mood match, no intro/outro swell, no music-only breath between sections. |
| Sound | Voice + bed | No transitions sounds/stingers (would need licence-clean SFX; see §7). |

## 4. Concepts

| Term | Meaning |
|---|---|
| **Lesson** (`bài học`) | One durable, text-only fact the system learned, with provenance and a status: e.g. "term `SME` (vi, vieneu) → read `ét em i` with pauses", "Whisper mishears `thoăn thoắt`; accept", "owner dislikes `pan_right` on portraits", "planner produced a sentence ending mid-clause". |
| **Signal** | Raw evidence that may create or update a lesson: a QA failure, a repair that worked, an owner override, a rating plus tags, an incident (job failure, interrupt). |
| **Lesson status** | `candidate` → `active` → `retired`. A candidate does nothing except show in the review queue. Some kinds may also be `owner-pinned` (§5.1.3). |
| **Stage** | One checkpoint of a video job: `plan`, `voice`, `visuals`, `compose`. Each stage has an artefact on disk and a status, and can be redone on its own. |
| **Storyboard** | The `visuals` stage shown as a grid: one card per scene with its narration, image, motion and transition. Cards can be edited, regenerated or reordered before compose. |
| **Art direction** (`chỉ đạo hình ảnh`) | A per-video block written once: palette, motif/character sheet, camera language, mood curve. Every visual prompt, motion choice and music choice reads it. |
| **Rating** | The owner's 1–5 score on a finished job, plus tags: `giọng`, `phát âm`, `nhịp`, `hình`, `chuyển cảnh`, `phụ đề`, `nhạc`, `nội dung`. |

## 5. Design

### 5.1 Learning system ("Sổ kinh nghiệm")

#### 5.1.1 Storage

New SQLite tables in the existing DB (`db.py`), text only. No audio and no biometric data.

```sql
CREATE TABLE lessons (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,        -- reading | judge_accept | lint | engine_route | visual_pref | motion_pref | music_pref | plan_rule | ops
  scope TEXT NOT NULL,       -- json: {voice?, lang?, engine?, style?, kind?}
  key TEXT NOT NULL,         -- normalised trigger: a term, a lint code, a style, …
  value TEXT NOT NULL,       -- json payload (reading, route, prompt fragment, …)
  status TEXT NOT NULL,      -- candidate | active | retired
  evidence INTEGER NOT NULL DEFAULT 0,   -- independent supporting signals
  contra INTEGER NOT NULL DEFAULT 0,     -- signals against (regressions, owner rejects)
  uses INTEGER NOT NULL DEFAULT 0,       -- times applied in a render
  wins INTEGER NOT NULL DEFAULT 0,       -- uses where the sentence/scene passed first time
  origin TEXT, created_at REAL, updated_at REAL, last_used_at REAL, note TEXT
);
CREATE TABLE signals (
  id INTEGER PRIMARY KEY, job_id TEXT, kind TEXT, payload TEXT, created_at REAL
);
CREATE TABLE ratings (
  job_id TEXT PRIMARY KEY, stars INTEGER, tags TEXT, note TEXT, created_at REAL
);
```

The lexicon JSON files stay the **human-edited source of truth** for `manual`/`verified` readings, so nothing the
owner set is migrated or lost. Automatic `reading` lessons live in `lessons`, and only `active` ones are merged
into `merged_lexicon()` below manual entries.

#### 5.1.2 The learning cycle

```
 before render ──► PRE-CHECK  (lint + prescan + active lessons; asks the owner only about unknowns)
                        │
 during render ──► APPLY      (active lessons: readings, routes, art prefs) ──► count uses
                        │
 after render  ──► CAPTURE    (QA results, repair outcome, stage timings, incidents → signals)
                        │
 owner review  ──► JUDGE      (nghe đúng rồi, rating + tags, take chosen, lesson approve/reject → signals)
                        │
 consolidate   ──► LEARN      (signals → candidate/active/retired lessons; runs after each job, cheap, no GPU)
```

`consolidate` is pure Python, deterministic and unit-tested. The LLM is used only to *phrase* plan/visual lessons
(§5.1.5), never to decide promotion.

#### 5.1.3 Promotion rules

| Kind | Becomes `candidate` when | Becomes `active` when | Retired when |
|---|---|---|---|
| `reading` | A respelling won a repair and passes the gate in §5.1.4 | It won again in **another sentence** (evidence ≥ 2) with the same engine, **or** the owner approves | contra ≥ 2, or wins/uses < 0.5 after 6 uses, or the owner rejects |
| `judge_accept` | The owner clicks **nghe đúng rồi** on a sentence | Immediately (owner-pinned). QA then treats the term as lenient for this voice/lang. | Owner removes it |
| `lint` | A QA failure traced to a lint-able cause (F1/F2) | Immediately: lint rules are fixed code, and the lesson only records an instance as a test case | — |
| `engine_route` | An engine switch repaired a sentence class (e.g. acronym runs pass on voxcpm, fail on vieneu) | evidence ≥ 3 for that class | contra ≥ 2 |
| `visual_pref` / `motion_pref` / `music_pref` | A rating ≤ 2 with tag `hình`/`chuyển cảnh`/`nhạc`, a scene regenerated by the owner, or a rating ≥ 4 | evidence ≥ 2 in the same direction | contradicted by later ratings |
| `plan_rule` | Lint caught an LLM plan defect, or rating tag `nội dung` | evidence ≥ 2 | — |
| `ops` | An incident (interrupt, ffmpeg error, upload corrupt) | Shown in System tab; not applied | Resolved by owner |

#### 5.1.4 The reading gate (fixes §3.3)

A respelling `term → say` may become a `reading` candidate only if **all** of these hold:

1. `say != term` after NFC + casefold (no identity rules).
2. `term` has ≤ 3 tokens and no clause punctuation (no whole-clause keys).
3. **VI:** `term` is not made only of valid Vietnamese syllables (`pronounce.is_vi_syllable`). Correct Vietnamese is
   never respelled, so the failure is the engine's or the judge's (→ `judge_accept` / `engine_route` instead).
   **EN:** `term` is not a dictionary word unless it is capitalised mid-sentence (names).
4. VI only: `say` does not merely drop or swap diacritics of `term` (`pronounce._ascii(say) == pronounce._ascii(term)` ⇒ reject).
5. It does not contradict a `manual`/`verified` entry for the same term in any scope (the global manual wins and
   the candidate is dropped).
6. **Counterfactual check**: the same attempt (seed, engine, phrases) re-run *without* the respelling must fail,
   one extra synth, only when the candidate would be new. This separates "the respelling fixed it" from "the new
   seed fixed it".
7. Scope = `{voice, lang, engine}`, because a respelling that helps VieNeu can hurt VoxCPM2.

**One-off migration (`mediaforge lessons migrate`).** Existing `learned` lexicon entries are run through the gate.
Passing ones become `candidate` lessons. Failing ones (all of §3.3's table) are moved into `lessons` with status
`retired` and note `failed gate rule n`, and removed from `lexicon.json`. A backup goes to
`data/backups/lexicon-*.json` first, per SDD §11 data safety. The owner sees the list in the Lessons tab and can
restore any entry.

#### 5.1.5 Feeding lessons forward

| Consumer | What it reads |
|---|---|
| `speak.synth_sentence` | Active `reading` and `engine_route` lessons for the voice/lang. `judge_accept` terms are added to `lenient`. |
| Pre-check (§5.2) | `lint` instances as regression tests. Any term with a retired/contra reading is surfaced to the owner instead of guessed. |
| `pipelines.plan` | Up to 8 active `plan_rule` lessons, appended as "Lessons from earlier videos: …" (e.g. "every sentence must be complete; never end a scene mid-clause"). |
| Art direction (§5.4) | Active `visual_pref` / `motion_pref` / `music_pref` for the style and kind. |
| Weekly digest | `mediaforge lessons report` (CLI + System tab): new lessons, retired ones, top recurring failures, QA pass-rate trend. |

### 5.2 Voice quality

#### 5.2.1 Script lint (pre-render, no GPU)

Runs inside `prepare` and at the start of every speak/video job. Errors block in the dashboard (with a one-click
auto-fix). In MCP/API calls, auto-fixes are applied and reported in `report.lint`.

| Code | Rule | Auto-fix |
|---|---|---|
| L1 incomplete | Sentence ends with a conjunction/preposition ("mà", "và", "của", "and", "the") or a trailing space/comma | Merge with the next sentence, or ask the LLM to complete it (brief mode only) |
| L2 too long | VI > 35 syllables / EN > 30 words without internal punctuation | Mark `phrases` strategy up front (today this is attempt 3 of the repair loop) |
| L3 acronym run | ≥ 2 consecutive all-caps tokens or spelled letters ("AI SME") | Insert pause markers between acronyms and use their readings |
| L4 URL/domain/email | `x.vn`, `www…`, `@` | Normalise to its reading ("chấm vi en") as one lexicon-backed unit, with a pause before it |
| L5 mixed script | Non-Latin characters in a VI/EN sentence | Strip, or ask |
| L6 unknown term | Not a VI syllable, not in the lexicon, no lesson | Existing prescan question (`pronounce.scan`) |

#### 5.2.2 Acronym runs and domains (F2)

The normaliser groups an acronym run into a unit. Each acronym reads from the lexicon, with a 120 ms pause between
acronyms, and the unit is synthesised as its own phrase (phrase-wise synthesis already exists in `takes`/repair).
Domains are split on dots into readings ("ai sme chấm vi en"). QA compares against the reading, not the
spelling, so the `lenient` list covers it.

#### 5.2.3 Judge hardening (F3, F4)

- `asr.transcribe` already forces `language=lang`, and CJK still appeared. QA should also decode with
  `temperature=0` and no fallback sampling (the CJK output is typical of fallback decoding). If the heard text
  contains a script that cannot occur in `lang` (CJK in VI/EN), that is a **judge error**: re-transcribe once with
  `initial_prompt` = the intended sentence. Only a second failure counts against the audio.
- **Repetition detector:** a repeated 2–4-gram that is absent from the intended text ⇒ F4 ⇒ re-seed immediately and
  skip LLM respelling.
- **Failure-class-aware budget:** the repair loop picks the next strategy by class (F1 → phrases, F2 → unit
  reading, F3 → re-judge, F4 → re-seed) instead of the fixed seed → engine → phrases → LLM order. Stop after 2
  attempts that do not improve the score. Target: max 3 attempts on average for failing sentences (today 4–5).
- **Owner override.** In the QA list, each failed sentence gets **✓ Nghe đúng rồi**. This marks the sentence
  passed, records a `judge_accept` lesson for the misheard terms, and stops future retries on them.
- Later (P4 in SDD §10, unchanged): speaker-similarity (WavLM-SV) to judge timbre of takes. Licence check
  required before adoption.

#### 5.2.4 Regression corpus

`tests/corpus/voice_regression.jsonl` holds text only: `{text, lang, class, expect}`. It starts from the 25
never-passed sentences and the 6 failed video sentences, and every new `lint` lesson adds a line automatically.
`mediaforge eval voice --voice <id>` synthesises the corpus locally and prints pass rate per class. It runs before
any engine, normaliser or lexicon-gate change and is stored in `data/evals/` (audio stays local, git-ignored). It
is the measurement for R7.

### 5.3 Staged, resumable video pipeline

```
 plan ──► voice ──► visuals ──► compose ──► (export / takes / repronounce — unchanged)
  │         │          │           │
 plan.json voice.wav  images/     final.mp4
 lint      words      clips/      subs.*
 artdir    sentences  storyboard  thumbnail
```

- Job `result.stages = {plan|voice|visuals|compose: {status, started_at, seconds, artefacts, error?}}`. Stage
  outputs already live in the job dir. The change is that each stage is a function with explicit inputs and
  outputs, and `pipelines.narrated/short/explainer` become `run_stages(job, from_stage, until_stage)`.
- **Resume:** on restart, `db.init()` no longer fails a `running` video job. It re-queues it with
  `from_stage = first stage not done`. This addresses the interrupted short (§3.1) and the mid-job shutdowns in
  the memory notes.
- **Gates (dashboard only, opt-in):** `review: ["plan", "visuals"]` pauses the job in a new status `waiting` after
  those stages, so the owner can approve or edit. MCP/API default is `review: []`, the current one-shot behaviour.
  `create_video` gains an optional `review` arg.
- **Stage re-runs** (new job type `restage`, priority 1, like `rerender`):
  - `visuals` for one scene: new seed or edited prompt, then compose only.
  - `compose` only: change music, motion, transitions or cards. Image and voice are reused, as `resubtitle` already
    does with `reuse=True`.
  - `voice` for edited narration: only the changed scenes' sentences are synthesised (the sentence cache already
    makes the rest free). Durations are updated and compose follows.
- **Stage timings** go into `report.stages` (closing the "where do 421 s go" gap) and are the baseline for the
  performance targets in SDD §11.
- **Scope:** narrated, short, explainer and screen in P6-C. Sing/recite (longest jobs, max 1523 s) get stage timings
  and resume at their natural stages (`melody` → `voice` → `accompaniment` → `video`) in the same phase. Their
  artistic gates stay with [SDD-artistry-voice-blend.md](SDD-artistry-voice-blend.md).
- **Upload validation:** the enrol/sample endpoint rejects files under 1 KB or that ffprobe cannot read, *before*
  queuing (the webm failure in §3.1). The recorder UI checks blob size after stop and asks the owner to record
  again.

### 5.4 Creative direction

#### 5.4.1 Art direction block

`plan` gains a first section, written by the same LLM call (no extra round-trip):

```json
"art_direction": {
  "palette": ["#0f1d2b", "#e8b04b", "#f4efe6"],
  "motif": "a small chibi fox in a red scarf who appears in every scene",
  "camera": "low angles, shallow depth of field, warm rim light",
  "mood_curve": ["curious", "tense", "tense", "hopeful", "triumphant"],
  "structure": "problem → stakes → turn → solution → call to action",
  "music_mood": "calm-uplifting"
}
```

- Every `visual_prompt` is composed as `motif + scene subject + camera + palette words + style hint`, so recurring
  subjects stay consistent. The character sheet sits in one place, so editing it in the storyboard updates every
  scene.
- **Seeds:** scenes sharing the motif share a base seed with small offsets, which keeps the look coherent.
- **Structure templates** for brief mode: `story`, `listicle`, `myth-fact`, `before-after`, `how-to`, `news`. The
  LLM picks one when unset. Shorts get **2 hook variants** in the plan; the dashboard shows both at the plan gate,
  and MCP uses the first.
- **Self-critique pass** (one extra cheap LLM call, can be turned off): the planner checks its own plan against the
  active `plan_rule` lessons and the lint codes, then returns a corrected plan. This lowers L1/L2 hits at the
  source.

#### 5.4.2 Motion, transitions, pacing driven by emotion

The index-based rotation is replaced by a small table keyed by the scene's `mood_curve` entry:

| Mood | Motion | Transition | Duration bias |
|---|---|---|---|
| calm / reflective | slow `zoom_in`, `pan_up` | `fade`, `dissolve` (0.7 s) | +10 % hold |
| tense / urgent | faster `zoom_in_tl`, small pushes | `fadeblack`, hard cut (0.15 s) | −10 % |
| hopeful / triumphant | `zoom_out`, `pan_right` | `circleopen`, `smoothleft` | |
| explainer slide | `static` | `slideup` | |

The table lives in `video/compose.py` as data. `motion_pref` lessons override entries (e.g. "owner disliked pans on
portraits"). The same motion is never used on two consecutive scenes.

#### 5.4.3 Cards and typography

Rendered with Pillow (`video/slides.py` already does this), using the art-direction palette:

- **Title card** (2.5 s, optional, default on for 16:9): title + subtitle, over a blurred first image.
- **Chapter / lower-third** for scenes the plan marks `chapter: true`.
- **End card** (3 s): CTA text from the plan, plus the CC BY music credit when present (today it only appears in
  the dashboard).

#### 5.4.4 Music that fits

- The LLM picks a track id from `music_catalog.json` by its `mood` field to match `art_direction.music_mood`, unless
  the owner chose one. The choice is shown on the storyboard and can be swapped with a compose-only re-run.
- Volume automation: an intro swell of 2 s before the voice starts, the existing ducking, and a 1.5 s music-only
  breath at chapter boundaries and the end card (`afade`/`volume` envelopes in `audio_graph`).
- SFX/stingers: only if a CC0 pack is curated like the music catalogue (licence and recording checked). Otherwise
  out of scope.

#### 5.4.5 Evaluate later (not adopted here)

- An image–text consistency score (e.g. an open CLIP/SigLIP model) to auto-reject off-prompt images. Licence
  review done 2026-10-15 (§11.4); not adopted yet.
- LTX-2 clips for 1–2 "hero" scenes per video instead of all or none. Weights licence is already flagged
  [VERIFY: confirm with compliance] in SDD §3.

### 5.5 Dashboard ("Studio")

#### 5.5.1 Information architecture

| Today (7 tabs) | Proposed |
|---|---|
| Tạo mới · Ngâm thơ · Hát | **Studio**: pick a kind as cards (Giọng nói, Short, Video 16:9, Explainer, Ảnh, Phụ đề, Ngâm thơ, Hát). Each kind has its own form with only its fields. |
| Jobs | **Thư viện**: grid of thumbnails, search, filter by kind/status/rating, "Làm thêm bản giống thế này" (clone params) |
| — | **Job page** `#/job/<id>` (§5.5.2) |
| Giọng nói | **Giọng nói** (unchanged content; recorder checks blob size) |
| Phát âm | **Kinh nghiệm**: tabs *Phát âm* (lexicon + reading lessons) · *Hàng chờ duyệt* (candidates) · *Lỗi hay gặp* (top failure classes, QA trend) · *Đã bỏ* (retired, restorable) |
| Hệ thống | **Hệ thống** + incidents (`ops` lessons), stage timing chart, eval results |

#### 5.5.2 The video flow (Studio → job page)

1. **Ý tưởng / Kịch bản.** Text or document, kind, length, style, voice.
2. **Kiểm tra trước** (pre-check, a merged `prepare`). Lint results appear inline in the script with one-click
   fixes. Unknown terms are asked once (existing prescan). Shown: "Đã áp dụng 6 bài học từ các video trước".
3. **Kịch bản & chỉ đạo hình ảnh** (plan gate, optional). Edit scenes, pick a hook, edit the art direction.
4. **Storyboard** (visuals gate, optional). Image grid with 🔁 regenerate, ✏️ prompt, ⇅ reorder, motion and
   transition per card, music preview.
5. **Dựng.** Progress per stage (not one bar). Remaining time is estimated from `report.stages` history.
6. **Duyệt** (job page):
   - The player and a **sentence timeline** under it: red markers on failed sentences, and clicking a sentence
     seeks the video.
   - Per-sentence actions: 🎙 guide, 🔁 regenerate, **✓ Nghe đúng rồi**, **✂ Tách câu** (force phrases).
   - A scene strip with "đổi ảnh cảnh này" (restage).
   - Takes, export, repronounce and resubtitle move here from the job card.
   - **Đánh giá** (1–5 ★ + tags + note) at the bottom. Asked once, never nagging.

The "Nhanh" mode (no gates) is one toggle, remembered per kind. It gives today's behaviour.

#### 5.5.3 Interaction details

- Undo toasts (8 s, "Hoàn tác") replace `confirm()` for reversible actions (delete job → trash, resubtitle).
  `confirm()` stays only where an action is truly irreversible.
- Progress over SSE everywhere (already exists). Live regions (`aria-live="polite"`) for status, full keyboard
  navigation on the timeline (←/→ sentence, space play, R record).
- Every lesson applied is visible: a chip "📘 bài học" on the sentence or scene, with a tooltip for its origin.
  This makes the learning auditable, and the owner can reject the lesson in place.

#### 5.5.4 Front-end structure

Stay build-free (SDD §3). Split `app.js` into ES modules (`/js/studio.js`, `/js/job.js`, `/js/lessons.js`,
`/js/voices.js`, `/js/api.js`) with a ~40-line hash router. No framework: React stays an optional later step in
SDD §10, because none of the above needs it. CSS gets design tokens (`:root` variables, light/dark).

### 5.6 Interfaces

| Surface | Addition |
|---|---|
| REST | `GET /api/lessons?status=&kind=`, `PATCH /api/lessons/{id}` `{status, note}`, `POST /api/jobs/{id}/rating`, `POST /api/jobs/{id}/sentences/{i}/accept` (nghe đúng rồi), `POST /api/jobs/{id}/restage` `{stage, scene?, changes}`, `POST /api/jobs/{id}/approve` `{stage, plan?}`, `POST /api/lint` `{text, lang}` → findings + fixed text, `GET /api/insights` (trends) |
| Job status | `waiting` (gate). `result.stages`, `report.lint`, `report.lessons_applied` |
| MCP | `create_video`/`prepare_video` gain `review`, `structure`, `art_direction` (optional overrides); new `rate_job`, `list_lessons`, `restage_video`. Existing tools are unchanged by default. |
| CLI | `mediaforge lessons list|approve|reject|migrate|report`, `mediaforge eval voice` |
| Jobs | New types: `restage` (priority 1), `eval` (priority 3). `PRIORITY` table updated. |

## 6. Measuring success

| Metric | Baseline (2026-10-15) | Target | Measured by |
|---|---|---|---|
| Video sentences failing QA | 4.3 % (6/141) | ≤ 1.5 % | job reports, Insights |
| Regression corpus pass rate | 0 % (25 never-passed) | ≥ 80 %, the rest `judge_accept` or known-engine-limit | `mediaforge eval voice` |
| Mean attempts on failing sentences | 4–5 | ≤ 3 — **3.0 measured 2026-10-15** (§11.2) | report attempts |
| Harmful learned readings | 12 of the voice's 15 `learned` entries (§3.3) | 0 active; gate unit-tested | `lessons` + tests |
| Manual retries of the same sentence | ~25 for 2 sentences | ≤ 3, then "nghe đúng rồi" or a lesson | signals |
| Work lost on restart | whole job (≤ 15 min) | ≤ 1 stage | resume test |
| Fix a scene's image | new job (~7 min) | restage ≤ 60 s + compose | stage timings |
| Owner rating | not captured | median ≥ 4/5 over the last 10 videos | ratings |

## 7. Privacy, licences, safety

- Lessons, signals and ratings are text. They never contain audio, and are stored in the local DB under `data/`
  (git-ignored). LLM prompts may include lesson *text* (readings, plan rules). They never include recordings or
  speaker embeddings (SDD §11 unchanged).
- No new model is adopted by this SDD. Anything in §5.4.5 needs the SDD §3 licence review first. A SFX pack needs
  the same per-recording licence check as `music_catalog.json` (CC0/CC BY only; NC/ND/SA excluded).
- Data safety: the lexicon migration backs up first and moves entries, never deletes (SDD §11). Retired lessons
  stay restorable.
- Testing follows the e2e safety note. Gates, resume and restage are tested on a copy of the data store (dev
  server on :8766), never with synthetic recordings against the owner's voice.

## 8. Phases

| Phase | Scope | Exit criterion | Status |
|---|---|---|---|
| **P6-A Learning hygiene + lint** | §5.1.4 gate + migration, §5.2.1 lint, §5.2.3 judge hardening + repetition detector + class-aware budget, "✓ Nghe đúng rồi", regression corpus + `eval` | 0 harmful active readings; corpus ≥ 80 %; unit tests for gate and lint | Done 2026-10-15 — 0 harmful active readings; corpus 32/36 (89 %) after v1.2 and the owner's ✓ on "thoăn thoắt" (see §11.2) |
| **P6-B Lessons store + review** | §5.1.1–5.1.3, 5.1.5; ratings; job page (§5.5.2 step 6); Kinh nghiệm tab; undo toasts; app.js modules | Lessons visible and applied with chips; rating stored; digest prints | Done 2026-10-15 |
| **P6-C Staged pipeline** | §5.3 stages, resume, gates, restage, stage timings, upload validation; Studio flow steps 1–5 | Kill the server mid-visuals → job resumes at visuals; restage one scene ≤ 60 s + compose | Done 2026-10-15 (resume unit-tested with a simulated crash; live restage measured, §11.2) |
| **P6-D Creative direction** | §5.4.1–5.4.4 art direction, structure templates, hook variants, self-critique, mood motion table, cards, mood music + envelopes | Blind A/B of 3 topics (old vs new) rated by the owner; new wins ≥ 2/3 | Done 2026-10-15 — owner's blind A/B: new style preferred in 3/3 topics |
| P6-E Evaluate | §5.4.5 candidates, WavLM-SV (already P4) | Licence review done | Open |

P6-A comes first because it is cheap and stops the active harm (the polluted lexicon). P6-B is next, because
creative features (P6-D) need ratings to learn from.

## 9. Owner decisions (2026-10-15)

1. **Gates on by default** in the dashboard for Video 16:9 and Explainer (plan + storyboard); off for Short and
   Giọng nói. The "Nhanh" toggle turns them off per kind. MCP/API stays one-shot.
2. **Polluted entries:** Claude scores its own confidence per entry. At ≥ 90 % it decides (retire or keep). Below
   90 % the entry stays a `candidate` in the review queue for the owner.
3. **Title and end cards on by default** for 16:9 (end card only when the plan has a CTA or a music credit).
4. **Self-critique pass on by default.**

## 10. Risks

| Risk | Mitigation |
|---|---|
| Lessons still learn noise | Counterfactual check (gate rule 6), evidence ≥ 2, wins/uses retirement, owner chips, and the corpus run before gate changes |
| Gates slow down quick work | "Nhanh" toggle; MCP default stays one-shot |
| Art direction makes every video look alike | The motif is per video, not global. `visual_pref` lessons shift style only after ≥ 2 consistent ratings. |
| Resume reruns a half-written stage | A stage writes into `stage.tmp/`, renamed on success. Resume restarts that stage from scratch. |
| Front-end split breaks existing flows | Modules are moved first with no behaviour change, and the dashboard is smoke-tested on the dev server before each phase |

## 11. Implementation notes (v1.1, 2026-10-15)

### 11.1 Where the code is

| Concern | Files |
|---|---|
| Lessons, signals, ratings, reading gate, engine routes, migration, insights, incidents | `lessons.py` (`propose_route`, `route`), tables in `db.py` |
| Script lint (L1–L5), TTS-only readings (incl. dotted acronyms via the lexicon), long-sentence phrasing | `text/lint.py` |
| Sing/recite checkpoints | `sing/pipeline.py` (`run` stages, `sing_vocal(resume=, checkpoint=)`, `make_video(drawn=)`) |
| Judge: greedy decoding, CJK retry, repetition, spelled-acronym match | `asr.py` (`judge_error`, `repetition`, `spelled_match`) |
| Class-aware repair, counterfactual learning, lesson readings per engine, engine routes | `voice/speak.py` (`classify`, `expected_class`, `REPAIR_ORDER`, `STALL_LIMIT` — first-pass attempts count towards it) |
| Regression corpus + eval job | `voice_regression.jsonl`, `evals.py`, `mediaforge eval voice` |
| Stages, gates, resume, plan edits, recompose, cards, chapters, auto music | `pipelines.py` (`run_video`, `AwaitApproval`, `apply_plan_edits`, `recompose`, `add_cards`, `chapter_ass`, `auto_music`), `restage.py`, `jobs.py` (`waiting`, `requeue`, `RESUMABLE`), `db.init` |
| Mood motion/transitions, music fades | `video/compose.py` (`MOOD_STYLE`, `assign_styles`, `audio_graph(total=)`) |
| Cards | `video/slides.py` (`render_card`), lower-thirds `subtitles.with_chapters` |
| Dashboard | `web/js/studio.js` (gates, scene strip), `web/js/jobpage.js` (#job/<id>, timeline, rating), `web/js/lessons.js` (Kinh nghiệm, incidents); `app.js` keeps the rest |
| REST | `POST /api/jobs/{id}/sentences/{i}/accept`, `/approve`, `/restage`, `GET/POST /api/jobs/{id}/rating`, `GET /api/lessons`, `PATCH /api/lessons/{id}`, `GET /api/insights`, `GET /api/incidents`, `POST /api/lint` |
| MCP | `create_video` (+ `review`, `structure`, `cards`, `critique`, `music="auto"`), `restage_video`, `rate_job`, `list_lessons` |

### 11.2 Measured

- **Regression corpus** (36 sentences that had *never* passed, voice gi-ng-t, data-store copy): 0/36 before →
  5/36 after the first P6-A pass → **20/36 (56 %)** after adding the spelled-acronym judge (acronym class 18/22).
  The remaining 16: 8 × "thoăn thoắt" (both engines are heard as "thoán thoát"; this needs the owner's ✓ Nghe đúng
  rồi, after which they count as judge-accepted), 3 engine babble/repeat on long sentences, 4 acronym-only
  fragments ("A.I.", "S, M, E."), 1 cut fragment. Mean attempts on failing sentences was still 4.3: the stall rule
  stopped after two non-improving tries, but the first pass already spent two attempts with `engine=best`.
- **Repair budget, v1.2** (same corpus and data-store copy, one run each, `eval-voice` rows now keep every try):

  | Code | Passed | Mean attempts, failing | Eval time |
  |---|---|---|---|
  | main (baseline) | 21/36 | 4.27 | 466 s |
  | first-pass attempts seed the stall counter | 20/36 | 3.69 | 360 s |
  | + a failed multi-attempt first pass allows only improving repairs; phrase-first > 25 VI words; dotted acronyms | **21/36** | **3.0** | **296 s** |

  Rows lost against main: "chấm Vi En." and one 20-word sentence, which main passed only at attempt 5 after three
  identical scores (Whisper hallucinates "Ghiền Mì Gõ" on the 3-word fragment). Rows gained: the 30-word
  "Thuật toán chấm điểm…" (phrase-first now applies above 25 words) and one 21-word sentence. Still failing (15):
  8 × "thoăn thoắt" (awaiting the owner's ✓ — with it the corpus is 29/36 = 81 %), 3 long babble/repeat, "A.I."
  (tagged English; the English lexicon has no `AI`), "chấm Vi En.", the cut fragment "ệu PNJ…", and one sentence
  with "Ây Ai - ét-em-i".
- **Corpus, v1.2b** (+ Whisper outro hallucinations and decoder loops count as judge errors, English dotted
  acronyms spelled): **27/36** on the original rows ("chấm Vi En.", "A.I." and the two "lưu lư lư…" long
  sentences now pass), 29/40 with 4 new `qa_fail` rows from the A/B renders; mean attempts on failing sentences
  3.0. After the owner's ✓ Nghe đúng rồi on "thoăn thoắt": **32/36 (89 %)**, 34/40 (85 %) — target ≥ 80 % met.
- **Gated 16:9 video, end to end** (copy of the data store, 25 s brief, chibi, 3 scenes): plan 38 s (with
  self-critique) → voice 47 s → visuals 85 s → compose 7.5 s. Redrawing one scene at the storyboard took ≈ 25 s.
  Changing the music of the finished video (compose-only restage) took ≈ 10 s. The art direction kept one
  recurring character across scenes. Title card, chapter lower-third, karaoke subtitles and the end card with the
  CC BY credit were checked frame by frame.
- **Lexicon migration** (dry-run on live data, real run on a copy): 13 of the 15 `learned` entries retired by the
  gate, 2 left as candidates (`for → pho`, `I m → ai am`).

### 11.3 Differences from the design

- The corpus ships in the package (`src/mediaforge/voice_regression.jsonl`, because the service runs an installed
  copy), not in `tests/corpus/`. It grows from `qa_fail` signals in the local DB instead of from appended lines.
  Eval runs (`learn=False`) record no signals and no lesson uses.
- `POST /api/lint`, not GET (the text is a body).
- **Added (not in v1.0):** spelled-acronym matching in QA (`asr.spelled_match`). The eval showed the main acronym
  failure was the judge comparing a phonetic spelling with Whisper's written form ("ây ai ét em i chấm vi en" vs
  "AISME.vn").
- `spelled_match` only letter-compares a script-side block made of letter names, single letters or vowel-less
  clusters ("vn", "pnj"), so ordinary English words are never forgiven ("cat"→"cats" still fails). Some letter
  names are also Vietnamese words (`em`, `đi`, `ca`, `vi`); a false match needs a misheard run of ≥ 3 of them with
  near-identical letters, because keys under 3 letters must match exactly — rare, but possible.
- Art direction is asked for in English (it goes into image prompts); the plan gate says so. `art_direction: false`
  (job param, REST only) drops it for an old-style render — used for the P6-D blind A/B.
- v1.2: Vietnamese sentences over 25 words (was 35) are read phrase-first and get the L2 lint note. Cost: such
  sentences spend 3 first-pass attempts with `best` even when they pass. Dotted acronyms ("A.I.", "S.M.E.") read
  like the undotted lexicon term; without a lexicon entry they stay as written.
- v1.2 repair budget: first-pass attempts count towards `STALL_LIMIT`, and once a first pass of several attempts
  has failed, repairs continue only while they improve the best score. Measured in §11.2.
- No "✂ Tách câu" button: 🔁 Tạo lại already renders phrase-first.
- `app.js` was not split. New UI lives in ES modules under `web/js/` that use `app.js`'s globals, which avoids
  churn in working code.
- `engine_route` lessons (v1.2): when a plain take of another engine passes where the first engine failed, the
  failure class gets evidence (`lessons.propose_route`, signal `route_win`); 3 different sentences make it active,
  2 repairs by another engine retire it. It is applied twice in `synth_sentence`: (1) the engine is put first for
  sentences whose *expected* class (`expected_class`: acronyms/non-Vietnamese words → `terms`, long → `truncated`)
  has a route; with `best`, only a perfect take (score 0) on it skips the other engine, so `best` still compares
  otherwise; (2) in the repair loop, the routed engine is the next strategy for that class. Route uses are counted
  like other lessons (`record_use`). Mood "duration bias" is not implemented, because scene length is the
  narration length.
- `music: "auto"` is available (dashboard: "🎵 Tự chọn theo không khí video"), but the default stays "no music"
  so existing MCP callers see no change.
- Sing/recite (v1.2): checkpoints in the song folder's `stages.json` like `run_video`: the poem reading
  (`interpret`), every sung line (`sung/line_NN.npy` + its report and the key), the mix, and each video shot
  already drawn. A resumed job carries on at the next line/shot. Resumes are limited to 2 per job, and
  enrol/rerender/export are never re-run.
- Jobs → Thư viện: thumbnails, text search, "⧉ Làm thêm bản như vầy" and a `waiting` filter were added to the
  Jobs tab instead of a new tab.

### 11.4 P6-E licence review (2026-10-15, no model added)

| Candidate | Use | Licence found | Verdict |
|---|---|---|---|
| SigLIP / SigLIP 2 (`google/siglip-base-patch16-224`, `google/siglip2-base-patch16-224`) | image–text consistency score | Apache-2.0 (HF model card) | **Allowed.** Preferred: permissive weights; prompts are English. |
| OpenAI CLIP (`openai/clip-vit-base-patch32`) | same | Code MIT (openai/CLIP); the HF card puts "any deployed use" out of scope (a model-card statement, not a licence term) | Allowed by the §3 rule, but SigLIP first because of the card's scope note. |
| WavLM-SV (`microsoft/wavlm-base-plus-sv`) | speaker similarity of takes | Points to the UniSpeech LICENSE = **CC BY-SA 3.0** (checked on raw.githubusercontent.com) | **Rejected** (ShareAlike is outside the Apache/MIT/BSD rule); added to SDD §3 Rejected. |
| SpeechBrain ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`) | speaker similarity (WavLM-SV replacement) | Apache-2.0 (HF card); trained on VoxCeleb 1+2; EER 0.80 % | **Candidate.** [VERIFY: VoxCeleb training-data terms before adoption.] VieNeu's own 192-d speaker encoder (`VieNeuEngine.speaker_embedding`) is already in the stack and needs no new model; try it first. |

Next step (not done): measure SigLIP on the scene images of 10 finished videos (score vs the owner's redraws)
before wiring an auto-reject.

