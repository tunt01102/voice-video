# SDD — Subtitles that always fit, karaoke highlight, and pronunciation pre-scan

Status: v1.0 (implemented) · 2026-10-14 · Owner: tu.dev@block8.ai · Extends [SDD.md](SDD.md) §5.2, §6, §7.1, §9

## 1. Problem

### 1.1 Subtitles run off the screen

In the video *“CSR của PNJ: Khi lời hứa mua lại gãy, thuật toán khuếch đại cú sốc”* (job `8fbbb5893211`)
many burned-in cues were cut at both edges of the frame, for example (0:23):

> ~~Trướ~~c hết, xin thừa nhận PNJ có số liệu thật, và nhóm không muốn đánh giá một c~~hiều.~~

Other reported cues: *“Nhưng vết nứt nằm ở hai tầng dưới, … dấu hiệu đáng chú ý, …”* and
*“Về môi trường, phát thải năm hai nghìn không trăm hai mươi lăm là mười bảy nghìn năm trăm…”*.

Root cause, three defects that compound:

| # | Defect | Effect |
|---|---|---|
| D1 | `subtitles._wrap` tries every two-line split with both halves ≤ 42 chars. When no word boundary satisfies that (an 84-char cue), it **falls back to one line of up to 84 chars**. | One 84-char line ≈ 2,400 px at 58 px bold Arial; the frame is 1,920 px. |
| D2 | The ASS header sets `WrapStyle: 2` (no automatic wrapping). | libass never wraps the over-long line, so it is clipped at both edges. |
| D3 | Width is measured in **characters only**. | Wide glyphs (capitals, `W`, `M`, acronyms such as `NHNN`, `GRI`) can overflow even at ≤ 42 chars; in vertical video 26 capitals ≈ 1,600 px vs 950 px available. |

Secondary defects in the same code: the greedy cue builder flushes at the 84-char limit, which leaves
one-word orphan cues (“nào.”, “động.”, “nhất.”, “hai mươi ba.”) and splits number phrases across cues
(“…mười bảy nghìn năm / trăm chín mươi…”).

### 1.2 Mispronunciations are found only after the render

The pronunciation lexicon (`lexicon/global.json`, `voices/<id>/lexicon.json`) exists, but terms are added
only after a sentence fails QA. A video built from a brief contains terms nobody has seen until the
render is done (in the PNJ video: `GRI`, `NHNN`, `KOL`, `V1`, `Carroll`, `Darby`, `Karni`, `Dybvig`,
`Perrow`, `livestream`, `website`, `CO2`…), so the owner discovers broken audio after ~11 minutes of render.

### 1.3 Owner request: karaoke-style highlight

“Đọc tới đâu thì text đó sáng lên, to hơn một chút hoặc đổi màu.” Today karaoke (`\kf` sweep) is used only
for vertical shorts.

## 2. Requirements

| # | Requirement |
|---|---|
| R1 | No subtitle line may ever extend beyond the safe area of the frame, in any aspect ratio, in SRT/VTT/ASS. |
| R2 | Cues are balanced: no one-word orphan cues when avoidable, prefer breaks at punctuation, avoid breaking inside a spoken number. |
| R3 | The PNJ video is re-rendered with fixed subtitles **without re-synthesising the voice or regenerating images**. Any finished video can be fixed the same way from the dashboard. |
| R4 | The word being spoken is highlighted (colour + slightly larger), for every video aspect, by default. |
| R5 | Before a video is rendered, the script is scanned for terms the TTS may misread; the owner writes how each is read (e.g. `PNJ → Pi En Di`), or confirms it is fine. |
| R6 | Those answers are **saved** in the lexicon (never overwritten by automatic learning) so later renders and later scans reuse them. |
| R7 | The same flow is available to Claude through MCP. |

## 3. Design — subtitles (R1, R2, R4)

### 3.1 Measuring width in pixels

`subtitles.Layout` measures strings with Pillow using the same font file libass uses for the style
(`Arial Bold` from `/System/Library/Fonts/Supplemental`, falling back to any Arial/DejaVu TTF, and finally
to a conservative 0.62 em/char estimate). Calibration: libass sizes a font so that its ascender+descender
spans `Fontsize` pixels; Pillow sizes it by em. Measured on this Mac (bold Arial, 58 px, burned with
ffmpeg-full/libass): libass width = 0.894 × Pillow width. We divide by 1.10 (slightly wider than drawn), add a
4 % safety factor, `2 × Outline`, and 8 % of the line's widest word for the karaoke pop (§3.4).

