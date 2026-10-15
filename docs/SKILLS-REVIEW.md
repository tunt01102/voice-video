# Skills & libraries review — 20 repos (2026-10-15)

Goal: pick the agent skills and libraries that give MediaForge full coverage of **video, presentations,
3D, audio and render**, under the project rule *Apache/MIT/BSD only* (CLAUDE.md, SDD §3).
Licences were read from the GitHub API / LICENSE / package metadata on 2026-10-15.

## Verdicts

| # | Repo | Licence | What it is | Verdict | Why |
|---|---|---|---|---|---|
| 1 | remotion-dev/skills | Remotion licence | 12 agent skills for Remotion | **Installed 11** (2026-10-16; not `remotion-saas`) | Owner: personal project → Remotion's free individual licence applies. |
| 2 | motiondivision/ai-kit | **no licence file** | `motion` skill (Motion + CSS animation, audits are Motion+ paid) | **Link only** | No licence ⇒ all rights reserved: not vendored. Install per-user via its own plugin if wanted. |
| 3 | greensock/gsap-skills | MIT (skills) | 8 GSAP skills | **Installed 5** (core, timeline, utils, performance, plugins) | Animation timing + SplitText/DrawSVG/MorphSVG. Scroll/React/framework skills irrelevant to a render pipeline. |
| 4 | svg/svgo | MIT | SVG optimiser | **Defer** | Useful only once we author SVG assets; nothing in the pipeline produces SVG today. |
| 5 | vercel/satori | MPL-2.0 | JSX/HTML/CSS → SVG (Node) | **Defer** (licence OK) | Licence fine for us; no gain: needs a Node sidecar and only a CSS subset, while Pillow + Chromium already render stills. |
| 6 | greensock/GSAP | GSAP Standard "no charge" licence (Webflow) | animation engine + free plugins | **Allowed** (2026-10-16) | No conflict for a personal video tool; anime.js stays in the existing templates, GSAP may be vendored when a template needs SplitText/MorphSVG. |
| 7 | juliangarnier/anime | MIT | animation engine (timelines, stagger, SVG) | **Adopted** — vendored 4.5.0 | Drives the animated slides; deterministic `timeline.seek()` = frame-exact render. |
| 8 | motiondivision/motion | MIT | React/JS animation | **Defer** | anime.js covers vanilla timelines; Motion shines in React UIs (dashboard is build-free). |
| 9 | rive-app/rive-wasm | MIT | Rive runtime | **Defer** | Needs `.riv` assets authored in the Rive editor; no asset pipeline yet. |
| 10 | LottieFiles/dotlottie-web | MIT | Lottie/dotLottie player (+ upstream `SKILL.md`) | **Skill installed**; lib deferred | Skill is MIT and self-contained; runtime only needed once we ship Lottie assets. |
| 11 | remotion-dev/remotion | Remotion licence (free for individuals / ≤ 3 staff) | React video framework | **Allowed** (2026-10-16), not wired into the pipeline | Skills installed for hand-built Remotion videos; MediaForge's own render path stays HTML + anime.js/three.js → FFmpeg. |
| 12 | motion-canvas/motion-canvas | MIT | code-driven animation + editor | **Defer** | Export runs from its Vite editor UI; no supported headless render, so it cannot be a job step. Good for hand-authored decks. |
| 13 | mrdoob/three.js | MIT | 3D engine (+ upstream `llms.txt`) | **Adopted** — vendored r186 | `3d` layout of animated slides. Project skill points at upstream `llms.txt`. |
| 14 | pmndrs/react-three-fiber | MIT | React renderer for three | **Defer** | Needs a React toolchain; plain three.js is enough inside a render template. |
| 15 | pmndrs/drei | MIT | R3F helpers | **Defer** | Depends on R3F (row 14). |
| 16 | pmndrs/react-three-rapier | MIT | physics for R3F | **Defer** | Depends on R3F; no physics need yet. |
| 17 | pmndrs/react-postprocessing | MIT | post FX for R3F | **Defer** | Depends on R3F. |
| 18 | goldfire/howler.js | MIT | browser audio playback | **Defer** | Audio is produced server-side (FFmpeg/pedalboard); Howler is only for in-browser playback. |
| 19 | Vanilagy/mediabunny | MPL-2.0 | in-browser mux/encode (WebCodecs) | **Defer** (licence OK) | Licence fine; no gain: our slides are DOM (WebCodecs encodes canvas/VideoFrames, not DOM) and FFmpeg encodes server-side. Candidate later for in-dashboard trim/convert of uploads. |
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
| `gsap-core`, `gsap-timeline`, `gsap-utils`, `gsap-performance`, `gsap-plugins` | greensock/gsap-skills (MIT) | GSAP timing, easing, perf, plugins (SplitText, DrawSVG, MorphSVG) |
| `remotion-*` (11, all but `remotion-saas`) | remotion-dev/skills | hand-built Remotion videos (create, markup, captions, render, studio, maps, …) |
| `dotlottie-web` | LottieFiles/dotlottie-web (MIT) | Lottie assets, if adopted |

GSAP 3.15.0 + SplitText vendored 2026-10-16 for slide text effects. Engine: `slides_engine="animated"` on explainer videos — see SDD §7.2 and `src/mediaforge/video/animated.py`.

## Coverage after this change

| Need | Covered by |
|---|---|
| Video (narrated / short / explainer / screen) | existing pipeline + FFmpeg |
| Presentation | explainer: Pillow stills (default) or **animated slides** (anime.js) |
| 3D | `layout: "3d"` animated slide (three.js, WebGL in headless Chromium) |
| Audio | cloned TTS, music ducking, loudnorm (existing) |
| Render | FFmpeg compose; Chromium frame capture for animated scenes |

## Licence decisions (owner, 2026-10-16 — personal test project)

Licence texts read 2026-10-16: Remotion `LICENSE.md` (repo), GSAP Standard "No Charge" License (gsap.com/standard-license,
effective 2025-04-30), `"license": "MPL-2.0"` in satori 0.60.0 / mediabunny 1.61.5 package.json.

| Library | Licence terms that matter | Verdict for MediaForge |
|---|---|---|
| Remotion | Free (even commercial) for individuals, for-profit orgs with ≤ 3 employees, non-profits; company licence otherwise. No reselling a Remotion derivative. | **OK as a personal project.** If MediaForge or its videos are used for Block8 work, the company size decides — re-check then [VERIFY: confirm with compliance]. |
| GSAP (incl. SplitText, DrawSVG, MorphSVG) | Free incl. commercial. Prohibited: tools that let users build animations through a visual no-code interface competing with Webflow; don't remove notices. Webflow may change or terminate the licence at its discretion. AI-generated GSAP code is explicitly fine. | **OK.** MediaForge generates videos from text; it is not an animation editor. Don't build a visual timeline/animation editor on GSAP; keep anime.js as the default so a licence change can't break renders. |
| MPL-2.0 (Satori, Mediabunny) | File-level weak copyleft: use unmodified in any project, any licence; only *modified MPL files* must stay MPL with source available **when distributed**; keep notices; includes a patent grant; GPL-compatible. | **OK** — nothing to do for local, undistributed, unmodified use. Not adopted because they add nothing today (rows 5, 19). |
