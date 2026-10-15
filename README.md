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

Nothing you record is ever destroyed by the app:

- **Backups:** every change to a voice writes a snapshot to `data/backups/voices-*.tar.gz` (the last 30 are kept).
- **Trash, not deletion:** deleting a sample, a voice or a job's files moves it to `data/trash/`.
- **Raw recordings:** the originals are always kept in `data/voices/<id>/raw/`.

To restore a snapshot:

```bash
tar -xzf data/backups/<snapshot>.tar.gz -C data/
```

### Pha giọng (voice blending)

Make a voice that stays **mostly yours** with a share (5–50 %, 20–30 % recommended) of a second voice.

1. Enrol the second voice like any voice (record or upload samples).
2. On that voice's page, section **3. Cho phép dùng làm giọng pha**, record whose voice it is and that they agreed:
   your own voice, a synthetic voice, or written permission (with who agreed). Without this the blend is refused:
   a voice is biometric data of a real person.
3. On *your* voice's page, section **4. Pha giọng**: pick the partner, set **Mức pha**, click **Tạo giọng pha**.
   It calibrates in about 30 s, so the share you set is the share you get, then opens the new voice.
   Use **▶ Nghe A · Pha · B** to compare, and the slider to change the share later (instant).
4. Pick the new voice anywhere: Tạo mới, Ngâm thơ, Hát, MCP, CLI. It always renders with VieNeu.

```bash
uv run mediaforge voice consent friend written --by "Tên người đồng ý"
uv run mediaforge voice blend tu friend --amount 0.25
```

### Giọng mẫu (ready-made voices) and the default voice

Voices tab → **Giọng mẫu**: 12 ready-made Vietnamese presenter voices (Nữ/Nam · Bắc/Trung/Nam · Tin tức / Tự nhiên
/ Kể chuyện / MC) with a sample that plays at once (▶). **Dùng** picks one for the next jobs; **★** makes it the
**default voice** — the one every request without a voice uses (dashboard "Mặc định", MCP `speak`/`create_video`,
CLI). The two Central voices (Nữ/Nam · Trung · Tự nhiên) are new; press **Tạo bản nghe thử** once to render their
samples. Other ways to set the default: `mediaforge voice default <id>`, MCP `set_default_voice(voice_id)`,
or **Đặt mặc định** on one of your own voices.

## 2. Make things

| Want | Dashboard | CLI |
|---|---|---|
| Voice-over | Tạo mới → 🎙 | `mediaforge speak "Xin chào…" --pitch -2` |
| Vertical short | 📱 Short | `mediaforge short "5 mẹo bảo mật mật khẩu" --lang vi` |
| 16:9 video | 🎬 Video | `mediaforge video "How MFA stops account takeover" --lang en --seconds 60` |
| Slide explainer | 🧑‍🏫 Explainer | `mediaforge explainer --slides deck.md` |
| Animated presentation (moving titles, count-up numbers, 3D) | 🧑‍🏫 Explainer → Kiểu slide: Động | `mediaforge explainer "brief" --animated` |
| Voice-over on a screen recording | — | `mediaforge screen demo.mov "@script.txt"` |
| Video from a Word/PDF (pictures kept) | 📄 Nhập từ Word/PDF | `mediaforge video --document report.pdf --style chibi` |
| Images | 🖼 Ảnh | `mediaforge image "a cozy café in Hanoi" --count 4 --style chibi` |
| Subtitles for any media | 💬 Phụ đề | `mediaforge subtitle talk.mp4 --burn --translate-to en` |
| Chant a poem (ngâm thơ) | 🪷 Ngâm thơ | `mediaforge recite "@tho.txt" --video` or `mediaforge recite --poem hoang-hac-lau` |
| Sing lyrics | 🎵 Hát | `mediaforge sing "@loi.txt" --mood hung-trang --video` |

For videos, the dashboard has two buttons:

- **1 · Viết kịch bản & kiểm tra phát âm** (recommended) writes the script first and lists the words the AI may
  misread: acronyms (`PNJ`, `GRI`), letter+digit codes (`V1`, `CO2`), foreign names and words (`Carroll`,
  `livestream`). Type how each is read (`PNJ` → `Pi En Di`), tick **đúng** if it already reads fine, or press ▶ to
  hear the sentence first. **2 · Lưu cách đọc & dựng video** saves your answers to the pronunciation lexicon
  (they are reused in every later video) and renders exactly the script you checked.
