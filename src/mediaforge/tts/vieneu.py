"""VieNeu-TTS v3 Turbo adapter (Apache-2.0). Vietnamese specialist, also handles English."""

from __future__ import annotations

import random

import numpy as np

from mediaforge.tts.base import VoiceRef, register


@register
class VieNeuEngine:
    name = "vieneu"
    languages = {"vi", "en"}
    sample_rate = 48_000
    self_normalises = {"vi"}

    def __init__(self) -> None:
        import torch
        from vieneu import Vieneu

        # The ONNX/CPU backend trips onnxruntime's external-data path check on HF-cache
        # symlinks, and MPS is faster anyway.
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.tts = Vieneu(backend="pytorch", device=device)
        self.sample_rate = self.tts.sample_rate

    def synth(self, text: str, lang: str, ref: VoiceRef | None, *, seed: int, temperature: float) -> np.ndarray:
        import torch

        random.seed(seed)
        np.random.seed(seed % (2**32))
        torch.manual_seed(seed)
        kwargs = dict(temperature=temperature, apply_watermark=True)
        if ref is not None:
            kwargs["ref_audio"] = ref.audio_path
        audio = self.tts.infer(text, **kwargs)
        return np.asarray(audio, dtype=np.float32).reshape(-1)
