# SDD — Motion videos, style first (tab ✨ Motion)

Status: built 2026-10-30 on `feature/motion-studio`; merged to main and deployed 2026-10-31.

## 1. What was reviewed: `scripts/motion-studio` (Motion Studio 0.4.1)

The reviewed copy (`scripts/motion-studio/` + `motion-studio-0.4.1.zip`, no licence file) was never committed and was
deleted on 2026-10-31 at the owner's request; nothing from it lives in this repo.

Motion Studio is a Claude Code **skill**, not a renderer. It sits in front of **HyperFrames** (HeyGen's open-source
"video = HTML page" framework, pinned to 0.8.181) and makes every job follow the same process:

| Step | Motion Studio | Who does the work |
|---|---|---|
| 1. Style | `styles.mjs gallery`: an HTML page of HyperFrames' `FRAME.md` presets + the user's saved looks; or a look made from 3–5 screenshots / a website (`hyperframes capture`) / a video (`styles.mjs frames` → contact sheet) | skill + Claude |
| 2. Describe | one message asking for everything missing (message, offer, audience → aspect, length, material, on-screen language, storyboard?) | Claude |
| 3. Brief | a hand-off request (`references/handoff.md`) to HyperFrames' interview; the user confirms the brief before anything is built; every editable word/number/colour/picture must be a **composition variable** | HyperFrames skills |
| 4. Build | HyperFrames writes the HTML composition and opens its Studio | HyperFrames + Claude |
| 5. Review / export | Studio (Variables, Design, Timeline, Export); `sync-text.mjs` reconciles canvas edits with variables before every render | Studio + skill |
| extras | `music.mjs` + `sound.js` (synthesised music bed, no account), `batch.mjs` (one MP4 per CSV row via `hyperframes render --batch`), 16:9 / 9:16 / 1:1 / 4:5 | skill |

Findings that shape the port:

- The build is done by **Claude following HyperFrames' skills interactively** (it writes the composition). There is
  no "brief in → MP4 out" CLI a dashboard job could call. Wrapping it would mean an agentic `claude -p` run with file
  tools inside the GPU worker, plus a Node 22 toolchain, a separate Chrome download (~700 MB), and HeyGen telemetry
  (on by default).
- HyperFrames is not installed on this Mac, and its presets (`FRAME.md`) and the Vietnamese preset notes
  (`presets-vi.mjs`) are someone else's design work; the skill folder has no licence file. Nothing from it is copied.
- MediaForge already has every runtime piece: the frame-exact HTML renderer (`video/animate.py`, anime.js + GSAP
  SplitText, licences in `scene_web/vendor`), the composer (xfade, music, loudnorm), the job queue with an approval
  gate (`AwaitApproval`), `llm.py` (Claude CLI) and the music library.

**Decision:** reproduce the *process and the kind of output* natively — style → describe → confirm brief → build →
retype variables → export, plus batch — on MediaForge's own renderer. No new dependency, no new licence.

## 2. What the tab does

1. **Chọn phong cách** — 8 looks of our own (`motion.STYLES`) shown as live CSS cards (colours, fonts, button),
   or **Tự chọn giúp mình**: the brief LLM picks one (`style: auto`).
2. **Mô tả video** — what it is, the one message, offer/numbers (used verbatim, none invented), audience/where,
   on-screen language (vi/en), aspect (16:9 / 9:16 / 1:1 / 4:5), length 8–60 s, logo, up to 8 pictures, music
   (auto by mood / none / a library track).
3. **Duyệt brief** (default on) — job `motion` stops in status `waiting` at stage `brief`: summary, title, style,
   every variable as a field (colour picker for colours), every scene (kind, what the viewer sees, its texts,
   seconds, ✕ to drop). "Duyệt brief → dựng video" posts the edited brief to `/api/jobs/{id}/approve`.
4. **Dựng** — each scene is one `scene_web/motion.html` page stepped at 30 fps in Chromium → MP4 clip; clips are
   cross-faded by `compose.compose` over a silent track, with the music bed faded and loudness-normalised (−16 LUFS).
5. **Sửa chữ & dựng lại** — on a finished video: retype variables, change style, aspect or music → a new `motion`
   job with the same brief (`review: []`), **no LLM call**.
