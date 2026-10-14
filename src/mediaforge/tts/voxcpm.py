"""VoxCPM2 adapter via mlx-audio (Apache-2.0 weights, MLX native). 30 languages incl. vi."""

from __future__ import annotations

import inspect

import numpy as np

from mediaforge.config import settings
from mediaforge.tts.base import VoiceRef, register


@register
class VoxCPMEngine:
    name = "voxcpm"
    languages = {"en", "vi"}
    sample_rate = 48_000
    self_normalises: set[str] = set()

    def __init__(self) -> None:
        from mlx_audio.tts.utils import load_model

        self.model = load_model(settings.voxcpm_model)
        self.sample_rate = int(getattr(self.model, "sample_rate", 48_000))
        self._params = set(inspect.signature(self.model.generate).parameters)

    def synth(self, text: str, lang: str, ref: VoiceRef | None, *, seed: int, temperature: float,
              prompt: VoiceRef | None = None) -> np.ndarray:
        import mlx.core as mx

        mx.random.seed(seed)
        kwargs: dict = {"text": text}
        if ref is not None:
            kwargs["ref_audio"] = ref.audio_path
            if prompt is not None and prompt.text:
                # Guided repair: timbre from the canonical clip, prosody continued from the owner's guide.
                kwargs["prompt_audio"] = prompt.audio_path
                kwargs["prompt_text"] = prompt.text
            elif ref.text:
                # "Combined" mode (reference + continuation prompt) gives the closest timbre.
                kwargs["prompt_audio"] = ref.audio_path
                kwargs["prompt_text"] = ref.text
        # VoxCPM2 is a diffusion decoder without sampling temperature; vary CFG on retries.
        kwargs["cfg_value"] = 2.0 + max(0.0, 0.8 - temperature)
        parts = [np.array(r.audio, dtype=np.float32).reshape(-1) for r in self.model.generate(**kwargs)]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
