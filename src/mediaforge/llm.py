"""Text LLM access: Claude Code CLI first (subscription, no API key), Ollama fallback."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

import httpx

from mediaforge.config import settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _claude_env() -> dict[str, str]:
    # A nested `claude -p` launched from inside a Claude Code session misbehaves if it
    # inherits the parent session's control variables.
    return {
        k: v
        for k, v in os.environ.items()
        if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_") and k != "CLAUDE_PID"
    }


def _claude(prompt: str, system: str | None, timeout: int) -> str:
    exe = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    cmd = [exe, "-p", prompt, "--output-format", "json", "--model", settings.claude_model]
    if system:
        cmd += ["--append-system-prompt", system]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=_claude_env(), stdin=subprocess.DEVNULL
    )
    if proc.returncode != 0:
        raise LLMError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}")
    data = json.loads(proc.stdout)
    if data.get("is_error") or "result" not in data:
        raise LLMError(f"claude error: {str(data)[:400]}")
    return data["result"]


def _ollama(prompt: str, system: str | None, timeout: int, want_json: bool) -> str:
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body: dict[str, Any] = {"model": settings.ollama_model, "messages": msgs, "stream": False, "think": False}
    if want_json:
        body["format"] = "json"
    r = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"]


def complete(prompt: str, system: str | None = None, *, want_json: bool = False, timeout: int = 240) -> tuple[str, str]:
    """Return (text, provider). Tries providers in settings.llm_order."""
    errors = []
    for provider in settings.llm_order:
        try:
            if provider == "claude":
                return _claude(prompt, system, timeout), "claude"
            if provider == "ollama":
                return _ollama(prompt, system, timeout, want_json), "ollama"
        except Exception as e:  # noqa: BLE001 - fall through to next provider
            log.warning("LLM provider %s failed: %s", provider, e)
            errors.append(f"{provider}: {e}")
    raise LLMError("all LLM providers failed: " + " | ".join(errors))


def extract_json(text: str) -> Any:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"no JSON found in LLM output: {text[:300]}")


def complete_json(prompt: str, system: str | None = None, *, timeout: int = 240) -> tuple[Any, str]:
    sys_prompt = ((system + "\n\n") if system else "") + "Respond with valid JSON only. No prose, no markdown fences."
    last: Exception | None = None
    for _ in range(2):
        text, provider = complete(prompt, sys_prompt, want_json=True, timeout=timeout)
        try:
            return extract_json(text), provider
        except LLMError as e:
            last = e
    raise LLMError(str(last))
