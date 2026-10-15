# MediaForge — Software Design Document

Status: v1.6 (implemented; v1.6 adds the review/upgrade design) · 2026-10-15 · Owner: tu.dev@block8.ai · Internal tool, localhost only

MediaForge is the single local service on this Mac that every other task (and every
Claude Code session) calls to produce **voice, audio, subtitles, images and videos**.
Its headline capability: narrated videos with subtitles, spoken in **the owner's own
cloned voice**, in **English and Vietnamese**.

---

## 1. Goals and scope

| In scope (v1) | Out of scope (v1) |
|---|---|
| Voice profiles from the owner's recordings (record in browser or upload) | Talking-head / lip-sync avatars (no Mac-GPU path upstream; not selected) |
| Voice-cloned TTS, EN + VI + mixed, with pitch ("độ trầm"), pace and clarity controls | Music generation (licence-clean options pending — see §10) |
| Automatic pronunciation QA and self-repair | Network exposure beyond 127.0.0.1 |
| Subtitles: SRT, VTT, karaoke ASS, burned-in or soft | Multi-user auth |
| Image generation (local diffusion) | |
| **Hát & Ngâm thơ**: singing / poetry chanting in the cloned voice, traditional accompaniment, illustrated video ([SDD-sing-recite.md](SDD-sing-recite.md)) | |
| **Artistry upgrade + pha giọng (voice blending)** — measured ngâm/hát parameters, reference-melody singing, RVC re-voicing, storyboard hoạt cảnh, consent-gated blend of two voice profiles ([SDD-artistry-voice-blend.md](SDD-artistry-voice-blend.md), design) | |
| **Review & upgrade (v1.6, design)** — safe learning from mistakes (lessons store, reading gate, owner "nghe đúng rồi" + ratings), script lint, staged/resumable video pipeline with storyboard, art direction and mood-driven motion/music, Studio dashboard ([SDD-review-upgrade.md](SDD-review-upgrade.md)) | |
| Video styles: **narrated + AI visuals (16:9)**, **vertical shorts (9:16)**, **slide / screen explainer** | |
| Optional AI video clips (LTX-2 MLX), off by default | |
| REST API + SSE, MCP server for Claude sessions, web dashboard | |

Success criteria
1. `mediaforge video "topic" --lang vi --aspect 9:16 --voice me` produces an MP4 with the
   cloned voice, karaoke subtitles and generated visuals, with no manual steps.
2. Every synthesised sentence passes ASR round-trip (CER ≤ 8 % VI / WER ≤ 10 % EN) or is
   flagged in the job report.
3. Any Claude Code session on this machine can call `mediaforge` MCP tools.

## 2. Hardware and environment

Apple M4 Max · 16 CPU cores · 40-core GPU (Metal 4) · 128 GB unified memory · ~200 GB free.
Python 3.12 (uv), FFmpeg 9 (`ffmpeg`) + `ffmpeg-full` (keg-only, has libass/freetype for
burned-in subtitles), Ollama, Claude Code CLI.

Design consequences
- **MLX first, PyTorch-MPS second, CPU last.** MLX ports are 25–40 % faster than MPS on
  this chip for diffusion/video.
- 128 GB lets us keep TTS (~4 GB), ASR (~1.6 GB) and the image model (~12 GB) resident
  simultaneously; video models (~30–45 GB) are loaded on demand and evicted after.
- One GPU ⇒ **one GPU worker** executes model jobs serially; CPU-only steps (ffmpeg
  encode, text normalisation) may overlap.

## 3. Research summary — what we chose and why

Measured on this machine (spine test, 2026-10-14, warm models):

| Engine | Lang | Synth time / audio | ASR round-trip |
|---|---|---|---|
| VieNeu-TTS v3 Turbo (PyTorch MPS) | VI | 9.3 s cold / ~2 s warm per 5 s | CER 0 % |
| VieNeu-TTS v3 Turbo | EN | 2.0 s / 4.9 s | WER 0 % |
| VoxCPM2 8-bit (MLX) | VI | 4.5 s / 5.0 s | CER 0 % |
| VoxCPM2 8-bit (MLX) | EN | 2.2 s / 4.2 s | WER 0 % |
| Whisper large-v3-turbo (MLX) | both | ~0.5 s per 5 s clip | — |

