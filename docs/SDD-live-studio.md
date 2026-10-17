# Phòng quay thuyết trình (live studio): camera + mic + slides → the master file — design

Status: shipped 2026-10-18 (dashboard **🎥 Thuyết trình + ghi hình → 🔴 Quay trực tiếp**, job type `present` with
`slide_track`, `src/mediaforge/present.py`, `web/studio.js`).

## 1. What the owner asked for

> "Làm chức năng bật camera lên để quay trực tiếp video, thu tiếng kèm slide để tạo file gốc cho thuyết trình, quá
> trình chỉnh sửa chỉ là làm đẹp lại, cho phép xuất video chất lượng Full HD."

[SDD-present-record](SDD-present-record.md) needs a recording made somewhere else, and it guesses the slide changes
from the speech. The studio records the talk **in the dashboard**: the camera and the mic run while the owner clicks
through their own slides. Every click is stamped on the recording's clock. The take plus that timeline is the
**master (file gốc)**. Editing afterwards only polishes the look and the sound, and never needs a new take. The
output is a 1920×1080 video.

## 2. Decisions

| Question | Decision | Why |
|---|---|---|
| What is recorded | The camera + mic only (MediaRecorder). The slides are **not** screen-captured. The server composes the deck's own PNGs at 1920×1080 | A screen capture of a slide is a lossy copy at the browser window's size. The deck's PNGs are sharp at Full HD, and the camera keeps its own resolution in the bubble |
| The master (file gốc) | `{recording, slides (the PNGs shown in the studio), slide_track, script?}`. These are the `present` job's params. Every re-edit is a new `present` job with the same master and different look params | One immutable source. "Làm đẹp lại" can't drift from what was recorded |
| Same pictures in studio and video | `POST /api/present/slides` renders the uploaded deck with `present.load_slides` at the frame size. The studio shows those PNGs and the job receives their paths as `slides` | A slide index means the same picture in both places (a PDF page count, sort order and letterboxing are decided once) |
| Slide timeline | `slide_track = [{slide, t}, …]`: the slide shown (0-based) from second `t` of the recording. Going back a slide is a normal event, and a slide may appear several times | Presenters go back. A per-slide start list (`slide_times`) can't express "2 → 1 → 2" |
| Timeline clean-up | Sorted by time. The first event is moved to 0. An event that shows the slide already on screen is dropped. A segment shorter than `MIN_LIVE = 0.4 s` (flicking through) is merged into the next one. **No** `MIN_SLIDE` push | The times are the owner's own clicks: moving them would put a slide off the moment it was clicked. Flicking past slides would otherwise leave sub-fade blips |
| Clock | The recorder's `onstart` is t = 0. A click is stamped `performance.now() − start − paused`. Clicks during a pause are applied at the paused instant | MediaRecorder's file excludes paused time, so the timeline must exclude it too. This is checked in E2E (§5) |
| Slide timing vs. script | With a `slide_track`, Whisper still runs (subtitles) but the script never moves a slide (`slide_timing: "live"`) | The click is the truth |
| Capture quality | `video: {width: {ideal: 1920}, height: {ideal: 1080}, frameRate: {ideal: 30}}`. The real size (`track.getSettings()`) is shown, with a warning under 1080p. `videoBitsPerSecond 8 Mbps`, `audioBitsPerSecond 192 kbps`. mimeType cascade: mp4/avc1 → webm vp9/opus → webm vp8/opus → default | Browsers default to 640×480 at ~2.5 Mbps, which looks blocky at Full HD |
| Mic | `echoCancellation`, `noiseSuppression` and `autoGainControl` off, like the voice-sample recorders. A live level meter. Recording is blocked without an audio track | The server denoises and normalises (`process_voice`). `present.run` fails on a silent file |
| Preview | Mirrored with CSS only. The recorded stream is not mirrored | Natural for the presenter; text on a whiteboard stays readable in the video |
| Don't lose the take | 1 s timeslices. **⬇ Lưu file gốc** downloads the take, the slide timeline (JSON) is kept beside it, and the upload uses XHR with a progress bar. A take is kept in the page until it is used or discarded | A 10-minute 1080p take is ~600 MB. A failed upload must not cost a re-take |
| Teleprompter | The script's part for the current slide (same `---` rules as `split_script`) shows under the slide, for the presenter only | Optional. It also feeds the subtitles' wording |
| Keys | → / PageDown / Space / Enter = next. ← / PageUp / Backspace = previous. Home = first. `R` = record/stop. `P` = pause | Presentation clickers send PageUp/PageDown |
| Polishing (làm đẹp lại) | The job page of a live job has a **✨ Làm đẹp lại** panel: layout, camera corner/size, denoise, pitch/warmth/clarity, subtitles, music + volume. It submits a new job with the same master. An `auto` music pick is kept | "Chỉnh sửa chỉ là làm đẹp lại": no re-recording, no re-timing unless the owner asks |
| Timeline edits | The **Mốc chuyển slide** table lists segments (a slide may repeat) and resubmits `slide_track` | The old one-row-per-slide table would truncate a timeline with repeats |
| Full HD | The render is 1920×1080 (16:9), H.264 at 14 Mbps target. Export adds **MP4 Full HD 1080p**: a stream copy when the video is already 1920×1080 (or 1080×1920), else a scale + pad re-encode to it | A re-encode of a 1080p file only loses quality. The format still guarantees Full HD for any job |