- **Dựng ngay** skips the check.

Notes on the inputs:

- **📄 Nhập từ Word/PDF** (under *Nội dung*) reads a `.docx` or `.pdf` (`.doc/.rtf/.odt` are converted) into the
  text box. Each picture becomes a marker such as `[ảnh 1]` where it sat in the document, with a thumbnail below:
  - The video shows that picture whole on the scene after its marker. Move or delete markers to change this, or
    press ✕ on a thumbnail to drop the picture.
  - With *Ý tưởng → Claude viết kịch bản*, Claude places the pictures on the matching scenes.
  - A voice-over skips the markers.
  - Scanned PDFs (no text layer) are not supported yet.
- **Phong cách hình → chibi** draws every AI scene with small, cute cartoon characters acting out the narration.
- **Chất giọng** is saved on the chosen voice as you move the sliders and reloaded next time. MCP/API calls that
  don't pass pitch/pace/warmth/clarity use these settings too, and so does the default key for Hát/Ngâm.
  **▶ Nghe thử** reads one sample sentence in Tiếng Việt or English (your choice, editable) with the current
  settings. If a render is running, the preview waits for it to finish (one GPU).

- Pass `--script "@file.txt"` to read your own narration word for word. Without it, Claude writes the script from your brief.
- For explainers, slide markdown looks like this:
  - `# Title` starts a slide
  - `- bullet` lines are bullet points
  - `> narration` lines are what the voice says
  - with animated slides, `@` lines under a title choose how that slide looks (never read aloud):

    | Line | Effect |
    |---|---|
    | `@layout bullets` | title + bullets (also stops slide 1 from being 3D) |
    | `@layout stat 87% \| khách hàng quay lại` (or `@stat 87% \| …`) | count-up number with a ring and its label; bullets hidden |
    | `@layout 3d cube` (or `@shape cube`) | rotating 3D object: `globe`, `cube`, `torus`, `network` |
    | `@fx type` | text effect: `rise`, `blur`, `wave`, `type` (otherwise chosen from the mood) |
    | `@mood calm` | scene mood (calm, reflective, curious, tense, urgent, hopeful, triumphant, playful) |

    An unknown value is ignored (the slide keeps its default) and listed in the job's warnings.