| Z-Image-Turbo q8 (mflux/MLX) 1344×768, 9 steps | — | ~55 s / image | — |
| Z-Image-Turbo q8, 1024×576, 7 steps ("standard", used for video) | — | ~23 s / image | — |
| Ollama `x/z-image-turbo` 768×1344 | — | ~56 s / image | — |
| End-to-end: 35 s 16:9 video, 5 scenes, Claude-planned | EN | 168 s | all sentences pass |
| End-to-end: 20 s 9:16 short, 3 scenes (Ollama images) | VI | 218 s | all sentences pass |

Notes: VieNeu's ONNX/CPU backend fails with HF-cache symlinks (onnxruntime external-data
path check); we use `backend="pytorch", device="mps"`. bf16 vs q8 vs q4 made no material
difference to Z-Image speed on this machine (52–63 s at 1344×768), so q8 is kept; resolution
and step count are the effective levers (`image.QUALITY`).

### Adopted stack

| Concern | Choice | Repo (≈stars) | Licence |
|---|---|---|---|
| VI voice clone (default) | VieNeu-TTS v3 Turbo | pnnbao97/VieNeu-TTS (2.9k) | Apache-2.0 |
| EN voice clone (default), VI alternate | VoxCPM2 via mlx-audio | OpenBMB/VoxCPM (39k), Blaizzy/mlx-audio (8k) | Apache-2.0 / MIT |
| Fallback TTS (not installed) | OmniVoice (k2-fsa) | k2-fsa/OmniVoice (14.5k) | Apache-2.0 code, **CC-BY-NC weights** (checked 2026-10-15) → do not install; see SDD-artistry-voice-blend.md §8 |
| ASR, word timings, QA | mlx-whisper large-v3-turbo | ml-explore/mlx-examples (9k) | MIT |
| VI text normalisation | vietnormalizer + own lexicon | PyPI vietnormalizer | MIT |
| Singing / chanting resynthesis (f0 replace, syllable re-timing) | WORLD via pyworld | JeremyCCHsu/Python-Wrapper-for-World-Vocoder (1.3k), mmorise/World | MIT / modified BSD |
| Voice FX (pitch w/ formants, EQ, comp) | pedalboard | spotify/pedalboard (6.4k) | GPL-3.0 (internal use only, not distributed) |
| Loudness, de-ess, compose, encode | FFmpeg (`loudnorm`, `deesser`, `zoompan`, `xfade`, `sidechaincompress`, `subtitles`, `hevc_videotoolbox`) | FFmpeg | LGPL/GPL |
| Reference clean-up | noisereduce (MVP); VieNeu built-in denoise | timsainb/noisereduce (1.9k) | MIT |
| Subtitles | own writers (SRT/VTT) + pysubs2 (ASS karaoke) | tkarabela/pysubs2 | MIT |
| Images | mflux Z-Image-Turbo (MLX); fallback Ollama `x/z-image-turbo` | mflux-community/mflux (2.5k) | MIT / Apache-2.0 |
| AI video clips (optional) | ltx-2-mlx (LTX-2.3 distilled, i2v) | dgrauet/ltx-2-mlx | MIT code, LTX community licence weights [VERIFY: commercial use with compliance] |
| LLM (script, scenes, prompts, respelling) | Claude Code CLI `claude -p --output-format json` → fallback Ollama `qwen3:32b` | — | — |
| API | FastAPI + native SSE, SQLite | — | MIT |
| MCP | `mcp` Python SDK, streamable HTTP | modelcontextprotocol/python-sdk | MIT |
| Dashboard | Build-free single-page HTML/JS served by FastAPI (React+Vite upgrade path) | — | — |

Pattern references (studied, not vendored): MoneyPrinterTurbo (130k — script→TTS→
material→subtitle→compose pipeline shape), NarratoAI, whisper.cpp karaoke output.