Safe width = `PlayResX − MarginL − MarginR` (88 % of the frame width).

### 3.2 Cue building

1. **Sentence groups.** Words are grouped at strong punctuation (`. ! ? … : ;`) and at pauses > 0.6 s.
2. **Fit test** `fits(words)`: the words can be wrapped into ≤ `max_lines` lines, each ≤ `max_chars`
   characters **and** ≤ safe width in pixels, and the span lasts ≤ `max_duration` (unless it is one word).
3. **Optimal split (dynamic programming)** of each group into cues: minimise
   `1000 × cues + Σ (len − target)² / 10 + break penalties`, where `target = total / ⌈total / limit⌉`.
   Break penalties: −40 after soft punctuation (`, – -`), +60 between two number words
   (“nghìn | năm”), +120 for leaving one or two words alone. Every chunk must pass the fit test, so a
   valid split always exists (a single word always “fits”, see 3.3).
4. **Line wrap** of each cue: try every split into ≤ `max_lines` lines that pass the fit test and pick the
   most balanced (in pixels), with a bonus for breaking after punctuation. There is **no fallback to an
   unwrapped line** (D1).
5. Timing rules are unchanged (close short gaps, linger 0.25 s, never overlap).

### 3.3 Safety nets

- A single word wider than the safe width (a URL) is drawn with `\fscx` reduced to fit.
- The ASS header uses `WrapStyle: 0` (libass smart wrap) instead of `2` (D2): if measurement were ever
  wrong, libass wraps onto an extra line instead of clipping.
- Tests assert, for real-world-length Vietnamese input, that every line of every cue passes the pixel and
  character limits, and a render test checks burned frames for text touching the frame edges.

### 3.4 Karaoke word highlight (R4)

`write_ass(..., karaoke=True)` now emits a per-word *pop* instead of the `\kf` colour sweep:

```
{\1c&HFFFFFF&\fscx100\fscy100\t(t0,t0+80,\1c&H00D4FF&\fscx108\fscy108)\t(t1,t1+60,\1c&HFFFFFF&\fscx100\fscy100)}word
```

`t0` is the word's start and `t1` the next word's start − 40 ms, relative to the cue start (ms). The spoken word turns yellow
(`#FFD400`) and grows 8 %; it returns to white when the next word starts. Because each word carries its
own tags, nothing bleeds into the following words. The 8 % growth is reserved in the width budget (§3.1).

`write_all` turns karaoke on for **all aspects** by default (`karaoke=None` → on); `karaoke: false` in
job params restores static subtitles. SRT/VTT are unaffected.

## 4. Design — re-subtitle a finished video (R3)

New job type **`resubtitle`** `{job}` (`pipelines.run_resubtitle`):

1. Loads the finished job's `result.json`: stored `words` (aligned word timings), `voice.wav`, `plan.json`,
   params (aspect, music, karaoke…).
2. Rebuilds `subtitles.{srt,vtt,ass}` with the new cue builder.
3. Rebuilds the scene list with the **same** code path as `run_video` (`build_visuals`), reusing the
   job's own `images/scene_NN.png` / `slides/` / `clips/` (no image generation unless a file is missing,
   which then hits the deterministic image cache). Scene durations come from `voice/scene_NN.wav`
   lengths + the 0.35 s gap, exactly as in the original render.
4. Composes `final.mp4` with the existing `voice.wav` (no TTS, no ASR).
5. The previous `final.mp4` and every previous export of the job move to `data/trash/<job>-<stamp>/`
   (exports carried the old burned subtitles; a stale “MP4 · giọng gốc” export would otherwise be reused).
6. Updates `result.json` (`subtitles`, `duration`, `resubtitled_at`) in place — the job card stays the same.

Dashboard: video job cards get **“🔤 Dựng lại phụ đề”**. CLI: `mediaforge resubtitle <job>`.

## 5. Design — pronunciation pre-scan (R5–R7)

### 5.1 Flow

```
Tạo video ──► [1. Chuẩn bị kịch bản] job "prepare"  (plan via LLM, or the owner's script / slides)
                        │  result: plan_file, scenes, candidates[]
                        ▼
              [2. Kiểm tra phát âm] dashboard table: term · context · "đọc là" (prefilled suggestion)
                        │  owner fills / ticks "đọc đúng" / ignores
                        ▼
              [3. Dựng video] POST /api/lexicon/terms (save) → POST /api/jobs {plan_file, pronunciations}
```

