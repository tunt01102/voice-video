# SDD — Word/PDF import with images, remembered voice settings + preview, chibi visuals

Status: v1.0 (implemented) · 2026-10-15 · Owner: tu.dev@block8.ai · Extends [SDD.md](SDD.md) §5.2, §7, §9

## 1. Problem

Content for a video or a voice-over often already exists as a Word or PDF document, sometimes with figures. The
owner had to copy the text by hand into the dashboard, and the figures were lost: every scene got an AI image even
when the document had the right picture.

Voice settings (pitch, pace, warmth, clarity) were reset on every page load, so each session started by re-tuning
the four sliders. There was also no way to hear the effect of a setting without making a real job.

Finally, the visual styles (cinematic, photo, illustration, 3d, minimal, tech) had nothing light and playful. The
owner wants a **chibi** style: small, cute cartoon characters with big heads.

## 2. Requirements (from the owner)

| # | Requirement |
|---|---|
| R1 | The content can be imported from a **Word** (.docx; .doc/.rtf/.odt converted) or **PDF** file. |
| R2 | Images inside the file are kept and used: a video shows them on the scene where they appear in the text. |
| R3 | Voice settings are saved and restored from the **last setup**. |
| R4 | After setting up, **Nghe thử** plays one sample sentence in English or Vietnamese (the owner chooses). |
| R5 | Videos (and images) can use a **chibi** style: small, adorable cartoon characters. |
| R6 | A render already in progress must not be disturbed; deploy only when the GPU worker is idle. |
| R7 | Available from the dashboard, the REST API and MCP, like the other features. |

## 3. Design

### 3.1 Document import (R1, R2)

New module `text/documents.py`. **`parse(path, out_dir) -> {title, text, images, warnings, kind}`** runs on the
CPU in the request thread (no model is called, so it does not go through the GPU job queue). It is called through
`anyio.to_thread` so a large PDF does not block the event loop.

```
.docx ─► python-docx: body in order (paragraphs + tables) ──┐
                      each paragraph: a:blip r:embed → image ├─► blocks ─► text with [ảnh N] markers
.pdf  ─► pypdf: page text, then that page's images ─────────┘            + images[N-1] = PNG path
.doc/.rtf/.odt ─► macOS textutil -convert docx ─► .docx path
```

- **Markers.** The text returned carries an inline marker `[ảnh N]` on its own line, where image N appeared in
  the document. The owner sees the markers in the text box, so they can move or delete them before rendering.
- **Images.** Each image is saved as PNG under `data/uploads/docs/<id>/image_NN.png`.
  - Images smaller than 160 px on either side are dropped, because they are icons, bullets or logos.
  - Duplicates (same SHA-1) are dropped, because headers and footers repeat on every page.
  - Formats Pillow cannot open (EMF/WMF/SVG) are skipped with a warning.
  - At most 40 images are kept.
- **Shredded PDFs** (Google Docs/Skia: one word per text operator → one word per line) are fixed in
  [SDD-pdf-import-fix.md](SDD-pdf-import-fix.md).
- **Scanned PDFs.** A PDF with images but no text layer is rejected with a clear message. OCR is out of scope.
- **Size.** Text is capped at 60 000 characters, with a warning. That is about 4 hours of narration and fits the
  LLM prompt.

**Using the markers** (`documents.split_markers`, used by `pipelines.make_plan` and `run_tts`):

| Source | What happens to `[ảnh N]` |
|---|---|
| TTS | Markers are stripped and never spoken. |
| Script (read verbatim) | The script is cut at each marker. Each part is split into scenes as before, and the scene that starts right after marker N shows image N (`media[i]`). An image therefore never lands mid-scene. |
| Brief (Claude writes the script) | The brief keeps the markers. The LLM is told the document's images and returns, for each scene, an optional `"image": N` (each image used at most once, in document order). |
| Slides markdown (explainer) | A marker inside a slide makes that image the slide's picture. |

- **Figure scenes.** A document image is usually a chart, diagram or screenshot, so it must not be cropped the
  way an AI picture is. `slides.figure_frame` fits it inside the frame ("contain") over a blurred, dimmed copy of
  itself. For explainers, the slide title is drawn on a band above the figure. Motion is a slow zoom (the 8 %
  padding absorbs its ~7 % crop). An explainer figure, which has a title, stays static so the title is not cut.
- **Persistence.** The image list is stored with the plan (`plan.json` → `scene.image`). So `prepare` →
  pronunciation pre-scan → render, as well as re-render, re-subtitle and repronounce, all keep the same pictures.
  `build_visuals` gives an explicit `media[i]` priority, then `scene.image`, then an AI image.

**Licences.** `python-docx` (MIT) and `pypdf` (BSD-3). **Rejected: PyMuPDF/fitz (AGPL-3.0)**, though it is the
usual choice for PDF image extraction. `pdfplumber`/`pdfminer.six` (MIT) were not needed.

