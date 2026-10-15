---
name: mediaforge-animated-presentation
description: Make a presentation / slide video (thuyết trình) with MediaForge — animated titles and bullets, count-up stat slides, rotating three.js 3D slides, the owner's cloned voice, karaoke subtitles, music, rendered to MP4. Use when the user asks for a presentation video, an explainer, slides with narration, a pitch or training video, or to change how MediaForge animated slides look.
---

# Animated presentation videos (MediaForge)

MediaForge already does everything from script to MP4; this skill is the map.

## Make one (any Claude session)

1. `list_jobs` — never start a GPU render beside a running one.
2. `list_voices` — use the owner's voice id, not `demo`.
3. Optional but recommended: `prepare_video(kind="explainer", …)` → check the script and risky words.
4. `create_video(kind="explainer", slides_engine="animated", theme="midnight"|"ocean"|"paper"|"block8",
   brief=… | script=… | slides_markdown=… | plan_file=…, lang="vi"|"en", voice=<id>, music="auto")`.
5. Poll `job_status`; the result has `video`, `thumbnail`, `subtitles`, `report.warnings`.

CLI: `mediaforge explainer "brief" --animated --theme block8 --lang vi`.

## Slide layouts (per scene in plan.json)

| layout | needs | shows |
|---|---|---|
| `bullets` (default) | `slide_title`, `bullets` (≤ 5) | word-by-word title, bullets arriving across the narration |
| `stat` | `stat: {"value": "87%", "label": "…"}` | count-up number + ring; bullets hidden (say them in narration) |
| `3d` | optional `shape`: globe / cube / torus / network | title + bullets left, rotating three.js object right |

Text effects (GSAP SplitText) on the title and bullets, from the scene's `mood` unless `text_fx` is set:

| text_fx | mood | look |
|---|---|---|
| `rise` (default) | curious, hopeful, triumphant, none | letters flip up into place |
| `blur` | calm, reflective | words sharpen out of a blur |
| `wave` | playful | letters pop in with a bounce |
| `type` | tense, urgent | typed letter by letter behind a blinking caret |

With no layout the first slide is `3d` and the rest are `bullets`. The LLM planner fills `layout`/`stat`/`shape`
for briefs. In `slides_markdown`, put directive lines under a slide's `# Title` (parsed by
`slides.parse_markdown_slides`, never read aloud):

```markdown
# Khách hàng quay lại
@layout stat 87% | khách hàng quay lại     ← or: @stat 87% | …
@fx type                                    ← rise | blur | wave | type
@mood tense                                 ← also drives music and the default fx
> Gần chín phần mười khách hàng quay lại.

# Hệ sinh thái
@layout 3d network                          ← or: @shape network (globe | cube | torus | network)
- …
```

`@layout bullets` on slide 1 stops the default 3D opener. Bad values are ignored and reported in
`report.warnings` ("slide N: …").

## How it renders (for code changes)

- `src/mediaforge/video/scene_web/scene.html` — one slide. anime.js timeline (`autoplay:false`) for bar, dots,
  stat; a paused GSAP timeline for SplitText text effects (`TEXT_FX`); three.js for 3D. Both timelines are
  seeked in `__seek`. Text is NFC-normalised in animate.py so a Vietnamese letter is never split from its marks;
  split only after `document.fonts.ready`; no line masks (they clip stacked diacritics like ể).
  Contract: `__setup(data)` once, `__seek(t)` per frame. **Never read the clock** (no `Date.now`,
  `requestAnimationFrame`, CSS transitions/animations): frames must be a pure function of `t`.
- `src/mediaforge/video/animate.py` — Playwright headless Chromium (bundled shell, falls back to installed
  Chrome), page served from `scene_web/` by request routing (no network), JPEG frames piped into
  `settings.ffmpeg` → `slides/slide_NN.mp4`, length = narration slot + 0.5 s cross-fade.
- `pipelines.build_visuals` uses it when `kind == "explainer"` and `slides_engine == "animated"`;
  compose, subtitles, music, cards are unchanged. Clips are re-rendered only when the slot length changed.
- Burned-in subtitles own the bottom band: keep slide content above ~86 % height (above 60 % on 9:16, where the
  subtitles sit at MarginV 520/1920). On 9:16 the `3d` canvas is a band at 7–28 % with the text below it.
  `#content` shrinks its type (down to 0.55×) until the text fits its box — never let text overflow.
- Vendored libs: `scene_web/vendor/` (anime.js 4.5.0, three.js r186 bundled with esbuild — MIT; GSAP 3.15.0 +
  SplitText — GSAP no-charge licence, allowed 2026-10-16), licence notes beside them.

Check a change: render one slide with `animate.renderer(1920, 1080)` → `scene(...)`, grab a frame with
`settings.ffmpeg -ss 3 -i slide.mp4 -frames:v 1 x.png` and look at it, then `uv run pytest`.

Related skills: `animejs`, `threejs`, `mediaforge-ffmpeg`, `gsap-*` (GSAP allowed for new effects), `remotion-*`
(for hand-built Remotion projects outside the MediaForge pipeline).