### Rejected — do not re-add without a licence review
F5-TTS weights and all VI fine-tunes (CC-BY-NC), viXTTS/XTTS (CPML, unmaintained),
Fish-Speech S2 (research licence), Higgs Audio (non-commercial), IndexTTS (bilibili
licence), vinorm (non-commercial, Linux binary), WhisperX VI aligners and
ctc-forced-aligner default model (CC-BY-NC-4.0), Wav2Lip (non-commercial models),
Remotion (company licence), aeneas (AGPL), VibeVoice (TTS removed upstream),
CosyVoice/Chatterbox/Qwen3-TTS/Kokoro (no Vietnamese), DiffusionKit (archived), Seed-VC (GPL-3.0 singing
voice conversion), SoulX-Singer / YingMusic-Singer (no Vietnamese; licences unclear).

## 4. Architecture

```
            ┌───────────── Claude Code sessions ─────────────┐
            │  MCP (streamable HTTP :8765/mcp)               │
            ▼                                                │
 Dashboard (browser) ──HTTP/SSE──►  FastAPI  :8765  ◄── CLI `mediaforge …`
                                     │
                         SQLite (jobs, voices, assets)
                                     │
                        Job queue ─► GPU worker thread (serial)
                                     │
   ┌──────────┬──────────┬───────────┼───────────┬────────────┬──────────┐
   ▼          ▼          ▼           ▼           ▼            ▼          ▼
 LLM       TextNorm   TTSEngine    VoiceFX      ASR/QA     ImageEngine  Composer
 (claude/  (vi/en +   (VieNeu,     (pedalboard  (mlx-      (mflux /     (FFmpeg)
  ollama)   lexicon)   VoxCPM2…)    + ffmpeg)    whisper)   ollama)
```

Single process (`mediaforge serve`) on **127.0.0.1:8765** hosting REST, SSE, MCP and the
dashboard, so models stay loaded once and all clients share one queue.

### 4.1 Module layout

```
src/mediaforge/
  config.py          settings (env MEDIAFORGE_*), paths under ./data
  db.py              SQLite: jobs, voices, assets
  jobs.py            queue, GPU worker thread, progress events, restart recovery
  llm.py             Claude CLI → Ollama fallback; JSON-mode helpers
  models.py          lazy model cache with eviction (LRU by memory class)
  text/normalize.py  language detection, VI/EN normalisation, lexicon, sentence split
  text/documents.py  Word/PDF import → text with [ảnh N] markers + pictures (SDD-doc-import-preview-chibi.md; PDF fix: SDD-pdf-import-fix.md)
  tts/base.py        TTSEngine protocol + SynthResult
  tts/vieneu.py      VieNeu adapter
  tts/voxcpm.py      VoxCPM2 (mlx-audio) adapter
  voice/profiles.py  voice profile CRUD, reference preparation
  voice/fx.py        pitch/formant, pace, clarity chain, loudness
  voice/speak.py     sentence-level synth + QA + self-repair loop
  voice/blend.py     pha giọng: consent, speaker-vector blend calibration, blend profiles (SDD-artistry-voice-blend.md §6)
  asr.py             transcribe, word timings, script alignment, similarity score
  subtitles.py       cue building, SRT/VTT/ASS karaoke writers
  image.py           image generation
  video/compose.py   FFmpeg composition (Ken Burns, xfade, subs, music ducking)
  video/slides.py    slide rendering (Pillow)
  video/clips.py     optional LTX-2 clips
  pipelines.py       narrated / short / explainer orchestrations
  takes.py           guided sentence re-render ("takes"), sentence spans, splice
  export.py          export formats table + remux/recompose export job
  music.py           background-music library (music_catalog.json metadata; audio in data/music/)
  sing/              hát & ngâm thơ: tones.py, melody.py (tone-aware), resynth.py (WORLD), accompany.py,
                     poems.py + poems_catalog.json, pipeline.py — see SDD-sing-recite.md
  api.py             FastAPI app (+ dashboard static, + MCP mount)
  mcp_server.py      MCP tools (thin wrappers over the job layer)
  cli.py             Typer CLI
  web/               dashboard (index.html, app.js, style.css)
```

### 4.2 The engine boundary (the design decision that matters)

```python
class TTSEngine(Protocol):
    name: str; languages: set[str]; sample_rate: int
    def synth(self, text: str, lang: str, ref: VoiceRef, *, seed: int, temperature: float) -> np.ndarray
```
Engines churn every few months; everything above this line (QA, FX, subtitles, video)
is engine-agnostic. Adding an engine = one adapter file + a registry entry.
Routing default: `vi → vieneu`, `en → voxcpm`, configurable per voice and per request;
`engine="best"` synthesises with every capable engine and keeps the best QA score.

