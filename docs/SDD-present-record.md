# Thuyết trình kèm ghi hình (present & record) — design

Status: shipped 2026-10-17 (job type `present`, `src/mediaforge/present.py`).

## 1. What the owner asked for

> "Tôi sẽ gửi slide, lời thoại cùng record của tôi lúc thuyết trình, dùng nó như video gốc, chỉnh sửa lại giọng điệu,
> có thể chèn thêm ít nhạc nền — giống tính năng thuyết trình kèm ghi hình của Canva."

The inputs are the owner's slides, the script (lời thoại), and their own recording of the talk. The output is one
video, Canva-style: the slides full-frame and the owner's camera in a round bubble. It keeps the owner's **real**
voice, cleaned and re-toned, and adds subtitles and soft background music.

## 2. Decisions

| Question | Decision | Why |
|---|---|---|
| Whose voice | The recording's own voice. Denoise (noisereduce, non-stationary, 0.75) + `VoiceFX` pitch / warmth / clarity + EBU R128 −16 LUFS | "Dùng nó như video gốc". A cloned-voice re-read would break lip-sync with the camera bubble. The `explainer` and `screen` kinds already cover cloned narration |
| Pace | Always 1.0 | Any time-stretch moves the voice off the camera's lips |
| Default fx | `clarity 0.35`, everything else neutral. The voice profile's saved fx are **not** inherited | Those sliders were tuned for TTS, not for a real microphone |
| Voice length | Padded or trimmed to the recording's container duration, to the exact sample | A camera's video stream often runs past its audio (measured: 0.65 s). Without padding, the mix stopped before the picture |
| Slide change | Each slide appears `LEAD = 0.3 s` before the first word of its script part. The parts come from Whisper word timings aligned to the script (`asr.align_script`, whitespace tokens) | The owner speaks to each slide, so the script says when the slide changed. Measured on the E2E: both changes fell inside the 0.9 s pauses before the new part |
| Anchoring | A part is anchored on its first script word that Whisper heard exactly, inside a run of at least 2 matching words. If the part's opening words were skipped or changed, the slide changes at the last pause (gap ≥ 0.35 s) before the first heard word, and it is flagged `check` with a warning | `align_script` spreads unspoken script words over whatever was said instead. In the E2E with an ad-lib, that put slide 2 at the start of the ad-lib (4.42 s) and not in the pause (8.91–9.95 s). The fix lands at 9.32 s. A single shared word ("đầu" in "bắt đầu" ~ "đầu tiên") matched spuriously, hence the 2-word runs |
| A/V offset | The voice is shifted by audio `start_time` − video `start_time` | Phone and camera files often start their audio 0.05–0.2 s late, and decoding the audio alone drops that gap. Measured on a fixture with the audio 150 ms late: the voice was 129 ms early before the fix and exact after it. Click-train cross-correlation: the processing (denoise, pitch ±3) shifts the voice ≤ 2 ms |
| Browser webm | A recording with no duration anywhere (MediaRecorder) is remuxed once to MKV (`-c copy`) | ffprobe returns no duration at all for these files |
| Script → parts | Lines `---`, `[slide N]`, `[N]`, `Slide N:` or `Trang N` separate the parts. With no separator line, blank-line paragraphs separate them | Plain to type in the dashboard textarea |
| No usable script | `slide_times` (manual) > script with exactly one part per slide > even split + a warning | The owner never gets a silent wrong timing. The job page shows every slide's start and first words, can be edited, and re-renders |
| Slides | PDF via **pypdfium2** (BSD-3 / Apache-2.0); PNG/JPG/WebP; a folder (natural sort); slide markdown (Pillow slides, `> narration` = script) | PyMuPDF is AGPL (rejected), and no poppler or LibreOffice is installed. PPTX/Keynote: the error says to export to PDF |
| 4:3 decks | Letterboxed onto 16:9, never cropped | Slides carry text to the edges |
| Layouts | `bubble` (default), `slides` (camera hidden), `recording` (the recording full-frame, e.g. a screen recording of the deck) | An audio-only recording falls back to `slides`, with a warning |
| Bubble | Round, anti-aliased (mask drawn at 4×), white ring, 30 % of the frame height. A bottom corner sits above the two-line subtitle band | Canva look. The bubble must not cover the subtitles |
| Music | Library id, `auto` (mood-free pick, the owner's music likes/dislikes respected) or an uploaded file. Default volume 0.12, ducked under the voice by `compose.audio_graph` | A talk needs quieter music than a narrated video (0.18) |
| Result shape | `video`, `voice`, `subtitles`, `words`, `slides[]`, `report`. **No `plan`** | restage, revoice and resubtitle need a plan with scene wavs, so the dashboard does not offer them for a presentation. Export (MP4/MOV/MKV/WebM/MP3…) works |

## 3. Pipeline (`present.run`)

1. Validate the input: recording, `layout`, `camera_pos`, `aspect`. A missing library track fails fast.
2. `load_slides` → frame-sized PNGs in `outputs/presentations/<job>/slides/`.
3. `process_voice` → `voice.wav` (48 kHz mono, exactly the recording's length).
4. Whisper on the processed voice, then align it to the script → `words` → subtitles (SRT/VTT/karaoke ASS).
5. Slide start times (§2).
6. `render`, one FFmpeg graph:
   - the slides as looped stills, each lasting its slot plus a 0.3 s cross-fade;
   - an `xfade` chain at the start times (compose.py's timeline contract);
   - for `bubble`: the camera cropped square, scaled and `alphamerge`d with the round mask, then overlaid after the ring;
   - the burned `ass` subtitles;
   - the voice and music through `compose.audio_graph`.
   
   Encoded with `-t` = the recording's length on h264_videotoolbox.

## 4. Interfaces

- Dashboard: Tạo mới → **🎥 Thuyết trình + ghi hình**. The form takes the recording, the slides, the script, the layout, the camera corner and size, a denoise switch, pitch / warmth / clarity, subtitles and music. The job page has a **Mốc chuyển slide** table (thumbnail, start time, first words) and a **↻ Dựng lại với các mốc này** button. That button submits a new job with `slide_times` and keeps an `auto` music pick.
- MCP: `present_recording(recording, slides | slides_markdown, script, layout, camera_pos, camera_size, pitch, warmth, clarity, denoise, music, music_volume, slide_times, burn_subtitles, lang, aspect, title)`.
- CLI: `mediaforge present talk.mp4 --slides deck.pdf --script @loi-thoai.txt --music auto --pitch -1`.
- REST: `{"type": "present", "params": {…same names…, "fx": {"pitch": -1}}}`.

## 5. Tests

`tests/test_present.py` uses synthetic media only (an ffmpeg test pattern, sine tones and Pillow slides; recordings are biometric data). It covers:

- script splitting;
- slide starts, both directly and through `align_script`;
- start clean-up;
- PDF, picture, folder and markdown loading, and the PPTX error;
- pace locking;
- the voice length;
- bubble placement;
- every layout end-to-end with music (audio length = video length);
- the audio-only fallback;
- manual times and the mismatch warning;
- bad input.

E2E (2026-10-17, data-store copy on :8766, real Whisper): a Vietnamese test recording (macOS `say`, 12.9 s) was rendered with a 3-page PDF in about 6 s. The slide changes landed at 4.88 s and 9.48 s, with the speech resuming at 5.48 s and 10.11 s. The mix measured −15.9 LUFS. MOV and MP3 export worked, and so did re-rendering with an edited time.

## 6. Not done (possible follow-ups)

- Subtitles show the script's words. An ad-libbed passage is captioned with the script text that `align_script` spread over it. A `subtitle_text="heard"` option, or captions mixing script and heard words, would fix that.

- A cloned-voice mode (re-read the script in the owner's clone), shown without the camera.
- Cutting long pauses and filler words ("ờ", "à"). This needs the same cuts on the camera track.
- Slide-change detection from a screen recording (scene-cut detection), for `layout=recording` with separate slides.
- Animated (HTML) slides for a markdown deck. Static Pillow slides are used today.
