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
    blends = True   # 192-d speaker vector + reference codes: the vector can be mixed (pha giọng)

    def __init__(self) -> None:
        import torch
        from vieneu import Vieneu

        # The ONNX/CPU backend trips onnxruntime's external-data path check on HF-cache
        # symlinks, and MPS is faster anyway.
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.tts = Vieneu(backend="pytorch", device=device)
        self.sample_rate = self.tts.sample_rate

    def synth(self, text: str, lang: str, ref: VoiceRef | None, *, seed: int, temperature: float,
              prompt: VoiceRef | None = None) -> np.ndarray:  # no prompt channel: prompt is ignored
        import torch

        random.seed(seed)
        np.random.seed(seed % (2**32))
        torch.manual_seed(seed)
        kwargs = dict(temperature=temperature, apply_watermark=True)
        if ref is not None and ref.preset:
            kwargs["voice"] = ref.preset   # a preset speaker shipped with VieNeu (stock voice)
        elif ref is not None and ref.blend is not None:
            kwargs["voice"] = self.blend_voice(ref.audio_path, ref.blend.partner_audio, ref.blend.weight)
        elif ref is not None:
            kwargs["ref_audio"] = ref.audio_path
        audio = self.tts.infer(text, **kwargs)
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    def blend_voice(self, owner_audio: str, partner_audio: str, weight: float) -> dict:
        """Preset for infer(voice=…): the speaker vector slides from the owner's toward the partner's (slerp),
        the owner's reference codes stay the in-context prompt, so delivery and accent remain the owner's."""
        from mediaforge.voice.blend import slerp

        e_a, codes = self.tts.encode_reference(owner_audio)   # cached by VieNeu per clip
        e_b, _ = self.tts.encode_reference(partner_audio)
        emb = slerp(np.asarray(e_a).reshape(-1), np.asarray(e_b).reshape(-1), weight)
        return {"speaker_emb": emb, "codes": codes}

    def reference_embedding(self, audio_path: str) -> np.ndarray:
        """The 192-d speaker vector VieNeu enrols for a reference clip (denoised, trimmed)."""
        e, _ = self.tts.encode_reference(audio_path)
        return np.asarray(e, dtype=np.float32).reshape(-1)

    def speaker_embedding(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """The same speaker encoder applied to rendered audio: where an output sits between two voices."""
        import torch

        enc = self.tts.engine.speaker_encoder
        wav = torch.from_numpy(np.asarray(audio, dtype=np.float32).reshape(1, -1))
        return np.asarray(enc.embed(wav, sr), dtype=np.float32).reshape(-1)
