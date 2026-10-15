# Skills & libraries review — 20 repos (2026-10-15)

Goal: pick the agent skills and libraries that give MediaForge full coverage of **video, presentations,
3D, audio and render**, under the project rule *Apache/MIT/BSD only* (CLAUDE.md, SDD §3).
Licences were read from the GitHub API / LICENSE / package metadata on 2026-10-15.

## Verdicts

| # | Repo | Licence | What it is | Verdict | Why |
|---|---|---|---|---|---|
| 1 | remotion-dev/skills | none in repo (Remotion's) | 12 agent skills for Remotion | **Reject** | Remotion is on SDD §3 Rejected (company licence); skills only make sense with Remotion. Owner decision to revisit. |
| 2 | motiondivision/ai-kit | **no licence file** | `motion` skill (Motion + CSS animation, audits are Motion+ paid) | **Link only** | No licence ⇒ all rights reserved: not vendored. Install per-user via its own plugin if wanted. |
| 3 | greensock/gsap-skills | MIT (skills) | 8 GSAP skills | **Installed 4** (core, timeline, utils, performance) | Good animation-timing knowledge. Scroll/React/framework skills irrelevant to a render pipeline. A project note overrides their "recommend GSAP" line — the GSAP library itself is not licence-clean (row 6). |
| 4 | svg/svgo | MIT | SVG optimiser | **Defer** | Useful only once we author SVG assets; nothing in the pipeline produces SVG today. |
| 5 | vercel/satori | MPL-2.0 | HTML/CSS → SVG | **Defer** | Outside the licence rule; Pillow (stills) + Chromium (animated) already cover HTML→image. |
| 6 | greensock/GSAP | GSAP Standard "no-charge" licence (not OSI) | animation engine | **Reject for the render path** | Not Apache/MIT/BSD. anime.js does the same job here. Owner + compliance may approve later. |
| 7 | juliangarnier/anime | MIT | animation engine (timelines, stagger, SVG) | **Adopted** — vendored 4.5.0 | Drives the animated slides; deterministic `timeline.seek()` = frame-exact render. |
| 8 | motiondivision/motion | MIT | React/JS animation | **Defer** | anime.js covers vanilla timelines; Motion shines in React UIs (dashboard is build-free). |
| 9 | rive-app/rive-wasm | MIT | Rive runtime | **Defer** | Needs `.riv` assets authored in the Rive editor; no asset pipeline yet. |
| 10 | LottieFiles/dotlottie-web | MIT | Lottie/dotLottie player (+ upstream `SKILL.md`) | **Skill installed**; lib deferred | Skill is MIT and self-contained; runtime only needed once we ship Lottie assets. |
| 11 | remotion-dev/remotion | Remotion licence (company licence > 3 staff) | React video framework | **Reject** (SDD §3) | Licence. Our substitute: HTML scenes + anime.js/three.js + headless Chromium → FFmpeg. |
| 12 | motion-canvas/motion-canvas | MIT | code-driven animation + editor | **Defer** | Export runs from its Vite editor UI; no supported headless render, so it cannot be a job step. Good for hand-authored decks. |
| 13 | mrdoob/three.js | MIT | 3D engine (+ upstream `llms.txt`) | **Adopted** — vendored r186 | `3d` layout of animated slides. Project skill points at upstream `llms.txt`. |
| 14 | pmndrs/react-three-fiber | MIT | React renderer for three | **Defer** | Needs a React toolchain; plain three.js is enough inside a render template. |
| 15 | pmndrs/drei | MIT | R3F helpers | **Defer** | Depends on R3F (row 14). |
| 16 | pmndrs/react-three-rapier | MIT | physics for R3F | **Defer** | Depends on R3F; no physics need yet. |
| 17 | pmndrs/react-postprocessing | MIT | post FX for R3F | **Defer** | Depends on R3F. |
| 18 | goldfire/howler.js | MIT | browser audio playback | **Defer** | Audio is produced server-side (FFmpeg/pedalboard); Howler is only for in-browser playback. |
| 19 | Vanilagy/mediabunny | MPL-2.0 | in-browser mux/encode (WebCodecs) | **Defer** | Outside the licence rule; FFmpeg already encodes server-side. |
| 20 | FFmpeg/FFmpeg | LGPL/GPL | encode/compose | **Already adopted** | Composition, loudness, subtitles (`settings.ffmpeg`). Project skill documents our usage. |

Maintainer-only dev skills found inside `motiondivision/motion` and `remotion-dev/remotion`
(`.agents/skills/fix`, `add-bug`, …) are for contributing to those repos and were not taken.

## What was added to the project

`.claude/skills/` (project skills, loaded by every Claude Code session in this repo):

| Skill | Source | Purpose |
|---|---|---|
| `mediaforge-animated-presentation` | written here | How to make an animated presentation video with MediaForge end-to-end (plan → voice → animated slides → compose → render) |
| `mediaforge-ffmpeg` | written here | FFmpeg rules for this repo (ffmpeg-full, timeline contract, filters in use) |
| `animejs` | written here (no upstream skill) | anime.js v4 timelines used by the slide templates |
| `threejs` | written here, points to upstream `llms.txt` | three.js scenes for the `3d` slide layout |
| `gsap-core`, `gsap-timeline`, `gsap-utils`, `gsap-performance` | greensock/gsap-skills (MIT) | animation timing/easing/perf knowledge (concepts transfer to anime.js) |
| `dotlottie-web` | LottieFiles/dotlottie-web (MIT) | Lottie assets, if adopted |

Engine: `slides_engine="animated"` on explainer videos — see SDD §7.2 and `src/mediaforge/video/animated.py`.

## Coverage after this change

| Need | Covered by |
|---|---|
| Video (narrated / short / explainer / screen) | existing pipeline + FFmpeg |
| Presentation | explainer: Pillow stills (default) or **animated slides** (anime.js) |
| 3D | `layout: "3d"` animated slide (three.js, WebGL in headless Chromium) |
| Audio | cloned TTS, music ducking, loudnorm (existing) |
| Render | FFmpeg compose; Chromium frame capture for animated scenes |

Open owner decisions: Remotion licence (rows 1, 11), GSAP licence (row 6), MPL-2.0 libraries (rows 5, 19)
[VERIFY: confirm with compliance].
