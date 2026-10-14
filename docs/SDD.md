# MediaForge — Software Design Document

Status: v1.0 · 2026-10-14 · Owner: tu.dev@block8.ai · Internal tool, localhost only

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

Note: VieNeu's ONNX/CPU backend fails with HF-cache symlinks (onnxruntime external-data
path check); we use `backend="pytorch", device="mps"`.

### Adopted stack

| Concern | Choice | Repo (≈stars) | Licence |
|---|---|---|---|
| VI voice clone (default) | VieNeu-TTS v3 Turbo | pnnbao97/VieNeu-TTS (2.9k) | Apache-2.0 |
| EN voice clone (default), VI alternate | VoxCPM2 via mlx-audio | OpenBMB/VoxCPM (39k), Blaizzy/mlx-audio (8k) | Apache-2.0 / MIT |
| Fallback TTS | OmniVoice (k2-fsa) | k2-fsa/OmniVoice (14.5k) | Apache-2.0 |
| ASR, word timings, QA | mlx-whisper large-v3-turbo | ml-explore/mlx-examples (9k) | MIT |
| VI text normalisation | vietnormalizer + own lexicon | PyPI vietnormalizer | MIT |
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
CosyVoice/Chatterbox/Qwen3-TTS/Kokoro (no Vietnamese), DiffusionKit (archived).

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
  tts/base.py        TTSEngine protocol + SynthResult
  tts/vieneu.py      VieNeu adapter
  tts/voxcpm.py      VoxCPM2 (mlx-audio) adapter
  voice/profiles.py  voice profile CRUD, reference preparation
  voice/fx.py        pitch/formant, pace, clarity chain, loudness
  voice/speak.py     sentence-level synth + QA + self-repair loop
  asr.py             transcribe, word timings, script alignment, similarity score
  subtitles.py       cue building, SRT/VTT/ASS karaoke writers
  image.py           image generation
  video/compose.py   FFmpeg composition (Ken Burns, xfade, subs, music ducking)
  video/slides.py    slide rendering (Pillow)
  video/clips.py     optional LTX-2 clips
  pipelines.py       narrated / short / explainer orchestrations
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
4. **QA**: resample to 16 kHz → Whisper → normalised CER (VI) / WER (EN) vs. the
   normalised input. Also check duration sanity (chars/sec) to catch babble/truncation.
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
9. Output: WAV (+ MP3), word timings, QA report JSON.

Sample-rate contract: engines 48 kHz (VoxCPM2/VieNeu) or 24 kHz (OmniVoice) → resampled
to 48 kHz on exit; ASR always receives 16 kHz mono.

## 6. Subtitles
- Because we **know the script**, cue text comes from the script, not from ASR.
  Whisper word timestamps on the final audio are mapped onto script tokens with
  `difflib.SequenceMatcher` (unmatched tokens interpolated). All-permissive licences.
- Vietnamese "words" are syllables → line breaking by **character width**
  (42 chars landscape, 26 chars vertical; ≤2 lines; ≤ 6 s per cue; break at punctuation
  first).
- Writers: SRT, VTT, ASS (styles per aspect; karaoke `\k` word highlight for shorts).
- `subtitle` job also works on arbitrary user audio/video (transcribe → cues), with
  optional translation EN↔VI via the LLM.

## 7. Visuals and video composition

### 7.1 Narrated video pipeline (`pipelines.narrated`)
1. **Plan** (LLM): topic/brief → JSON `{title, scenes:[{narration, visual_prompt,
   duration_hint}]}` in the target language; visual prompts always English.
2. **Voice**: `speak` per scene → per-scene WAV + timings (scene length = audio length
   + 0.3 s tail).
3. **Visuals** per scene: image (mflux Z-Image-Turbo, 9 steps, seeded, style preset)
   or AI clip (LTX-2 i2v from that image, when `clips=true`), or user-supplied media.
4. **Compose** (FFmpeg): Ken Burns (`zoompan` on pre-upscaled stills, alternating
   zoom/pan directions), `xfade` transitions, voice track, optional music with
   `sidechaincompress` ducking, `loudnorm`, subtitles burned (ASS via `ffmpeg-full`) and
   attached as soft track; H.264 (default, compatibility) or HEVC VideoToolbox.
5. Outputs: `final.mp4`, `subs.srt/.vtt/.ass`, `voice.wav`, `plan.json`, thumbnail.

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
| `POST /api/jobs` `{type, params}` | type ∈ `tts, image, subtitle, video, short, explainer, clip` |
| `GET /api/jobs`, `GET /api/jobs/{id}` | list / detail (status, progress, result, report) |
| `GET /api/jobs/{id}/events` | SSE progress stream |
| `POST /api/jobs/{id}/cancel`, `DELETE /api/jobs/{id}` | control |
| `GET /files/{path}` | download outputs (path-confined to `data/`) |
| `POST /api/uploads` | upload media for later jobs |

`?wait=true` on `POST /api/jobs` blocks until completion (≤ 600 s) for simple clients.

### 9.2 MCP (streamable HTTP at `http://127.0.0.1:8765/mcp`)
Tools: `speak`, `generate_image`, `create_video`, `create_short`, `create_explainer`,
`transcribe_subtitles`, `job_status`, `list_voices`, `list_jobs`. Long jobs return a job id
immediately plus a `wait_seconds` option; results return absolute file paths (sessions
run on the same machine). Registered with
`claude mcp add -s user --transport http mediaforge http://127.0.0.1:8765/mcp`.

### 9.3 CLI
`mediaforge serve | speak | image | video | short | explainer | subtitle | voice … | doctor`.
CLI talks to the running server when available, otherwise runs in-process.

## 10. Phases
| Phase | Deliverable | Status |
|---|---|---|
| P0 Spine | TTS adapters, ASR QA, FX, CLI `speak` | |
| P1 Video | subtitles, images, composer, narrated/short/explainer pipelines | |
| P2 Service | FastAPI + jobs + SSE, MCP, `claude mcp add` | |
| P3 Dashboard | record/enrol voice, lexicon editor, job launcher, gallery | |
| P4 Later | LTX-2 clips by default when fast enough; OmniVoice adapter; music generation (ACE-Step, Apache-2.0 — evaluate); speaker-similarity score (WavLM-SV); React upgrade | |

**Owner action required:** record real samples (dashboard → Voices → Record). Until then
a placeholder voice (macOS `say`) proves the pipeline; "sounds like me" can only be
judged after enrolment.

## 11. Non-functional
- **Security/privacy**: bind 127.0.0.1 only; voice recordings are biometric personal
  data — stored only under `data/` (git-ignored), never sent to Claude/LLM (only text is),
  deletable from the dashboard. Generated audio carries VieNeu's inaudible watermark by
  default. File serving is confined to `data/`.
- **Reliability**: jobs persisted; on restart `running` → `failed (interrupted)`,
  `queued` resumes. Every job writes `report.json` (timings, QA per sentence, engine).
- **Performance targets** (warm): 60 s narration < 60 s TTS incl. QA; 1024² image ≲ 15 s;
  60 s 1080p video (stills) < 4 min end-to-end.
- **Observability**: structured logs in `data/logs/`, `/api/health`, dashboard queue view.
- **Licences**: see §3; GPL components acceptable because the tool is internal and not
  distributed. LTX weights licence to be confirmed before any commercial output
  [VERIFY: confirm with compliance].