- **Animated slides** (`--animated`, dashboard "Kiểu slide: Động", MCP `slides_engine="animated"`): titles and
  bullets move in time with the narration (letters flip up, sharpen from a blur, bounce in or are typed — chosen
  from each slide's mood), a slide about one number counts it up, and big-idea slides get a
  rotating 3D object. From a brief, Claude picks each slide's layout; with markdown slides the first slide is 3D and
  the rest are bullets unless an `@layout` line says otherwise. It adds about 1–1.5 s of rendering per second of video. Needs Playwright's Chromium
  once: `uv run playwright install chromium --only-shell` (installed Google Chrome is used if it's missing).
- Add `--music bg.mp3` for background music. It's automatically ducked under the voice.
- Or pick from the built-in library of calm, free tracks: `mediaforge music sync` downloads them once,
  `mediaforge music list` shows their ids, then pass `--music <id>` (or choose in the dashboard's
  "Nhạc nền" dropdown). Tracks licensed CC BY show the credit line you must include when publishing.

Outputs are written to `data/outputs/<kind>/<job-id>/`:

- `final.mp4`
- `voice.wav` / `voice.mp3`
- `subtitles.srt`, `.vtt` and `.ass`. Burned-in subtitles always fit inside the frame (every line is measured in
  pixels), and the word being spoken lights up in yellow and grows slightly (karaoke). Pass `"karaoke": false` for
  static subtitles.
- `plan.json`
- `report.json` (pronunciation QA for each sentence)

### Hát & Ngâm thơ

Your cloned voice chants a poem or sings lyrics, with accompaniment and an optional illustrated video.

- **🪷 Ngâm thơ**: paste your poem (one line per row) or pick a built-in Tang poem (`mediaforge poems`). Choose
  the Hán-Việt reading or a verse translation.
- **🎵 Hát**: paste lyrics and optionally set a BPM.

How it works:

- **Melody.** The melody follows Vietnamese tones (sắc rises, huyền falls, nặng drops), so the words stay clear.
  Line ends are held with ornaments and vibrato.
- **Accompaniment.** Đàn tranh and sáo trúc play in your voice's key and answer each line. Songs add mõ and
  trống on the beat.
- **Tinh thần.** Leave it on *auto* and Claude reads the poem's spirit. Or pick a mood: hùng tráng (khí khái),
  trầm mặc, u hoài or bi ai.
- **Khí khái field.** Describe the stance you want affirmed. It steers both the melody's mood and the images.
- **Dựng video hoạt cảnh.** Produces one image per line with one consistent figure, a title card, and lyrics
  that light up syllable by syllable.

Each line is listened back and re-taken if unclear. The table under the result shows what the AI heard.
Outputs go to `data/outputs/songs/<job-id>/`:

- `song.wav` / `song.mp3`
- `vocal.wav`
- `accompaniment.wav`
- `melody.json`
- `final.mp4` and `lyrics.srt` / `.ass`, with video

Design: [docs/SDD-sing-recite.md](docs/SDD-sing-recite.md).

## 3. Use from other Claude Code sessions

The MCP server is registered at user scope:

```bash
claude mcp add -s user --transport http mediaforge http://127.0.0.1:8765/mcp
```

| Tool | What it does |
|---|---|
| `speak` | Text → cloned-voice audio, with pitch, pace, warmth and clarity controls. Returns wav, mp3, srt, vtt and ass. |
| `generate_image` | Prompt → image(s) |
| `prepare_video` | Writes the script and lists terms the TTS may misread, before rendering. Returns `plan_file` + `candidates`. |
| `create_video` | `kind`: `narrated`, `short` or `explainer`. Input is a brief, your own script, slide markdown, or a `plan_file` from `prepare_video` with `pronunciations` (`{"PNJ": "Pi En Di"}`). Returns a `job_id`. |
| `list_voices` / `set_default_voice` | Voices with their `default` flag; make a voice (yours or a giọng mẫu) the default |
| `revoice_video` | Reads a finished video again in another voice (clone, blend or stock); keeps script and pictures |
| `resubtitle_video` | Rebuilds a finished video's subtitles (fit-to-frame, karaoke) without re-rendering voice or images |
| `repronounce_video` | Fixes how words are read in a finished video (`{"PNJ": "Pi En Di"}`): re-reads only those sentences and exports a new MP4, no re-render |
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

Job types: `tts`, `image`, `subtitle`, `narrated`, `short`, `explainer`, `screen`, `enrol`, `prepare`, `resubtitle`, `repronounce`.

To fix the subtitles of a video rendered before 2026-10-14 (lines could run off the screen), press
**🔤 Dựng lại phụ đề** on its card, or run `mediaforge resubtitle <job-id>`. The voice and pictures stay exactly
the same; the old file moves to `data/trash/`.

To fix a word that a finished video reads wrongly, open **🗣 Sửa phát âm** on its card. Enter the word as it
is written (`PNJ`) and how to read it (`Pi En Di`); add more rows as needed. Then press **Đọc lại & xuất video**.
Every sentence containing the word is read again and spliced into a new MP4. The pictures and subtitles are not
re-rendered. The reading is saved to the lexicon, so later videos get it right. CLI:
`mediaforge repronounce <job-id> PNJ="Pi En Di"`.

To hear a finished video in another voice, open the video's page (**Jobs → Mở**) and press **🎙 Đổi giọng**.
Pick one of your voices or a **Giọng mẫu** (▶ plays its sample at once), optionally move the pitch, and press
**Đổi sang giọng này**. The page shows an estimate first (about 1 minute per minute of video for a stock voice,
about 3 minutes for your cloned voice with `best` routing). Only the narration is read again: the script and
pictures stay. Subtitles, karaoke, title/end cards, chapter headings and music move to the new timing. AI clips
with a fixed length are cut, held on their last frame, or looped, and each case is listed as a warning. Per-sentence
re-reads of the old voice are dropped. Your pronunciations are kept. The old render goes to `data/trash/`. Someone
else's voice needs recorded consent (Giọng nói tab). MCP: `revoice_video(job_id, voice, pitch?)`.

## How pronunciation is kept correct

1. **Normalisation.** Numbers, dates, currency and units are written out as words (`vietnormalizer` for Vietnamese).
2. **Pronunciation lexicon.** Applied next. Filled before rendering by the pronunciation pre-scan (your answers are
   `manual`; terms you confirmed read fine are `verified` and are not asked again), and editable under **Phát âm**.
3. **Speech-recognition check.** Every sentence is synthesised and transcribed back with Whisper large-v3-turbo, then compared with the script:
   - Vietnamese is scored as the mean of syllable WER and CER.
   - English is scored as WER.
   - Before any synthesis, a **script check** (lint) joins sentences cut in the middle, removes stray foreign
     characters, puts short pauses between runs of acronyms ("AI SME"), reads web addresses aloud ("aisme.vn" →
     "aisme chấm vi en") and reads very long sentences phrase by phrase. Subtitles keep the written text.
   - The judge is hardened: greedy decoding, a second listen when Whisper outputs Chinese/Korean characters, and
     spelled-out acronyms are compared letter by letter ("ây ai ét em i" ≈ "AISME").
4. **Automatic repair.** If a sentence fails, MediaForge picks the next try from *how* it failed: a repeated
   word → a new seed; a cut-off ending → phrase-by-phrase; an acronym or foreign word → Claude's respelling; plus
   the other engine. It stops after two tries that do not improve.
5. **Learning, safely.** A respelling is learned only when the same try *without* it fails, it is not a correct
   Vietnamese word (those are never respelled), and it has won in two different sentences, or you approve it.
   Everything learned is listed in the **📘 Kinh nghiệm** tab (approve, reject, restore). A lesson that stops
   helping retires itself.
6. **Reporting.** Sentences that still fail are **highlighted in red** in the job's result panel, with the misheard words underlined. Each one has two buttons:
   - **🎙 Ghi âm câu này** records you reading the sentence. Your recording is **not** put into the video. The AI uses it as a guide (your pauses and phrasing) and reads the sentence again in your cloned voice, so it matches the sentences around it.
   - **🔁 Tạo lại** has the AI read the sentence again without a guide, with a new seed and phrase-by-phrase synthesis.
   - **✓ Nghe đúng rồi**: you listened and the sentence is right, so the speech recogniser misheard it. The
     sentence counts as passed, and QA stops failing those words for this voice.

   The new reading ("take") appears right under that sentence, next to the original and your recording (which is only there for comparison). The video is not re-rendered.
7. **Export.** Under the video, pick which voice each sentence uses, choose a format from the dropdown (**MP4** by default; also MOV, MKV, WebM, MP3, WAV or M4A), and click **⬇ Xuất video**.
   - The chosen takes are spliced into the voice track, and the original picture is kept as it is.
   - Exports appear as download links. Exporting the same choice again reuses the earlier file.
8. **Learning from your recordings.** Every recording is kept in `data/voices/<id>/corrections/` and added to the voice's samples.
   - The next time any job says that sentence, the AI uses your guide automatically.
   - Respellings that fixed a sentence become lessons (see step 5).

## Reviewing, rating and learning (Studio)

- **Duyệt từng bước** (on by default for Video 16:9 and Explainer, off for Short). The job stops twice:
  1. **Kịch bản.** Edit the narration, remove scenes, pick the hook (shorts), adjust the art direction (the
     recurring character, the camera, the palette), and write readings for risky words.
  2. **Storyboard.** Every picture with its prompt. **🔁 Vẽ lại** redraws one scene. Then **Dựng video**.
- Each video gets an **art direction** shared by all scenes, a **mood** per scene that drives the camera move and
  the transition, a **title card**, **chapter** labels and an **end card** (CTA and the music credit).
  `music: auto` picks a library track that fits the mood.
- **↗ Duyệt** on a job opens its review page:
  - a sentence timeline under the player (red = failed; click to jump);
  - **Cảnh & nhạc** to redraw one scene or change the music (only the picture is re-composed, in seconds);
  - **Đánh giá**: 1–5 ★ and tags. Two low ratings on the same thing teach the system to avoid it, and a note
    tagged *nội dung* becomes a rule for future scripts.
- A render interrupted by a restart **continues** from its last finished stage instead of failing.
- `mediaforge lessons list|approve|reject|report`, `mediaforge eval voice --voice <id>` (regression corpus of
  sentences that failed before).

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