## 5. Voice pipeline (core feature)

### 5.1 Enrolment (one-off, per voice)
1. Owner records in the dashboard (MediaRecorder → webm) or uploads audio. Guided
   prompts: ≥3 Vietnamese and ≥3 English sentences, 5–15 s each, quiet room.
2. Convert to 48 kHz mono WAV → trim silence → noise reduction → peak-normalise.
3. Transcribe each sample (mlx-whisper) to obtain `ref_text`; owner can correct it.
4. Pick the best reference per language (SNR, duration 6–12 s, ASR confidence).
5. Store under `data/voices/<id>/` with `profile.json` (default pitch/pace/clarity,
   preferred engine per language). **Voice data never leaves the machine.**

### 5.2 Synthesis — `speak(text, voice, lang, params)`
1. **Normalise**: language detect per sentence (`auto` supports mixed EN/VI);
   VI → `vietnormalizer` (numbers, dates, currency, units); EN → small rule normaliser;
   then the **pronunciation lexicon** (global + per-voice JSON, e.g. `API → ây-pi-ai`).
2. **Split** into sentences/clauses ≤ 220 chars.
3. **Synthesise** each sentence with the routed engine (fixed seed for reproducibility).
4. **QA**: resample to 16 kHz → Whisper → compare with the normalised input.
   Score = mean(syllable WER, CER) for VI (CER alone let "Hà Nội → Hà Lội" pass at 3.6 %),
   WER for EN. Lexicon terms and brand-like tokens (MediaForge, Block8, API) are passed to
   Whisper as an `initial_prompt` hint and diff blocks made only of them are excluded, because
   ASR cannot spell back a correctly spoken brand name ("Media4"). Duration sanity
   (tokens/sec) catches babble/truncation.
5. **Self-repair** on failure (max 4 attempts per sentence, keep best):
   a. new seed + lower temperature; b. switch engine; c. ask the LLM to propose
   phonetic respellings for the mismatched words (diff of expected vs. heard), apply
   them for this attempt, and record successful respellings into the voice's lexicon
   as `learned` entries (visible/editable in the dashboard).
6. **Join** sentences with natural pauses (sentence 250 ms, clause 120 ms).
7. **Voice FX** (`voice/fx.py`):
   - `pitch` (semitones, −6…+6; negative = deeper) — pedalboard `time_stretch(...,
     pitch_shift_in_semitones, preserve_formants=True)`.
   - `pace` (0.7…1.4) — same call, `stretch_factor`.
   - `clarity` (0…1) — high-pass 80 Hz, presence peak 3–4 kHz, gentle compression,
     FFmpeg `deesser`; `warmth` (0…1) — low-shelf 150–250 Hz for a fuller, "trầm" timbre.
   - Final loudness `loudnorm I=-16 TP=-1.5 LRA=11`, 48 kHz.