## 3. Flow

1. Dashboard → Tạo mới → 🎥 Thuyết trình + ghi hình → **🔴 Quay trực tiếp**.
2. Pick the slides (PDF or pictures, optional). They upload, the server renders them, and **Mở phòng quay** opens.
3. The studio shows the camera and mic pickers, the real camera resolution, the level meter, the current slide
   (large), the next slide, the camera bubble preview and the teleprompter.
4. **⏺ Quay** (3-2-1 countdown) → click through the slides → **⏸** / **⏹**.
5. Review: the take plays back with its timeline. **Quay lại** discards it. **⬇ Lưu file gốc** downloads it.
   **✓ Dùng bản này** uploads the take and submits a `present` job with `slide_track` and the form's look settings.
6. The job page shows the video, the timeline, **✨ Làm đẹp lại**, the master's link (**⬇ File gốc**) and Export
   (MP4 Full HD 1080p …).

No slides: the studio records the camera alone, and the job uses `layout=recording` (the camera full-frame at 1080p).

## 4. Interfaces

- REST: `POST /api/present/slides {slides: [paths], aspect}` → `{slides: [{path, url}]}`.
  `{"type": "present", "params": {recording, slides, slide_track: [{slide, t}], …}}`.
- MCP: `present_recording(…, slide_track=[{"slide": 0, "t": 0}, …])`.
- CLI: `mediaforge present take.webm --slides s1.png --slides s2.png --track @timeline.json`.
- Result: `slides[]` holds one row per segment `{slide, image, start, end, first_words}`; `master`
  `{recording, slide_track}`; `report.slide_timing = "live"`.

## 5. Tests

- Unit (`tests/test_live_studio.py`, synthetic media only):
  - timeline → segments: going back, repeats, flick merging, the first event at 0, past-the-end events;
  - render with a repeated slide (frames before and after each change), with the audio length equal to the video
    length and the output 1920×1080;
  - the slides endpoint;
  - the Full HD export, both copy and scale.
- E2E (Playwright + Chrome fake camera/mic, data-store copy on :8766): open the studio, record, flip slides at known
  times (with a pause), use the take, wait for the job. Then check:
  - the frame just before and after each flip shows the right slide;
  - the duration matches the take;
  - the result is 1920×1080;
  - Làm đẹp lại re-renders with the same timeline;
  - the Full HD export works.

## 6. Not done

- Recording the screen as well (a live demo inside a slide). `layout=recording` covers a separate screen capture.
- Slide animations or builds (a PDF page is one picture).
- Cutting mistakes out of the take. Trimming the master's start and end would be the first step.
