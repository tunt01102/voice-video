---
name: animejs
description: anime.js v4 (MIT) — the animation engine of MediaForge's animated slides. Timelines, stagger, easings, SVG stroke drawing, number count-ups, and deterministic seeking for frame-by-frame rendering. Use when editing video/scene_web/scene.html or adding a slide layout or effect.
---

# anime.js v4 for frame-exact video

Vendored: `src/mediaforge/video/scene_web/vendor/anime.umd.min.js` (4.5.0) → global `anime`
(`const { createTimeline, animate, stagger, utils, svg } = anime`). Docs: https://animejs.com/documentation

## Rules for rendering (not for live web pages)

- Build everything on one `createTimeline({ autoplay: false, defaults: { ease: "outExpo" } })`.
- Drive it only with `tl.seek(ms)` from `__seek(t)`. No `autoplay`, `loop: true`, `requestAnimationFrame`,
  CSS transitions or `Date.now()` — they break determinism.
- Clamp: `tl.seek(Math.min(t * 1000, tl.duration))`.
- Anything continuous (ambient drift, 3D rotation) is a pure function of `t` written in `__seek`.

## v4 API cheatsheet (v3 differs!)

```js
tl.add(targets, { opacity: [0, 1], translateY: ["40px", 0], duration: 900, delay: stagger(70) }, 150) // abs. ms position
  .add(el, { scale: [0, 1.35, 1], ease: "outBack" }, "<<")      // "<<" = start of previous, "<" = its end, "<<+=200", "+=300"
tl.add(obj, { v: [0, 87], onUpdate: () => (num.textContent = obj.v.toFixed(0)) }, 800) // count-up on a plain object
tl.add("#arc", { strokeDashoffset: [100, 22] }, 800)              // SVG ring (pathLength="100")
```

Easing names: `linear`, `inQuad`… `outExpo`, `inOutQuart`, `outBack`, `outElastic(1, .5)`; springs via `createSpring({ stiffness, damping })`.
v3 → v4: `anime.timeline()` → `createTimeline()`, `easing:` → `ease:`, `'easeOutExpo'` → `'outExpo'`,
`anime.stagger` → `stagger`, `complete` → `onComplete`, `update` → `onUpdate`.

Timing concepts in the installed `gsap-timeline` / `gsap-performance` skills (position parameter, transforms
over layout properties) apply directly — but write anime.js, not GSAP (GSAP's licence is not on the project's
allowed list).