8. **Cache** by `sha256(normalised_text, voice, engine, params)` so re-renders are free.
9. Output: WAV (+ MP3), word timings, QA report JSON (per sentence: score, heard, missing/extra words).
10. **Human-in-the-loop repair (guided)** — full design in [SDD-guided-repair.md](SDD-guided-repair.md).
    The dashboard shows failed sentences in red with the misheard words underlined.
    - **Recording as a guide.** The owner records the failed sentence. The recording is a **guide**,
      never part of the output.
    - **Storage.** It is stored for good under `voices/<id>/corrections/` and also enrolled as a
      `correction` sample.
    - **Re-render.** The AI re-renders the sentence as a **take**, using the job's own reference clip
      and voice FX. The recording supplies the phrasing (the owner's pauses → phrase-wise synthesis)
      and, for VoxCPM2, the prosody prompt.
    - **Learning.** Later renders of that sentence pick up the guide automatically.
    - **Export.** Takes are listed under the video. Exporting splices the chosen takes into the voice
      track and remuxes; the video stream is copied, not re-rendered. A recompose runs only when a take
      cannot fit its slot.
    - **Legacy.** `POST /api/jobs/{id}/patch` (full re-render, recording replaces the synthesis) remains
      for API clients only.
    - **Fix a word everywhere.** **🗣 Sửa phát âm** on a finished job takes `term → reading` (e.g. `PNJ → Pi En Di`).
      It saves the reading to the lexicon as `manual`, re-reads every sentence containing the term as a take,
      and exports a remuxed MP4. There is no video re-render. Design: [SDD-repronounce.md](SDD-repronounce.md).
    - **Punctuation split.** The automatic repair loop also tries phrase-wise synthesis split at internal
      punctuation before asking the LLM, because long sentences truncate or babble.

Sample-rate contract: engines 48 kHz (VoxCPM2/VieNeu) or 24 kHz (OmniVoice) → resampled
to 48 kHz on exit; ASR always receives 16 kHz mono.

## 6. Subtitles
- Because we **know the script**, cue text comes from the script, not from ASR.
  Whisper word timestamps on the final audio are mapped onto script tokens with
  `difflib.SequenceMatcher` (unmatched tokens interpolated). All-permissive licences.
- Vietnamese "words" are syllables → line breaking by **character count and measured pixel width**
  (42 chars landscape, 26 chars vertical; ≤2 lines; ≤ 6 s per cue). Each sentence is split into cues by dynamic
  programming (balanced, prefers punctuation, avoids splitting spoken numbers and orphan words); no line can exceed
  the frame's safe width. Details: [SDD-subtitle-fit-pronunciation.md](SDD-subtitle-fit-pronunciation.md) §3.
- Writers: SRT, VTT, ASS (styles per aspect; `WrapStyle: 0` safety net; karaoke word *pop* — spoken word turns
  yellow and grows 8 % — on by default for every aspect).
- `resubtitle` job rebuilds a finished video's subtitles from its stored word timings, voice track and images.
- `subtitle` job also works on arbitrary user audio/video (transcribe → cues), with
  optional translation EN↔VI via the LLM.

## 7. Visuals and video composition

### 7.1 Narrated video pipeline (`pipelines.narrated`)
1. **Plan** (LLM): topic/brief → JSON `{title, scenes:[{narration, visual_prompt,
   duration_hint}]}` in the target language; visual prompts always English.
2. **Voice**: `speak` per scene → per-scene WAV + timings (scene length = audio length
   + 0.3 s tail).
3. **Visuals** per scene: image (mflux Z-Image-Turbo, "standard" 1024×576 / 7 steps, seeded,
   style preset; falls back to Ollama while the MLX weights are not fully cached)
   or AI clip (LTX-2 i2v from that image, when `clips=true`), or user-supplied media, or a picture from an
   imported Word/PDF (`scene.image`, fitted whole by `slides.figure_frame`, never cropped). Style presets:
   cinematic, photo, illustration, 3d, minimal, tech, **chibi** (the LLM is told to put cute chibi characters
   acting out each scene) — see SDD-doc-import-preview-chibi.md.
4. **Compose** (FFmpeg): Ken Burns (`zoompan` on pre-upscaled stills, alternating
   zoom/pan directions), `xfade` transitions, voice track, optional music with
   `sidechaincompress` ducking, `loudnorm`, subtitles burned (ASS via `ffmpeg-full`) and
   attached as soft track; H.264 (default, compatibility) or HEVC VideoToolbox.
5. Outputs: `final.mp4`, `subs.srt/.vtt/.ass`, `voice.wav`, `plan.json`, thumbnail; `result.sentence_spans`
   (sentence positions in `voice.wav`), and later `takes/` and `exports/` (see SDD-guided-repair.md §5).

**Music library.** `music` is either a local path or an id from `music_catalog.json`, a curated
list of calm instrumental tracks that are CC0 / public domain or CC BY (recording licence checked,
not just the composition; NC/ND/SA and platform-licensed sites such as Pixabay are excluded).
`mediaforge music sync` downloads them once into `data/music/`; ids are resolved before any
rendering so a missing file fails fast. CC BY tracks carry an `attribution` line that the job
result exposes as `report.music_credit` and the dashboard shows under the video.

### 7.2 Variants
- **Short (9:16)**: 1080×1920, ≤ 60 s narration target, punchy hook-first script, large
  centred karaoke subtitles.
- **Explainer**: input = markdown slides (`# title` / bullets per slide, `> note` =
  narration) or a brief the LLM turns into slides; slides rendered by Pillow; or
  input = a screen recording + script → voice-over + subtitles laid over the recording.

## 8. LLM layer
`llm.complete(prompt, json_schema=None)`:
1. `claude -p <prompt> --output-format json --model sonnet` (env stripped of
   `CLAUDECODE`/`CLAUDE_CODE_*` so nested calls work), timeout 180 s.
2. On error/rate limit/timeout → Ollama `qwen3:32b` (`/api/chat`, `format=json` when a
   schema is requested). Configurable via `MEDIAFORGE_LLM_ORDER=claude,ollama`.
Uses: scene planning, image prompts, respellings for pronunciation repair, translation.

## 9. Interfaces

### 9.1 REST (127.0.0.1:8765, JSON)
| Method & path | Purpose |
|---|---|
| `GET /api/health` | status, loaded models, queue depth |
| `GET/POST /api/voices`, `GET/PATCH/DELETE /api/voices/{id}` | voice profiles |
| `POST /api/voices/{id}/samples` | multipart upload/recording → processed sample |
| `GET/PUT /api/voices/{id}/lexicon`, `GET/PUT /api/lexicon` | pronunciation lexicons |
| `POST /api/jobs` `{type, params}` | type ∈ `tts, image, subtitle, video, short, explainer, clip, prepare, sing, recite` (+ internal `rerender`, `export`, `resubtitle`, `repronounce`, `blend`) |
| `GET /api/poems`, `GET /api/sing/moods` | Tang poem catalogue (Ngâm thơ tab); moods/styles for sing/recite |
| `POST /api/jobs/{id}/sentences/{i}/rerender` | `{file?, lang?}` — AI re-renders sentence *i* as a take (recording = guide) |
| `POST /api/jobs/{id}/repronounce` | `{readings: {term: reading}, format?}` — re-read every sentence containing a term, export (no re-render); `GET /api/jobs/{id}/occurrences?terms=` previews matches |
| `POST /api/jobs/{id}/resubtitle` | rebuild subtitles of a finished video (no TTS/image work) |
| `POST /api/lexicon/terms` | `{lang, terms: {term: reading}, verified: [term]}` — merge pre-scan answers |
| `POST /api/jobs/{id}/export` | `{selection: {i: take_id}, format}` — mp4 (default), mov, mkv, webm, mp3, wav, m4a |
| `GET /api/export-formats`, `GET /api/voices/{id}/corrections` | format catalogue; a voice's sentence guides |
| `GET /api/jobs`, `GET /api/jobs/{id}` | list / detail (status, progress, result, report) |
| `GET /api/jobs/{id}/events` | SSE progress stream |
| `POST /api/jobs/{id}/cancel`, `DELETE /api/jobs/{id}` | control |
| `GET /files/{path}` | download outputs (path-confined to `data/`) |
| `POST /api/uploads` | upload media for later jobs |
| `POST /api/documents` | multipart Word/PDF → `{text with [ảnh N] markers, images, title, warnings}`; video params take `doc_images` |
| `GET /api/music`, `GET /api/music/{id}/audio` | background-music library / preview |
| `GET/POST /api/blends`, `PATCH /api/blends/{id}` | pha giọng: blend profiles (`{owner, partner, amount}` → calibration job); partner needs `consent` (`PATCH /api/voices/{id}`) |

`?wait=true` on `POST /api/jobs` blocks until completion (≤ 600 s) for simple clients.

### 9.2 MCP (streamable HTTP at `http://127.0.0.1:8765/mcp`)
Tools: `speak`, `blend_voices`, `generate_image`, `prepare_video`, `create_video`, `resubtitle_video`, `repronounce_video`, `recite_poem`, `sing_song`, `list_poems`,
`transcribe_subtitles`, `job_status`, `list_voices`, `list_jobs`, `list_music`. Long jobs return a job id
immediately plus a `wait_seconds` option; results return absolute file paths (sessions
run on the same machine). `create_video`/`prepare_video` take `document` (+ `document_as` brief|script) to use a
Word/PDF and its pictures; voice settings left unset come from the voice profile (the owner's last setup). Registered with
`claude mcp add -s user --transport http mediaforge http://127.0.0.1:8765/mcp`.

### 9.3 CLI
`mediaforge serve | speak | image | video | short | explainer | subtitle | recite | sing | poems | voice … (incl. `voice consent`, `voice blend`) | music list|sync | doctor`.
CLI talks to the running server when available, otherwise runs in-process.

## 10. Phases
| Phase | Deliverable | Status |
|---|---|---|
| P0 Spine | TTS adapters, ASR QA, FX, CLI `speak` | Done 2026-10-14 |
| P1 Video | subtitles, images, composer, narrated/short/explainer/screen pipelines | Done 2026-10-14 |
| P2 Service | FastAPI + jobs + SSE, MCP, `claude mcp add`, launchd | Done 2026-10-14 |
| P3 Dashboard | record/enrol voice, lexicon editor, job launcher, gallery | Done 2026-10-14 |
| P3b Hát & Ngâm thơ | tone-aware melody, WORLD resynthesis of the cloned voice, đàn tranh/sáo accompaniment, video hoạt cảnh, dashboard tabs | Done 2026-10-14 |
| P5 Artistry + pha giọng | **pha giọng done 2026-10-15** (VieNeu speaker-vector blend, calibrated: 25 % target → 25.5 % measured; consent-gated). Designed: measured ngâm/hát parameters, reference-melody mode, RVC re-voicing, storyboard hoạt cảnh — [SDD-artistry-voice-blend.md](SDD-artistry-voice-blend.md) §9 | P1a done, rest open |
| P6 Review & upgrade | P6-A learning hygiene + lint, P6-B lessons store + ratings + job page, P6-C staged pipeline + storyboard, P6-D creative direction — [SDD-review-upgrade.md](SDD-review-upgrade.md) §8 (review found tone-stripping `learned` lexicon entries, e.g. `độ → đô`) | Designed 2026-10-15 |
| P4 Later | install ltx-2-mlx and enable clips; music generation (ACE-Step, Apache-2.0 — evaluate); speaker-similarity score (WavLM-SV) to auto-pick the best reference and check take timbre; fine-tune on `corrections/`; broader automated tests (unit tests for guided repair/export exist in `tests/`); React upgrade | Open |

**Owner action required:** record real samples (dashboard → Voices → Record). Until then
a placeholder voice (macOS `say`) proves the pipeline; "sounds like me" can only be
judged after enrolment.

## 11. Non-functional
- **Security/privacy**: bind 127.0.0.1 only; voice recordings are biometric personal
  data — stored only under `data/` (git-ignored), never sent to Claude/LLM (only text is),
  deletable from the dashboard. Generated audio carries VieNeu's inaudible watermark by
  default. File serving is confined to `data/`.
- **Service**: launchd user agent `ai.block8.mediaforge` (`scripts/service.sh`). macOS TCC
  blocks background agents from reading `~/Documents`, so the agent runs a non-editable copy
  from `uv tool install` (pinned to `uv.lock`) and keeps data in `~/.local/share/mediaforge`;
  the project's `data/` is a symlink to it. Code changes go live with `scripts/service.sh update`.
- **Job scheduling**: priority queue — enrolment > tts/image > subtitle > video — so the
  dashboard's recording and preview stay responsive during long renders.
- **Data safety**: recordings are never deleted by the app. Every voice mutation writes a
  `data/backups/voices-*.tar.gz` snapshot (keep 30); deletes move to `data/trash/`; raw uploads
  stay in `voices/<id>/raw/`. `service.sh update` waits for the running job before restarting.
- **Reliability**: jobs persisted; on restart `running` → `failed (interrupted)`,
  `queued` resumes. Every job writes `report.json` (timings, QA per sentence, engine).
- **Performance targets** (warm): 60 s narration < 60 s TTS incl. QA; 1024² image ≲ 15 s;
  60 s 1080p video (stills) < 4 min end-to-end.
- **Observability**: structured logs in `data/logs/`, `/api/health`, dashboard queue view.
- **Licences**: see §3; GPL components acceptable because the tool is internal and not
  distributed. LTX weights licence to be confirmed before any commercial output
  [VERIFY: confirm with compliance].
