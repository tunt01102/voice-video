"""Model cache release: idle eviction, whisper holder, worker hooks (RAM leak: 79 GB held after 8 days)."""

import queue
import sys
import time
import types

import pytest

from mediaforge import models


@pytest.fixture(autouse=True)
def _clean_cache():
    models._cache.clear()
    yield
    models._cache.clear()


def _fake_whisper(monkeypatch):
    holder = type("ModelHolder", (), {"model": object(), "model_path": "mlx-community/whisper"})
    mod = types.ModuleType("mlx_whisper.transcribe")
    mod.ModelHolder = holder
    pkg = types.ModuleType("mlx_whisper")
    pkg.transcribe = mod
    monkeypatch.setitem(sys.modules, "mlx_whisper", pkg)
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", mod)
    return holder


def test_evict_idle_drops_only_old_models():
    now = time.time()
    models._cache["tts:old"] = (object(), now - 1000)
    models._cache["tts:fresh"] = (object(), now - 10)
    gone = models.evict_idle(600)
    assert gone == ["tts:old"]
    assert list(models._cache) == ["tts:fresh"]


def test_evict_idle_zero_disables():
    models._cache["tts:old"] = (object(), time.time() - 10_000)
    assert models.evict_idle(0) == []
    assert "tts:old" in models._cache


def test_unload_clears_whisper_holder(monkeypatch):
    holder = _fake_whisper(monkeypatch)
    models.unload()
    assert holder.model is None and holder.model_path is None


def test_evict_idle_clears_idle_whisper(monkeypatch):
    holder = _fake_whisper(monkeypatch)
    models.touch("asr:whisper")
    models._cache["asr:whisper"] = (None, time.time() - 1000)
    assert models.evict_idle(600) == ["asr:whisper"]
    assert holder.model is None


def test_settings_model_idle_seconds(monkeypatch):
    from mediaforge.config import Settings

    assert Settings().model_idle_seconds == 600
    monkeypatch.setenv("MEDIAFORGE_MODEL_IDLE_SECONDS", "0")
    assert Settings().model_idle_seconds == 0


class _StopLoop(BaseException):
    pass


def _run_loop(monkeypatch, jobs, gets):
    """Drive JobRunner._loop with scripted q.get results; returns the models calls it made."""
    from mediaforge import jobs as J
    from mediaforge import pipelines

    calls = []
    monkeypatch.setattr(models, "evict_idle", lambda s: calls.append(("evict", s)) or [], raising=False)
    monkeypatch.setattr(models, "unload", lambda p="": calls.append(("unload", p)) or [])
    monkeypatch.setattr(pipelines, "run", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(J.db, "get_job", lambda jid: jobs.get(jid))
    monkeypatch.setattr(J.db, "update_job", lambda *a, **k: None)
    r = J.JobRunner()
    seq = list(gets)
    timeouts = []

    def fake_get(*a, **k):
        timeouts.append(k.get("timeout"))
        if not seq:
            raise _StopLoop()
        item = seq.pop(0)
        if item is queue.Empty:
            raise queue.Empty
        return item

    monkeypatch.setattr(r.q, "get", fake_get)
    with pytest.raises(_StopLoop):
        r._loop()
    return calls, timeouts


def test_worker_evicts_when_queue_empty(monkeypatch):
    from mediaforge.config import settings

    monkeypatch.setattr(settings, "model_idle_seconds", 123, raising=False)
    calls, timeouts = _run_loop(monkeypatch, {}, [queue.Empty])
    assert timeouts[0] == 60
    assert ("evict", 123) in calls


def test_worker_unloads_image_after_image_job(monkeypatch):
    jobs = {"a": {"id": "a", "type": "image", "status": "queued", "params": {}},
            "b": {"id": "b", "type": "tts", "status": "queued", "params": {}}}
    calls, _ = _run_loop(monkeypatch, jobs, [(1, 0.0, "a"), (1, 0.0, "b")])
    assert calls.count(("unload", "image:")) == 1
