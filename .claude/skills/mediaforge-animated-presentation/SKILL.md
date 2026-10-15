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

With no layout the first slide is `3d` and the rest are `bullets`. The LLM planner fills `layout`/`stat`/`shape`
for briefs; for `slides_markdown` edit the plan (`review=["plan"]` or a `plan_file`).

## How it renders (for code changes)

- `src/mediaforge/video/scene_web/scene.html` — one slide. anime.js timeline (`autoplay:false`) + three.js.
  Contract: `__setup(data)` once, `__seek(t)` per frame. **Never read the clock** (no `Date.now`,
  `requestAnimationFrame`, CSS transitions/animations): frames must be a pure function of `t`.
- `src/mediaforge/video/animate.py` — Playwright headless Chromium (bundled shell, falls back to installed
  Chrome), page served from `scene_web/` by request routing (no network), JPEG frames piped into
  `settings.ffmpeg` → `slides/slide_NN.mp4`, length = narration slot + 0.5 s cross-fade.
- `pipelines.build_visuals` uses it when `kind == "explainer"` and `slides_engine == "animated"`;
  compose, subtitles, music, cards are unchanged. Clips are re-rendered only when the slot length changed.
- Burned-in subtitles own the bottom band: keep slide content above ~86 % height (40 % on 9:16).
- Vendored libs: `scene_web/vendor/` (anime.js 4.5.0, three.js r186 bundled with esbuild) — MIT, licences beside them.

Check a change: render one slide with `animate.renderer(1920, 1080)` → `scene(...)`, grab a frame with
`settings.ffmpeg -ss 3 -i slide.mp4 -frames:v 1 x.png` and look at it, then `uv run pytest`.

Related skills: `animejs`, `threejs`, `mediaforge-ffmpeg`, `gsap-timeline` (timing concepts only).
