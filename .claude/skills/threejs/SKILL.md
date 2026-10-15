---
name: threejs
description: three.js (MIT) for MediaForge's 3D slides — scene/camera/light setup, geometries, materials, rendering a frame as a pure function of time inside headless Chromium. Use when editing the `3d` layout in video/scene_web/scene.html, adding 3D shapes or effects, or answering three.js API questions.
---

# three.js in MediaForge

Vendored: `src/mediaforge/video/scene_web/vendor/three.module.min.js` (r186, bundled from `three.module.js` +
`three.core.js` with esbuild). Import: `import * as THREE from "/vendor/three.module.min.js";`
Addons (OrbitControls, GLTFLoader, postprocessing) are not vendored — bundle the one you need the same way
(`npx esbuild <file> --bundle --minify --format=esm`) and keep `LICENSE-three`.

API reference for agents: https://threejs.org/llms.txt (index) and https://threejs.org/docs/llms-full.txt.
Fetch the specific page rather than guessing — the API changes between releases (r150+ removed many legacy
names; colour management and light intensity units changed in r155).

## Rendering rules

- `new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true })` —
  `preserveDrawingBuffer` is required for screenshots; `setPixelRatio(1)`.
- No animation loop. `render(t)` sets every transform from `t` and calls `renderer.render(scene, camera)`;
  `__seek(t)` calls it. Randomness comes from fixed seeds (index-based), never `Math.random()` at frame time.
- Headless Chromium runs WebGL through ANGLE/Metal (`--use-angle=metal`); keep geometry modest (< ~200k tris)
  so a frame stays ~15 ms.
- Colours come from the slide theme (`data.colors.accent`), so 3D matches the deck.

React wrappers (react-three-fiber, drei, rapier, postprocessing) were reviewed and deferred — plain three.js
in the template needs no React toolchain (docs/SKILLS-REVIEW.md).