6. **Nhiều phiên bản từ CSV** — "⬇ CSV mẫu" (header = `name` + variable labels, one row of current values); each
   row → `versions/NN-<name>.mp4`. Columns match a variable's id or label; empty cells keep the default; ≤ 50 rows.

Phase 2 (2026-10-31, §7): own looks, a look drafted from a reference picture, GIF export, transparent MOV.
Still not ported: a look from a website or a video, a synthesised music bed (we use the CC library), direct canvas
editing (Studio's Design panel).

## 3. Data model

```jsonc
// brief.json in data/outputs/motion/<job>/ (motion.normalise clamps LLM or owner input to this)
{ "title": "Cold brew Mộc – ủ 18 tiếng", "summary": "…", "style": "khoi-mau", "music_mood": "lofi",
  "variables": [{"id": "price", "label": "Giá", "type": "text|number|color", "value": "45.000đ"}],
  "scenes": [{"kind": "hook|title|stat|list|chart|quote|image|cta", "seconds": 3.0, "note": "what the viewer sees",
              "headline": "{{headline}}", "sub": "…", "value": "{{guests}}", "label": "…", "items": ["…"],
              "bars": [{"label": "Q1", "value": "120 triệu"}], "quote": "…", "author": "…", "image": 1, "button": "…"}],
  "warnings": [], "images": 0 }
```

- Scene text refers to variables as `{{id}}`; a variable's value may name another one (`Ghé quán {{brand}}`, one
  level). Undeclared references are dropped with a warning. A `color` variable whose id is a style role
  (`accent`, `bg`, `ink`…) replaces that colour.
- Clamps: ≤ 12 scenes, each 1.6–8 s, total = requested length; chart needs ≥ 2 numeric bars, stat a value, image a
  valid picture number — otherwise the scene becomes a `title`.
- Clips are cached by a hash of their render data (`scenes/<sha>.mp4`), so a batch row only draws the scenes its
  values change (E2E: 3 videos × 6 scenes = 10 clips drawn, not 18).

## 4. Rendering notes

- `animate.Renderer.render(page, data, out, seconds)` is the shared encode loop (`scene()` uses it too); pages can
  load local files as `/file/<key>` (`Renderer.files`: `logo`, `img1…`).