### 3.2 Remembered voice settings (R3)

- The four sliders are saved on the **voice profile** (`PATCH /api/voices/{id}` `{fx}`), debounced 600 ms after the
  last change. `speak()` already merges `profile.fx` under the request's `fx`. As a result, MCP `speak` / `create_video`
  calls without an explicit `pitch` also inherit the owner's last setup. That is "the voice", not a browser setting.
  The MCP `speak`/`create_video` defaults changed from neutral values to "unset" for this reason. Hát/Ngâm already
  take their default key from `profile.fx.pitch`, so they follow the same setup.
- When a voice is selected (or the page loads), its saved `fx` fills the sliders. "Mặc định" resolves to the
  default voice.
- Choices that are not a voice property (selected voice, engine, language, preview language and sentence) go to
  `localStorage`, wrapped in try/catch.
- A **↺ Mặc định** button resets the sliders to neutral (0 / 1.0 / 0 / 0.5) and saves that.

### 3.3 Preview — Nghe thử (R4)

- A ▶ **Nghe thử** button and a language select (🇻🇳 Tiếng Việt / 🇬🇧 English) sit inside *Chất giọng*.
- One fixed sample sentence per language, editable in a small input and remembered:
  - vi: *“Xin chào, đây là giọng đọc thử với chất giọng tôi vừa thiết lập.”*
  - en: *“Hello, this is a quick preview of the voice settings I just chose.”*
- Clicking it submits a `tts` job `{text, voice, lang, engine, fx, preview: true}` with `?wait=true` and plays the MP3.
  - `preview: true` skips Whisper word timings and subtitle files, since nothing reads them.
  - Voice FX is applied **after** the sentence cache (`speak.py`: `FX.shape` on the joined track). So after the
    first preview of a sentence, each new slider setting costs only the FX pass (≈ 1 s) and no TTS.
- `tts` has priority 1, so it jumps ahead of queued renders. It still waits for a render that is **already
  running** (one GPU worker), and the button says so ("đang chờ GPU — đang có job khác").
- Preview jobs are hidden from the Jobs list (`HIDDEN_TYPES` stays by type, so the filter uses `params.preview`).

### 3.4 Chibi style (R5)

- `image.STYLE_PRESETS["chibi"]`: *chibi anime style, cute small cartoon characters with oversized heads, tiny
  bodies and big sparkling eyes, kawaii, soft pastel colours, clean bold lineart, cel shading, playful cheerful
  mood, simple soft background*.
- A style preset alone gives chibi-*looking* landscapes. So when `style == "chibi"`, `plan()` and
  `visual_prompts_for()` tell the LLM to put one or two chibi characters **acting out** each scene's idea (a chibi
  hacker locking a padlock, a chibi student reading), always as characters and never photoreal.
- The style is available in the Video, Short, Explainer (slide backgrounds) and Image kinds, and in the MCP
  `generate_image` / `create_video` docstrings.

### 3.5 Safety of the live render (R6)

- Work happens in a separate worktree/branch. The service runs a non-editable `uv tool` copy, so source edits cannot
  affect it.
- Tests are CPU-only (parsing, markers, scene mapping, figure frame).
- Deploy (`scripts/service.sh update`) and merge happen only when `/api/health` shows `running: null` and `queue: 0`.

## 4. Interfaces (R7)

| Surface | Addition |
|---|---|
| REST | `POST /api/documents` (multipart `file`) → `{id, title, kind, text, images: [{path, url, width, height}], warnings}`. Video params: `doc_images: [path]` (image N = index N-1), text carrying `[ảnh N]`. TTS param `preview`. |
| MCP | `create_video(..., document=<path>, document_as="brief"\|"script")` and `prepare_video(...)` import the file server-side. `generate_image` / `create_video` list `chibi`; `speak` / `create_video` voice settings default to the profile. |
| Dashboard | *Nội dung* → **📄 Nhập từ Word/PDF**: fills the text box (with markers) and shows the images as thumbnails, each removable. *Chất giọng* → remembered sliders, ↺, ▶ Nghe thử + language. *Phong cách hình* → **chibi (hoạt hình dễ thương)**. |
| CLI | `mediaforge video --document file.pdf`. |

## 5. Tests

- `tests/test_documents.py` builds a .docx (python-docx) with text, a table and two inline images, plus a tiny icon
  that must be dropped. It also builds a PDF (Pillow writes an image page; pypdf merges a text page). The tests
  assert order, markers, dedupe and the scanned-PDF error.
- Marker → scene mapping for a script: the image lands on the scene after its marker, and markers are never spoken.
- The brief path's `"image": N` is validated: out-of-range and duplicate numbers are dropped.
- `figure_frame` produces a frame of the exact output size and does not crop the figure.
- `STYLE_PRESETS` has `chibi`. `plan()`'s prompt contains the chibi character instruction (LLM stubbed).
