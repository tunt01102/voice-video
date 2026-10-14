# SDD — Guided sentence repair, takes and export

Status: v1.0 (implemented) · 2026-10-14 · Owner: tu.dev@block8.ai · Extends [SDD.md](SDD.md) §5.2 step 10, §7, §9

## 1. Problem

A finished job can contain sentences that fail pronunciation QA, for example:

> vi · vieneu · lỗi 28% · nghe thành: “mỗi ngày tiến bộ một chút! bạn sẽ không còn là nút thắt! mà trở thành!”

The engine truncated or babbled the end of the sentence. Until now the owner had two options:

- **🎙 Ghi âm câu này**: the owner's raw recording *replaced* the sentence, and the whole video was
  re-rendered as a new job.
- **🔁 Tạo lại**: a new seed for that sentence, also followed by a full video re-render.

This caused three problems:

1. A raw human recording sits between synthetic sentences. It has a different room tone, loudness and
   pace from its neighbours, so the switch is audible.
2. Every fix re-rendered the visuals and produced a new job card.
3. Nothing learned from the recording, so the same sentence failed the same way the next time.

## 2. Requirements (from the owner)

| # | Requirement |
|---|---|
| R1 | When a sentence fails, the owner records it. The recording is used to **improve the AI voice** so the sentence does not fail next time. |
| R2 | The recording is **never inserted** into the output. The AI re-renders the sentence in the cloned voice, consistent with the sentences around it. |
| R3 | **No video re-render.** The re-rendered sentence appears **directly under the video**. The owner chooses which voice (original or re-rendered take) each sentence uses in the export. |
| R4 | The export format is chosen from a **dropdown** and defaults to **MP4**. |
| R5 | **Every recording is kept**, to raise accuracy and improve the voice over time. |

## 3. Concepts

| Term | Meaning |
|---|---|
| **Guide** (`correction`) | One owner recording of one sentence, stored permanently under the voice: `voices/<id>/corrections/`. A guide is reference data only and is never mixed into any output. |
| **Take** | One AI rendering of one sentence of a finished job: `outputs/<kind>/<job>/takes/<index>/<take_id>.wav`. A take is either `guided` (made with a guide) or `regen` (new seed and phrasing, no guide). |
| **Selection** | A map `{sentence index → take id | "original"}` chosen in the dashboard when exporting. |
| **Export** | A file built from the job's finished video/audio plus the selected takes, in a chosen format: `outputs/<kind>/<job>/exports/`. |

## 4. How a recording improves the voice (R1, R2, R5)

The `rerender` job runs on the GPU worker:

1. **Store the guide.**
   - The raw upload is copied to `voices/<id>/corrections/<cid>.<ext>` and cleaned (trimmed, denoised,
     48 kHz) into `<cid>.wav`.
   - It is transcribed with Whisper to get word timings and an ASR score against the sentence.
   - `corrections/index.json` records `{key(sentence) → [{cid, text, lang, asr_text, score, pauses, job,
     index, created_at, strategy}]}`.
   - The recording is never deleted, and voice backups include it (SDD §11).
2. **Read the owner's phrasing.** Whisper word timings, aligned to the script (`asr.align_script`),
   locate the pauses the owner made (gap ≥ 180 ms). These pauses split the sentence into phrases and
   give the pause length after each one, clamped to 0.08–0.6 s. With no pauses, the split falls back
   to internal punctuation (`, ; : – !`).
3. **Synthesise candidates.** All candidates use the **same canonical reference clip the job used**,
   which is pinned in the report as `ref`. This keeps the timbre identical to the neighbouring
   sentences (R2).
   - **a. Phrase-wise:** each phrase is synthesised separately with the job's engine. The phrases are
     joined with the owner's pause lengths, and QA runs on the joined sentence. Short phrases avoid
     the long-sentence truncation and babble seen in the example.
   - **b. Guide prosody:** the guide is the VoxCPM2 continuation prompt (`prompt_audio`/`prompt_text`),
     while the canonical clip stays the timbre reference (`ref_audio`). This applies only when VoxCPM2
     is the engine. VieNeu has no prompt channel, so for VieNeu this candidate becomes a whole-sentence
     retry with a new seed.
   - **c.** The usual repair loop (seed, engine switch, LLM respelling) runs on whichever of a/b is
     best, until the sentence passes or the attempt budget runs out.
   - The candidate with the lowest QA score wins. On ties within 0.02, phrase-wise wins because it
     keeps the canonical timbre.