- Chromium renders offline (every non-local request is aborted), so web fonts cannot load. Each style uses installed
  macOS faces checked (fontTools cmap) to cover every Vietnamese letter: Avenir Next, Helvetica Neue, Baskerville,
  Big Caslon, DIN Condensed, Georgia, Chalkboard SE, Courier New, Monaco. Menlo, Futura, Didot, Bodoni, Impact and
  Arial Black fail the check. Headline numerals are forced to lining figures (Big Caslon's old-style "1" read as "I").
- Unit = min(W, H)/1080 (×1.18 in portrait); a fit loop shrinks the content until it fits. Portrait charts are
  horizontal bars. Text is NFC so SplitText keeps diacritics whole.
- Number count-ups parse `1.200+`, `45.000đ`, `3,5 triệu`, `87%` (thousand separators vs decimals) and keep the
  original format.

## 5. Interfaces

- Job `motion` (`jobs.JOB_TYPES`, resumable): params `style, prompt, message, offer, audience, aspect, seconds,
  lang, logo, images, music, review, brief, variables, rows`. Result: `video, thumbnail, title, brief, brief_data,
  values, style, aspect, versions[{name, video, values}], duration, report{music_id, music_credit, warnings…}`.
- `GET /api/motion/styles`; `POST /api/jobs/{id}/approve` `{stage: "brief", brief}`.
- MCP `motion_video` (same params; `brief` + `variables` re-render without the LLM).
- Dashboard: tab ✨ Motion (`web/js/motion.js`), gate via `window.renderGate` → `motionGate`.
- Phase 2: `GET /api/motion/styles` also returns `options` (allowed fonts/texture/deco/shadow/motion/case/transition/
  head_weight) and `roles`; each style has `own`. `POST /api/motion/styles` (save; an own `id` updates),
  `DELETE /api/motion/styles/{id}` (own only), `POST /api/motion/styles/from-image {image, name?}` → draft + `palette`.
  Job param `alpha` → result `alpha` (MOV path). Export format `gif` (`POST /api/jobs/{id}/export`). MCP
  `motion_video(alpha=…)`.

## 6. Verification (2026-10-30)

- `tests/test_motion.py` (11 tests: clamping, variables by id/label, nested values, colour roles, auto style via a
  fake LLM, the brief gate + owner edits, a real 4:5 render with one CSV row and clip reuse, picture/logo scenes keep
  their type size in 9:16). Full suite: 229 passed.
- Picture and logo scenes checked by eye in 16:9 and 9:16 (found and fixed: a `flex: 1` picture frame made the fit
  loop shrink the caption to half size in portrait).
- E2E on a data-store copy (port 8766, live server idle): real Claude brief for a 20 s 9:16 cold-brew ad (6 scenes,
  13 variables, only the given numbers), edited at the gate (style → khoi-mau, discount changed) → 19.5 s MP4,
  −16.0 LUFS, CC-BY credit shown; re-render 1:1 in `den-vang` with 2 CSV rows → 3 MP4s in 41 s. Frames checked by
  eye for all 8 styles × 4 aspects; dashboard checked by headless-Chrome screenshot.

## 7. Phase 2 (2026-10-31)

Order: own looks → look from a picture → GIF → transparent MOV (the only part touching the encoder and compose, so last).

**Own looks.** Saved in `data/motion/styles.json` (id `rieng-<slug>`, never a built-in id). `motion.all_styles()` merges
them with `STYLES`, so the dashboard cards, the LLM's auto-pick list, `normalise` and `run` all see them.
`clean_style` clamps every field to what `motion.html` draws (`OPTIONS`); fonts only from `FONTS` (the faces checked
for Vietnamese in §4); colours `#rrggbb`, else the base look's. Readability is enforced on save: ink ≥ 4.5:1 on bg,
muted ≥ 3:1, and when ink is unreadable on the surface colour a `surface_ink` is added (list rows). Unknown fields fall
back to the base look. Editing a look changes later renders only (clips are cached by their render data, which
includes the colours and fonts). Deleting a look an old brief names falls back to the default look.

**Look from a picture.** `palette_from_image`: Pillow median-cut to 8 colours on a ≤ 200 px copy (deterministic, no
model, cheap enough for a request handler). bg = commonest colour; ink = the swatch (or near-black/white) with the best
contrast on bg; accents = the most saturated swatches at least 60 RGB units from bg and ink (weighted by share);
the accent is pushed until bg-coloured button text reads at 3:1. Fonts and the rest come from Đêm neon (dark bg) or
Doanh nghiệp (light bg) in the API draft; the dashboard editor takes only its colours (fonts, shapes and motion
the owner already set are kept). Nothing is saved until the owner presses save. Not done: a look from a website or video, an
LLM naming the mood.

**GIF.** An `export.FORMATS` entry (all video jobs): fps 15, long side ≤ 720 px, `palettegen stats_mode=diff` +
`paletteuse` bayer, `-loop 0`, no sound. Export labels drop "giọng gốc" for jobs without narration.

**Transparent MOV.** A second render, not an export (`final.mp4` has no alpha). `motion.html` gets `alpha: true` →
no page colour and no texture (an ink-coloured texture over someone else's footage looks like dirt); decorations,
cards and text stay. `Renderer.render(alpha=True)` takes PNG shots with `omit_background` → ProRes 4444
`yuva444p10le` (`animate.PRORES_ALPHA`, qscale 9: ~36 MB/s at 1080×1920, about half the encoder default; HEVC with
alpha was tried and rejected — ffprobe reports yuv420p, so the alpha cannot be checked). `alpha_concat` cross-fades the
clips on `yuva444p10le` with the look's transition (`fadeblack` → `fade`), no sound; the per-scene ProRes clips are
deleted once joined. Only the main video gets one, not CSV rows.

**Verification.** `tests/test_motion.py` +5 (own look clamp/save/update/delete/LLM list; palette roles and contrast
on a drawn picture, light and dark; GIF size and streams; alpha MOV: ProRes, yuva, transparent corner, opaque text,
no intermediates, size cap). Full suite 234 passed. E2E on a throw-away data dir (port 8766, music symlinked, live
server idle) driven by Playwright through the dashboard: look from a picture → texture/font changed → saved → selected
→ renamed → 9:16 render with alpha (5.5 s video in 24 s) → GIF exported from the export bar → look deleted; no JS
errors, server log clean. Frames checked by eye (alpha composited over a checkerboard; count-up ends on the given
value).
