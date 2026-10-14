# SDD — Fix a word's pronunciation in a finished video (no re-render)

Status: v1.0 (implemented) · 2026-10-14 · Owner: tu.dev@block8.ai · Extends [SDD.md](SDD.md) §5.2 step 10, §9;
builds on [SDD-guided-repair.md](SDD-guided-repair.md) (takes and export) and
[SDD-subtitle-fit-pronunciation.md](SDD-subtitle-fit-pronunciation.md) §5 (lexicon sources)

## 1. Problem

A finished video can read a few words wrongly even when every sentence passes QA. Whisper is lenient on acronyms
and lexicon terms, so a wrong reading still "passes". Examples from *“CSR của PNJ: Khi lời hứa mua lại gãy,
thuật toán khuếch đại cú sốc”* (job `8fbbb5893211`, 48 sentences, ~4.5 min):

| Written | Heard | Wanted | Sentences |
|---|---|---|---|
| `PNJ` | Pi Ên Di | Pi En Di | 0, 4, 11, 27, 45 |
| `GRI` | letters read wrongly | Gi A AI | 13 |
| `CSR` | letters read wrongly | Ci Ét A | 20, 45 |
| `pháp lý` | pháp **ly** (tone dropped) | pháp lý | 3, 6, 31 |

Until now the owner had two options. The first was a full re-render (≈ 11 min, new job card, new random visuals
unless they come from the cache). The second was clicking 🔁 on each sentence one by one, then exporting. Neither
says "this word is read like this, everywhere".

## 2. Requirements (from the owner)

| # | Requirement |
|---|---|
| R1 | On a finished video, the owner types the mispronounced word(s) and the new reading in an edit box. |
| R2 | Every sentence where the word occurs is re-read automatically with the new reading. |
| R3 | **No video re-render.** Only those sentences' audio changes; visuals, subtitles and duration stay the same. |
| R4 | The result is a ready-to-download file (MP4 by default) in a few minutes at most. |
| R5 | The reading is remembered, so later videos read the word correctly from the start. |
| R6 | Available from the dashboard, the REST API, MCP and the CLI. |

## 3. Design

A new job type **`repronounce`** `{job, readings: {term: reading}, format?}` (`repronounce.run`). It
orchestrates parts that already exist. There is no new synthesis or mixing code.

```
readings ──► 1 clean + match ──► 2 save to lexicon ──► 3 takes.rerender × N ──► 4 export.run (remux)
                                                                                     │
                                         result.repronounced[] + result.exports[] ◄──┘
```

1. **Clean and match.**
   - Blank rows and no-op rows (`reading == term`) are dropped. Text is NFC-normalised, as the script is.
   - The affected sentences are found with **the same matcher the TTS uses**: `normalize.term_pattern`.
     - The match is on whole words.
     - A term containing a capital letter is case-sensitive: `PNJ` matches neither `PNJs` nor `pnj`.
     - A lower-case term matches any case: `pháp lý` also matches the sentence-initial `Pháp lý`.
   - So "every occurrence" is exactly the set of sentences whose TTS text changes. `apply_lexicon` now uses the
     same pattern.
   - Terms that do not occur are reported in `not_found`. If none of the terms occur, the job fails before any
     GPU work. The API returns 400 even before the job is queued.
2. **Save the reading (R5).**
   - `pronounce.remember(..., origin="repronounce")` writes the global lexicon as `manual`. That source is never
     overwritten by automatic learning, and it overwrites an older `manual` reading. For example, `PNJ` was
     already `Pi Ên Di` and is now `Pi En Di`.
   - `merged_lexicon` lets a voice lexicon shadow the global one, so an existing voice-scoped entry for the same
     term is updated too.
   - Only terms that occur in the job are saved.
3. **Re-read each affected sentence.**
   - Each sentence goes through `takes.rerender` with no recording. This is the 🔁 path:
     - the job's voice, engine routing and reference clip (pinned when the job recorded one);
     - the full QA and repair loop;
     - the voice FX of the job;
     - level-matching to the original sentence region.
   - The lexicon is read inside `synth_sentence`, so no new parameter is needed there.
   - Two fixes in `speak.synth_sentence` make this reliable:
     - **Cache key.** It now includes lexicon terms matched with `terms_in` instead of a case-sensitive substring
       test. A lower-case term (`pháp lý`) now invalidates the cached audio of `Pháp lý …`. Before this fix the
       old (wrong) cache entry would have been returned.
     - **QA leniency.** It uses the term **as written in the sentence** (`Pháp lý`), so the ASR check forgives the
       respelling.
   - Each take is stored with `source: "repronounce"` and `readings: {term: reading}` (the terms in that sentence).
     The dashboard labels it, e.g. "Đọc lại: PNJ → Pi En Di".
4. **Export (R3, R4).**
   - The selection starts as what the dashboard selects by default: the latest take that passed QA for each
     sentence. Earlier fixes are therefore kept.
   - Every new take is selected **even if QA is unsure**. The owner asked for this reading, and a respelled
     acronym is often heard back as letters. Each sentence's score and `heard` text are reported, so a real
     failure is visible.
   - `export.run` splices the takes into `voice.wav` and remixes music and loudness exactly as compose does. The
     video stream is copied (`report.path = "remux"`). A take that cannot fit its slot, even after borrowing
     silence and stretching ×1.15, falls back to recompose. Recompose uses the cached images and rebuilds only the
     timeline. The result says which path ran.
