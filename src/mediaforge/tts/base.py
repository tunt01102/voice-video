"""The engine boundary: everything above this file is engine-agnostic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class BlendSpec:
    """Pha giọng: borrow part of a second voice's identity. `weight` is the engine-internal mix (already
    calibrated from the perceived share, see voice/blend.py); the owner's reference stays the prompt."""
    partner_audio: str   # the partner voice's reference clip
    weight: float        # 0 = owner only, 1 = partner's speaker vector
    partner_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.partner_audio}@{self.weight:.3f}"


@dataclass(frozen=True)
class VoiceRef:
    audio_path: str      # cleaned reference clip (48 kHz mono wav); "" for a stock voice
    text: str            # transcript of the clip
    lang: str            # language of the clip
    blend: BlendSpec | None = None   # set for blend profiles; only engines with `blends = True` honour it
    # Stock voices (voice/stock.py) have no recording: a VieNeu preset speaker, or a VoxCPM2 voice designed from a
    # text description. Only the engine named in the profile can render them.
    preset: str | None = None
    instruct: str | None = None

    @property
    def key(self) -> str:
        """Identity of the speaker for caches."""
        if self.preset:
            return f"preset:{self.preset}"
        if self.instruct:
            return f"instruct:{self.instruct}"
        return self.audio_path


class TTSEngine(Protocol):
    name: str
    languages: set[str]
    sample_rate: int
    self_normalises: set[str]   # languages for which the engine normalises raw text itself
    blends: bool                # can mix a second speaker into the voice (VoiceRef.blend)

    def synth(self, text: str, lang: str, ref: VoiceRef | None, *, seed: int, temperature: float,
              prompt: VoiceRef | None = None) -> np.ndarray: ...
    # prompt: optional prosody guide (the owner reading this very sentence). Engines without a
    # continuation-prompt channel ignore it; `ref` always sets the timbre.


_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    _REGISTRY[cls.name] = cls
    return cls


def _ensure_loaded() -> None:
    if not _REGISTRY:
        from mediaforge.tts import vieneu, voxcpm  # noqa: F401 - registers engines


def get_engine(name: str) -> TTSEngine:
    from mediaforge import models

    _ensure_loaded()
    if name not in _REGISTRY:
        raise ValueError(f"unknown TTS engine {name!r}; available: {sorted(_REGISTRY)}")
    return models.get(f"tts:{name}", _REGISTRY[name])


def engines_for(lang: str) -> list[str]:
    _ensure_loaded()
    return [n for n, c in _REGISTRY.items() if lang in c.languages]


def blend_engines(lang: str) -> list[str]:
    """Engines that can render a blend profile (VoiceRef.blend) in this language."""
    _ensure_loaded()
    return [n for n, c in _REGISTRY.items() if lang in c.languages and getattr(c, "blends", False)]


DEFAULT_ROUTE = {"vi": "vieneu", "en": "voxcpm"}


def self_normalises(name: str, lang: str) -> bool:
    _ensure_loaded()
    cls = _REGISTRY.get(name)
    return bool(cls and lang in cls.self_normalises)