4. **Learn.**
   - The winning strategy and phrasing are saved on the guide entry.
   - `synth_sentence` checks the index first, so any later render of the same sentence by this voice
     starts with the learned strategy. This is the "lần sau không lỗi nữa" (no repeat failure) path.
   - The cache key includes the guide id, so old cached failures are bypassed.
   - Respellings that fixed the sentence go into the lexicon as `learned`, as before.
   - The recording is also enrolled as a voice sample with `source: "correction"`. This grows the
     sample pool; `pick_refs` weights correction clips slightly below dedicated enrolment clips, so a
     single emphatic sentence does not become the main reference.
   - **No model weights are fine-tuned.** "Improvement" here means a guided reference, learned
     phrasing and lexicon, and a larger sample pool. The `corrections/` folder is a clean dataset
     (text + audio pairs) for a future fine-tune (P4).
5. **Produce the take.**
   - The take is the raw winning audio. Its preview is the same audio passed through the job's voice FX
     (`voice fx ∪ job fx`).
   - The preview is then level-matched to the RMS of the original sentence's region of the voice track,
     not loudness-normalised on its own.
   - The take is appended to `result.sentences[index].takes` of the **source** job (`db.update_job`
     plus `result.json`). No new job card is created for the user to manage.

The `regen` mode (🔁 Tạo lại) runs the same steps without a guide: candidate a (phrase-wise, punctuation
split) plus a new seed.

## 5. Export without re-rendering the video (R3, R4)

`export` job, params `{job, selection, format}`:

1. **Spans.** Each sentence's span in `voice.wav` is computed from word timings. New jobs store the words
   in the result; old jobs re-align with `asr.align_script` once and cache the spans in
   `result.sentence_spans`. A sentence's **slot** runs from the midpoint of the gap to the previous
   sentence to the midpoint of the gap to the next one.
2. **Splice.** For every selected take, its FX'd preview is placed at the start of the slot (after the
   original leading silence):
   - **Take shorter than the slot:** the rest of the slot is padded with silence.
   - **Take longer than the slot:** it is time-stretched (pedalboard, pitch preserved) up to ×1.15.
   - **Still longer:** the slot borrows silence from the neighbouring gaps. If that is not enough, the
     export falls back to a **recompose**.
3. **Remux (default path).** The new voice track is re-mixed exactly like `compose`:
   - background music is looped and ducked with `sidechaincompress`;
   - the mix goes through `loudnorm I=-16`;
   - the video stream is copied from `final.mp4` (`-c:v copy`).
   Visuals, transitions and burned subtitles are untouched, and the duration is identical. This takes
   seconds, not minutes.
4. **Recompose (fallback).** Recompose runs only when a take cannot fit its slot. It is `run_video` with
   `plan_file` and `takes` overrides; images come from the content-addressed cache, so only the timeline
   and encode are redone. The result says which path ran (`report.path = remux | recompose`).
5. **Formats.** These are defined in one table, `export.FORMATS`, which the dashboard dropdown and the
   API share:

   | id | Container / codecs | Video | Notes |
   |---|---|---|---|
   | `mp4` (default) | MP4 H.264 + AAC | copy | soft subs `mov_text` kept |
   | `mov` | QuickTime H.264 + AAC | copy | |
   | `mkv` | Matroska H.264 + AAC | copy | soft subs converted to SRT |
   | `webm` | WebM VP9 + Opus | re-encode (slower) | soft subs converted to WebVTT |
   | `mp3` | MP3 192k | — | mixed audio only |
   | `wav` | WAV PCM 48 kHz | — | mixed audio only |
   | `m4a` | AAC 192k | — | mixed audio only |

   TTS jobs (audio only) offer `mp3` (default), `wav` and `m4a`.

