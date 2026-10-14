# MediaForge

The local media engine for this Mac. It makes **speech in your own cloned voice (English + Vietnamese)**, **subtitles**, **images** and **videos** (narrated 16:9, vertical 9:16 shorts, slide explainers, screen-recording voice-overs). It's exposed as a **dashboard**, a **REST API**, an **MCP server** for every Claude Code session, and a **CLI**.

Design and the reasons behind each choice: [`docs/SDD.md`](docs/SDD.md).

## Quick start

```bash
cd ~/Documents/voice_video
uv sync                                  # one-off
scripts/service.sh install               # run in the background at login (127.0.0.1:8765)
open http://127.0.0.1:8765               # dashboard
```

The first request downloads models into `~/.cache/huggingface` (VieNeu ≈ 1 GB, VoxCPM2 8-bit ≈ 3 GB, Whisper turbo ≈ 1.6 GB, Z-Image-Turbo ≈ 20 GB).

## 1. Clone your voice (once)

Dashboard → **Giọng nói** → create a voice → pick Tiếng Việt or English → read the suggested sentence → **● Ghi âm** / **■ Dừng**.

- Record at least **3 Vietnamese and 3 English** samples of **5–12 s** each, in a quiet room with the mic 15–20 cm away.
- Each sample is converted, trimmed, denoised and transcribed. The best clip per language becomes the reference (★).
- Set the voice as default (★) so every request uses it automatically.
- Tune the voice's defaults and click **▶ Nghe thử** to hear the result:
  - **Độ trầm** (pitch): −6 to +6 semitones. Formants are preserved so it stays natural.
  - **Tốc độ** (pace)
  - **Độ ấm** (warmth)
  - **Độ rõ** (clarity)

CLI alternative:

```bash
uv run mediaforge voice create "Tu"                       # also sets it as default
uv run mediaforge voice record tu --lang vi --count 3     # records from the default mic
uv run mediaforge voice record tu --lang en --count 3
uv run mediaforge voice add tu ~/Desktop/old_talk.m4a     # or import existing recordings
uv run mediaforge voice set tu --pitch -1.5 --warmth 0.4 --clarity 0.7
```

Recordings stay in `data/voices/` on this machine (git-ignored) and are never sent to any LLM.

## 2. Make things

| Want | Dashboard | CLI |
|---|---|---|
| Voice-over | Tạo mới → 🎙 | `mediaforge speak "Xin chào…" --pitch -2` |
| Vertical short | 📱 Short | `mediaforge short "5 mẹo bảo mật mật khẩu" --lang vi` |
| 16:9 video | 🎬 Video | `mediaforge video "How MFA stops account takeover" --lang en --seconds 60` |
| Slide explainer | 🧑‍🏫 Explainer | `mediaforge explainer --slides deck.md` |
| Voice-over on a screen recording | — | `mediaforge screen demo.mov "@script.txt"` |
| Images | 🖼 Ảnh | `mediaforge image "a cozy café in Hanoi" --count 4` |
| Subtitles for any media | 💬 Phụ đề | `mediaforge subtitle talk.mp4 --burn --translate-to en` |

Notes on the inputs:

- Pass `--script "@file.txt"` to read your own narration word for word. Without it, Claude writes the script from your brief.
- For explainers, slide markdown looks like this:
  - `# Title` starts a slide
  - `- bullet` lines are bullet points
  - `> narration` lines are what the voice says
- Add `--music bg.mp3` for background music. It's automatically ducked under the voice.

Outputs are written to `data/outputs/<kind>/<job-id>/`:

- `final.mp4`
- `voice.wav` / `voice.mp3`
- `subtitles.srt`, `.vtt` and `.ass` (karaoke for shorts)
- `plan.json`
- `report.json` (pronunciation QA for each sentence)

## 3. Use from other Claude Code sessions

The MCP server is registered at user scope:

```bash
claude mcp add -s user --transport http mediaforge http://127.0.0.1:8765/mcp
```

