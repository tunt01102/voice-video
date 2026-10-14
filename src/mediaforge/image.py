"""Local image generation: mflux Z-Image-Turbo (MLX); Ollama x/z-image-turbo as fallback."""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from mediaforge import models
from mediaforge.config import settings

log = logging.getLogger(__name__)

STYLE_PRESETS = {
    "cinematic": "cinematic still, dramatic lighting, shallow depth of field, 35mm film, rich colour grading",
    "photo": "professional photograph, natural light, sharp focus, high detail",
    "illustration": "clean modern digital illustration, flat shading, vibrant palette, editorial style",
    "3d": "3D render, soft global illumination, octane, smooth materials, studio lighting",
    "minimal": "minimalist composition, lots of negative space, soft pastel palette, simple shapes",
    "tech": "sleek technology aesthetic, dark background, neon cyan and violet accents, glowing UI elements",
    "chibi": "chibi anime style, cute small cartoon characters with oversized heads, tiny bodies and big sparkling "
             "eyes, kawaii, soft pastel colours, clean bold lineart, cel shading, playful cheerful mood, "
             "simple soft background",
    "none": "",
}

ASPECTS = {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (1024, 1024), "4:3": (1152, 864), "3:4": (864, 1152)}
# Measured on M4 Max (Z-Image-Turbo q8): 1344x768/9 steps ≈ 55 s, 1024x576/9 ≈ 30 s, 1024x576/6 ≈ 20 s.
# Videos use "standard": frames are upscaled for Ken Burns anyway and quality is near-identical.
QUALITY = {"draft": (0.75, 6), "standard": (0.75, 7), "high": (1.0, 9)}


def _mflux_model():
    from mflux.models.common.config import ModelConfig
    from mflux.models.z_image import ZImage

    return ZImage(model_config=ModelConfig.z_image_turbo(), quantize=settings.image_quantize)


MFLUX_REPO = "Tongyi-MAI/Z-Image-Turbo"


def mflux_ready() -> bool:
    """True when the MLX weights are fully cached (avoid blocking a job on a 30 GB download)."""
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(MFLUX_REPO, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _size(aspect: str | None, width: int | None, height: int | None, scale: float = 1.0) -> tuple[int, int]:
    if width and height:
        return width // 16 * 16, height // 16 * 16
    w, h = ASPECTS.get(aspect or "16:9", ASPECTS["16:9"])
    return int(w * scale) // 16 * 16, int(h * scale) // 16 * 16


def generate(
    prompt: str, *, out_path: str | Path | None = None, aspect: str = "16:9", width: int | None = None,
    height: int | None = None, seed: int | None = None, steps: int | None = None, style: str = "none",
    quality: str = "high",
    negative_prompt: str | None = None, backend: str = "auto",
) -> dict[str, Any]:
    scale, default_steps = QUALITY.get(quality, QUALITY["high"])
    steps = steps or default_steps
    w, h = _size(aspect, width, height, scale)
    seed = int(seed if seed is not None else time.time() * 1000) % 2**31
    full_prompt = ", ".join(p for p in (prompt.strip(), STYLE_PRESETS.get(style, style)) if p)
    out_path = Path(out_path or settings.sub("outputs", "images") / f"{time.strftime('%Y%m%d-%H%M%S')}-{seed}.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t = time.time()
    if backend == "auto":
        backend = "mflux" if mflux_ready() else "ollama"
    used = backend
    # Deterministic cache: re-rendering a video (e.g. after re-recording one sentence) reuses its images.
    import hashlib
    import shutil

    ckey = hashlib.sha256(f"{full_prompt}|{seed}|{w}x{h}|{steps}|{negative_prompt}".encode()).hexdigest()[:24]
    cached = settings.sub("cache", "images") / f"{ckey}.png"
    if cached.exists():
        shutil.copy(cached, out_path)
        return {"image": str(out_path), "width": w, "height": h, "seed": seed, "prompt": full_prompt,
                "backend": "cache", "seconds": 0.0}
    try:
        if backend != "mflux":
            raise RuntimeError("ollama selected")
        model = models.get("image:zimage", _mflux_model)
        img = model.generate_image(
            seed=seed, prompt=full_prompt, num_inference_steps=steps, width=w, height=h,
            negative_prompt=negative_prompt,
        )
        img.save(str(out_path))
    except Exception as e:  # noqa: BLE001
        if backend == "mflux":
            log.warning("mflux failed (%s); falling back to Ollama", e)
        used = "ollama"
        r = None
        for attempt in range(3):  # Ollama's image runner occasionally 500s under GPU contention
            r = httpx.post(
                f"{settings.ollama_url}/api/generate",
                json={"model": "x/z-image-turbo", "prompt": full_prompt, "stream": False,
                      "width": w, "height": h, "steps": steps, "seed": seed},
                timeout=900,
            )
            if r.status_code < 500:
                break
            log.warning("ollama image attempt %d failed: %s %s", attempt + 1, r.status_code, r.text[:200])
            time.sleep(3 * (attempt + 1))
        r.raise_for_status()
        data = r.json()
        b64 = data.get("image") or (data.get("images") or [None])[0]
        if not b64:
            raise RuntimeError(f"Ollama returned no image: {str(data)[:200]}") from e
        out_path.write_bytes(base64.b64decode(b64))
    shutil.copy(out_path, cached)
    return {"image": str(out_path), "width": w, "height": h, "seed": seed, "prompt": full_prompt,
            "backend": used, "seconds": round(time.time() - t, 1)}