6. **Output.** The export is written to `exports/<n>-<selection-hash>.<fmt>` and appended to
   `result.exports` of the source job. An identical selection and format returns the existing file.

## 6. Dashboard (R3, R4)

Under the video or audio of a finished job:

- **QA list.** Failed sentences stay red, and each sentence keeps two buttons:
  - **🎙 Ghi âm câu này** records a guide and submits `rerender`.
  - **🔁 Tạo lại** submits `rerender` without a guide.

  The header now says that the recording is used as a guide and the AI renders the sentence again.
- **Takes under the sentence.** When a sentence has takes, a voice picker appears:
  - ◉ **Bản gốc (AI)**, with a player for the original sentence region;
  - ◉ one entry per take, with its player and its QA result, e.g. `vieneu · lỗi 0% ✓`;
  - ▶ **Bản ghi của bạn**, a player for listening only. It is never selectable, because recordings are
    never exported.

  The latest take that passed QA is selected by default.
- **Export bar.** A format `<select>` (MP4 selected by default), then **⬇ Xuất video**. Exports appear as
  download links under the bar.
- **Refresh.** When a `rerender` or `export` job ends, the source job's card refetches and repaints. The
  card previously repainted only on a status change.

## 7. Interfaces

| Method & path | Body | Result |
|---|---|---|
| `POST /api/jobs/{jid}/sentences/{index}/rerender` | `{file?: upload path, lang?}` (no file = regen) | `{job}` (type `rerender`) |
| `POST /api/jobs/{jid}/export` | `{selection: {index: take_id}, format: "mp4"}` | `{job}` (type `export`) |
| `GET /api/export-formats` | — | `{video: [...], audio: [...]}` |
| `GET /api/voices/{id}/corrections` | — | guide index (for the Voices tab and debugging) |

Job types `rerender` (priority 1, interactive) and `export` (priority 2) are added to `jobs.JOB_TYPES`.

The legacy `POST /api/jobs/{jid}/patch` stays for API clients but is no longer used by the dashboard.
It still re-renders the whole job.

## 8. Data layout

```
voices/<id>/corrections/index.json           guide index (sentence key → entries)
voices/<id>/corrections/<cid>.<ext>          raw recording (never deleted)
voices/<id>/corrections/<cid>.wav            cleaned 48 kHz guide
outputs/<kind>/<job>/takes/<i>/<tid>.wav     raw take (pre-FX)
outputs/<kind>/<job>/takes/<i>/<tid>.fx.wav  take after voice FX, level-matched (what is spliced)
outputs/<kind>/<job>/exports/*.{mp4,…}       exports
```

Every recording is personal biometric data. It stays under `data/`, is never sent to an LLM (only text
is) and is never committed.

## 9. Testing

- **Unit (`tests/`):**
  - phrase split from timings and from punctuation;
  - slot computation and splice (pad, stretch, borrow, overflow → recompose);
  - format table → ffmpeg args;
  - guide index round-trip and lookup by sentence key.
- **End to end against the live service:**
  - use job `a2572392baf0`, sentence 14, with a stand-in recording from macOS `say`;
  - run `rerender` and check that a take exists with a QA score;
  - export as `mp4` and `mov`, then run `ffprobe` to check that the video codec is unchanged, the
    duration is within 0.1 s, and an audio stream is present;
  - export as `mp3`.
- `node --check src/mediaforge/web/app.js`.

## 10. Out of scope / later

- Matching guides at phrase level across *different* sentences: reusing the phrasing learned for
  "nút thắt" in a new sentence.
- Fine-tuning engine weights on `corrections/` (needs a licence-clean training recipe).
- A speaker-similarity score (WavLM-SV) to check take timbre against neighbouring sentences
  automatically.