| Tool | What it does |
|---|---|
| `speak` | Text → cloned-voice audio, with pitch, pace, warmth and clarity controls. Returns wav, mp3, srt, vtt and ass. |
| `generate_image` | Prompt → image(s) |
| `create_video` | `kind`: `narrated`, `short` or `explainer`. Input is a brief, your own script, or slide markdown. Returns a `job_id`. |
| `voiceover_recording` | Narration and subtitles over a screen recording |
| `transcribe_subtitles` | Media → SRT/VTT/ASS, with optional burn-in and translation |
| `job_status`, `list_jobs`, `list_voices` | Track jobs and list voices |

REST (`/docs` has the full OpenAPI). For example:

```bash
curl -s "localhost:8765/api/jobs?wait=true" -H 'content-type: application/json' \
  -d '{"type":"tts","params":{"text":"Xin chào","lang":"vi","fx":{"pitch":-2}}}'
curl -s localhost:8765/api/jobs -H 'content-type: application/json' \
  -d '{"type":"short","params":{"brief":"3 tips to spot phishing","lang":"en"}}'
curl -N localhost:8765/api/jobs/<id>/events          # live progress (SSE)
```

Job types: `tts`, `image`, `subtitle`, `narrated`, `short`, `explainer`, `screen`, `enrol`.

## How pronunciation is kept correct

1. **Normalisation.** Numbers, dates, currency and units are written out as words (`vietnormalizer` for Vietnamese).
2. **Pronunciation lexicon.** Applied next. Edit it in the dashboard under **Phát âm**.
3. **Speech-recognition check.** Every sentence is synthesised and transcribed back with Whisper large-v3-turbo, then compared with the script:
   - Vietnamese is scored as the mean of syllable WER and CER.
   - English is scored as WER.
4. **Automatic repair.** If a sentence fails, MediaForge tries, in order:
   1. A new seed with a calmer temperature
   2. The other engine
   3. Claude proposing phonetic respellings for the misheard words
5. **Learning.** A respelling that fixes a sentence is saved to the lexicon as `learned`.
6. **Reporting.** Sentences that still fail are listed in the job report and shown in the dashboard.

## Engines (all local, on Apple Silicon)

| Purpose | Engine |
|---|---|
| Vietnamese voice | VieNeu-TTS v3 Turbo (PyTorch MPS) |
| English voice (also Vietnamese) | VoxCPM2 8-bit (MLX) |
| Speech recognition and subtitles | Whisper large-v3-turbo (MLX) |
| Images | Z-Image-Turbo via mflux (MLX), falling back to Ollama `x/z-image-turbo` |
| Scripts, image prompts, respellings and translation | Claude Code CLI (`claude -p`), falling back to Ollama `qwen3:32b` |
| Voice FX | pedalboard |
| Composition | FFmpeg (`ffmpeg-full`, which includes libass for burned-in subtitles) |
| AI video clips (optional) | `ltx-2-mlx`. See `src/mediaforge/video/clips.py`, then pass `clips: true`. |

## Operations

```bash
scripts/service.sh status | restart | logs | uninstall
scripts/service.sh update                                 # after changing code: reinstall + restart
uv run mediaforge doctor
curl -X POST localhost:8765/api/models/unload            # free GPU memory
```

Configuration uses environment variables:

| Variable | Default |
|---|---|
| `MEDIAFORGE_PORT` | 8765 |
| `MEDIAFORGE_LLM_ORDER` | `claude,ollama` |
| `MEDIAFORGE_CLAUDE_MODEL` | `sonnet` |
| `MEDIAFORGE_OLLAMA_MODEL` | `qwen3:32b` |
| `MEDIAFORGE_QA_MAX_CER_VI` | 0.08 |
| `MEDIAFORGE_QA_MAX_WER_EN` | 0.10 |

See `src/mediaforge/config.py` for the rest.

The background service runs an installed copy of the code (`uv tool install`) and keeps its data in `~/.local/share/mediaforge`. This is because macOS doesn't let background services read `~/Documents`. The project's `data/` folder is a symlink to that location, so outputs still appear in the project.

The server binds to 127.0.0.1 only and has no authentication. Don't expose it to a network.