5. **Record.**
   - `result.repronounced[]` on the source job appends `{at, readings, not_found, lexicon, sentences: [{index,
     text, take, score, passed, heard}], export, file, path}`.
   - The export also appears in `result.exports` as usual. No new job card is created.

### 3.1 What does not change

- **Burned-in subtitles** show the written text (`PNJ`), not the reading, so they are correct as they are.
- **Karaoke timing inside a re-read sentence** can drift by a few hundred ms. The sentence slot does not move.
  If that is visible, run **🔤 Dựng lại phụ đề** on the exported file's job (see §6).
- **The original `final.mp4` is kept.** The fixed version is the newest export, shown under "Bản sửa gần nhất".

### 3.2 Limits

- **Timbre.** Jobs made before reference pinning (such as `8fbbb5893211`) have no `ref` per sentence, so a re-read
  sentence uses the voice's *current* reference clip. Loudness is matched; timbre may differ slightly if the
  reference has changed since the render.
- **LLM respelling.** In the repair loop, the LLM respelling step could in theory propose its own respelling for
  a term the owner set. Lexicon terms are excluded from QA misses, so in practice the owner's reading stands.
- **Short slots force a recompose.** A reading that lengthens a short sentence can exceed its slot beyond ×1.15.
  The whole job then takes the recompose path:
  - the video is re-encoded, not stream-copied, which adds about 1–2 min;
  - images still come from the cache;
  - subtitles are re-timed to the new audio;
  - the duration can shift by a few hundred ms.
- **QA cannot prove the reading.** Whisper spells an acronym back as letters ("PNJ") and lexicon terms are
  lenient. A pass means the rest of the sentence is intact; only listening confirms "Pi En Di".

## 4. Dashboard

On every finished job card with sentences, **🗣 Sửa phát âm** sits between the export bar and the QA list:

- **Rows.** Each row is `[Từ bị đọc sai] → [Cách đọc mới]`, followed by a live count ("5 câu" / "không có trong
  video"; debounced `GET /api/jobs/{id}/occurrences`) and ✕. **+ thêm từ** adds a row.
- **🗣 Đọc lại & xuất video** submits `repronounce` and follows its progress, e.g. "câu 3/9: AI đang đọc lại câu ·
  41%". When it ends:
  - the new takes become the card's export selection;
  - the card repaints;
  - **Bản sửa gần nhất** shows a player for the fixed video, a download link and the list of re-read sentences.
    A sentence QA was unsure about is shown in grey with what was heard.
- **Takes.** The takes also appear under each sentence in the QA list, as for 🔁 / 🎙, so any sentence can be
  switched back to the original and exported again.

## 5. Interfaces

| Interface | Addition |
|---|---|
| REST | `POST /api/jobs/{id}/repronounce` `{readings: {term: reading}, format?}` → `{job, occurrences}`; `GET /api/jobs/{id}/occurrences?terms=a,b` → `{term: [index…]}` |
| Job | type `repronounce`, priority 1 (interactive; jumps ahead of queued renders) |
| MCP | `repronounce_video(job_id, readings, format="mp4", wait_seconds=0)` |
| CLI | `mediaforge repronounce <job> PNJ="Pi En Di" "pháp lý=pháp lí"` |

## 6. Data

```
outputs/<kind>/<job>/takes/<i>/<tid>.wav, .fx.wav      as in SDD-guided-repair §8 (source: repronounce)
outputs/<kind>/<job>/exports/<stamp>-<sel>.mp4         the fixed video
result.repronounced[]                                  history of fixes on the source job
lexicon/global.json  {term: {say, source: manual, origin: repronounce, added_at}}
```

No recording is involved, so the voice's samples and corrections are untouched.

## 7. Testing

- **Unit** (`tests/test_repronounce.py`):
  - occurrences equal exactly the sentences `apply_lexicon` rewrites (case, word boundary, NFD input);
  - cleaning of the readings;
  - QA leniency for a sentence-case match;
  - lexicon save overrides an older `manual` reading and a shadowing voice entry;
  - `run` re-reads only the affected sentences, selects the new takes and keeps earlier passed takes, reports
    `not_found`, has monotonic progress, and rejects a request where no term occurs.
- **Live, 2026-10-14**, on job `8fbbb5893211` (repronounce job `812a36dd7c44`), with `PNJ → Pi En Di`,
  `GRI → Gi A AI`, `CSR → Ci Ét A` and `pháp lý → pháp lí`:
  - **Coverage.** 10 sentences were matched (0, 3, 4, 6, 11, 13, 20, 27, 31, 45), and one take was made for each.
    9 passed QA. Sentence 13 was unsure: score 0.09, heard "SAR I-417".
  - **Recompose.** Sentence 27's take (4.00 s) did not fit its 3.55 s slot, so the export took `path = recompose`.
  - **Images.** All 14 scene images are byte-identical to the original render.
  - **Output.** H.264 1920×1080 + AAC; 277.2 s against 277.5 s for the original. The job took about 2.5 min in
    total.

## 8. Later

- An "apply to the main video" button that promotes the export to `final.mp4`, so the card's player shows the fix.
- Re-align word timings for re-read sentences and refresh the karaoke subtitles in the same job.
- Pin the reference clip per sentence in every new render, so re-reads of any job keep the exact timbre.
