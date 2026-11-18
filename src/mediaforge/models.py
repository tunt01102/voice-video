"""Process-wide lazy model cache. All model calls happen on the single GPU worker thread."""

from __future__ import annotations

import gc
import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)
_cache: dict[str, tuple[Any, float]] = {}
_lock = threading.RLock()


def get(key: str, loader: Callable[[], Any]) -> Any:
    with _lock:
        if key in _cache:
            obj, _ = _cache[key]
            _cache[key] = (obj, time.time())
            return obj
        log.info("loading model %s", key)
        t = time.time()
        obj = loader()
        log.info("loaded %s in %.1fs", key, time.time() - t)
        _cache[key] = (obj, time.time())
        return obj


def touch(key: str) -> None:
    """Record a use of a model held outside this cache (e.g. mlx_whisper's ModelHolder) so idle eviction sees it."""
    with _lock:
        _cache[key] = (_cache.get(key, (None, 0.0))[0], time.time())


def _clear_whisper() -> None:
    """mlx_whisper keeps its own model in a class attribute; drop it so the weights can be freed."""
    try:
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.model = None
        ModelHolder.model_path = None
    except Exception:  # noqa: BLE001
        pass


def _release() -> None:
    gc.collect()
    try:
        import mlx.core as mx

        mx.clear_cache()
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def unload(prefix: str = "") -> list[str]:
    with _lock:
        gone = [k for k in _cache if k.startswith(prefix)]
        for k in gone:
            del _cache[k]
        if "asr:".startswith(prefix) or prefix.startswith("asr:"):
            _clear_whisper()
        _release()
        return gone


def evict_idle(max_idle: float) -> list[str]:
    """Free models not used for more than max_idle seconds (0 or less disables). Call only from the GPU worker."""
    if max_idle <= 0:
        return []
    with _lock:
        cutoff = time.time() - max_idle
        gone = [k for k, (_, t) in _cache.items() if t < cutoff]
        if not gone:
            return []
        for k in gone:
            del _cache[k]
        if "asr:whisper" in gone:
            _clear_whisper()
        _release()
        log.info("evicted idle models: %s", ", ".join(gone))
        return gone


def loaded() -> list[dict[str, Any]]:
    with _lock:
        return [{"key": k, "last_used": t} for k, (_, t) in _cache.items()]