`run_video` already accepts `plan_file` (resume path), so the rendered script is exactly the scanned one.
`run_video` also applies a `pronunciations` param by saving it to the lexicon first, so MCP/CLI callers that
skip the dashboard still persist their answers.

The plan step is extracted from `run_video` into `pipelines.make_plan(p)` and shared by both job types.

### 5.2 Scanner (`text/pronounce.py`)

`scan(text, lang, voice, use_llm=True) → [{term, say, known, source, reasons, context, count}]`

Heuristics (fast, deterministic):

| Rule | Examples |
|---|---|
| Acronym: ≥ 2 capitals/digits, no lowercase | `PNJ`, `GRI`, `NHNN`, `KOL`, `CSR` |
| Letters + digits | `V1`, `PL4`, `KN1`, `CO2`, `T+120` |
| CamelCase / brand | `GitHub`, `iPhone` |
| *vi only*: Latin word that is not a valid Vietnamese syllable | `Carroll`, `Darby`, `website`, `livestream` |
| *en only*: word with Vietnamese diacritics | `Nguyễn` |
| Symbols the normaliser may not voice | `₂`, `&`, `@`, `#`, `/` inside a token |

The Vietnamese-syllable test is a regex for onset + nucleus + coda (`(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|
[bcdđghklmnprstvx])? [aăâeêioôơuưy]{1,3} (ng|nh|ch|[cmnptiyou])?`), applied to the diacritic-free ASCII
word; words with `f j w z` or clusters such as `rr`, `ll`, `rb` fail it.

LLM pass (optional, through `llm.complete_json`, same provider order as planning): given the script, list
further terms a {lang} TTS is likely to misread and suggest a respelling in {lang} orthography for every
term. Suggestions are kept only for terms that occur verbatim in the script. The LLM never receives audio.

Terms already in the merged lexicon are returned with `known: true` and their saved reading (shown
collapsed, editable). Terms the owner previously confirmed as fine (`source: "verified"`) are not returned.

### 5.3 Storage (R6)

Lexicon entries gain two sources and a timestamp:

| `source` | Meaning | Written by |
|---|---|---|
| `manual` | owner-entered reading (pre-scan or Phát âm tab) — never overwritten by `learn()` | owner |
| `verified` | owner confirmed the term reads correctly as written (`say == term`) — skipped by future scans | owner |
| `learned` | respelling that fixed a QA failure | repair loop |
| `default` | shipped defaults | code |

`POST /api/lexicon/terms {lang, voice?, terms: {term: say}, verified: [term]}` merges (the existing
`PUT /api/lexicon` replaces the whole file and stays for the Phát âm tab). Entries record `added_at` and
`origin: "prescan"`. Default scope is the global lexicon (pronunciation of `PNJ` does not depend on the
voice); the dashboard offers the voice scope too.

### 5.4 Interfaces

| Interface | Addition |
|---|---|
| REST | job type `prepare` (params as `narrated/short/explainer`), job type `resubtitle {job}`, `POST /api/lexicon/terms`, `POST /api/jobs/{id}/resubtitle` |
| MCP | `prepare_video(brief|script, kind, lang, seconds…)` → `{plan_file, candidates}`; `create_video(..., plan_file, pronunciations)`; `resubtitle_video(job_id)` |
| CLI | `mediaforge resubtitle <job>` |
| Dashboard | “1 · Chuẩn bị kịch bản & kiểm tra phát âm” before “Dựng video” for video kinds; “🔤 Dựng lại phụ đề” on video cards |

`prepare` runs on the single worker like every model call (CLAUDE.md), with priority 1 so it does not wait
behind queued renders.

## 6. Test plan

| Test | Checks |
|---|---|
| `test_subtitles.py::test_every_line_fits` | synthetic Vietnamese sentences of 40–260 chars (incl. the three reported cues) for 16:9, 9:16, 1:1: every line ≤ max_chars and ≤ safe px |
| `::test_no_orphans` | no one-word cue < 0.5 s when it could merge |
| `::test_karaoke_tags` | ASS contains `WrapStyle: 0`, per-word `\t(...)` tags, valid timings |
| `test_pronounce.py` | acronyms, alnum, foreign words flagged; Vietnamese words not flagged; known/verified handling; lexicon merge keeps `manual` |
| Render check | burn the new ASS for job `8fbbb5893211` over black at every cue midpoint; assert no text pixels in the outer 2 % of the frame |
| Live | `resubtitle` on job `8fbbb5893211`; extract frames at 0:23, 0:35, 0:44 |
