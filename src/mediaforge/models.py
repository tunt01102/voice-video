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


def unload(prefix: str = "") -> list[str]:
    with _lock:
        gone = [k for k in _cache if k.startswith(prefix)]
        for k in gone:
            del _cache[k]
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
        return gone


def loaded() -> list[dict[str, Any]]:
    with _lock:
        return [{"key": k, "last_used": t} for k, (_, t) in _cache.items()]
