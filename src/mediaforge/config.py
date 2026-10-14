"""Runtime settings. Override any field with an env var MEDIAFORGE_<FIELD> (upper case)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# A checkout keeps data in <repo>/data (a symlink to the store); the installed tool (service, `mediaforge` CLI)
# has no repo around it, so it uses the store directly — the same place service.sh points the service at.
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" if (PROJECT_ROOT / "pyproject.toml").exists() else Path.home() / ".local/share/mediaforge"


def _env(name: str, default: str) -> str:
    return os.environ.get(f"MEDIAFORGE_{name.upper()}", default)


def _find_ffmpeg_full() -> str:
    """Prefer Homebrew's keg-only ffmpeg-full (has libass for burned-in subtitles)."""
    for cand in ("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/opt/ffmpeg-full/bin/ffmpeg"):
        if Path(cand).exists():
            return cand
    return shutil.which("ffmpeg") or "ffmpeg"


@dataclass
class Settings:
    host: str = field(default_factory=lambda: _env("host", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("port", "8765")))
    # Resolved so paths match whether we reach the store via the project's data/ symlink or directly.
    data_dir: Path = field(default_factory=lambda: Path(_env("data_dir", str(DEFAULT_DATA_DIR))).expanduser().resolve())
    ffmpeg: str = field(default_factory=lambda: _env("ffmpeg", _find_ffmpeg_full()))
    ffprobe: str = field(default_factory=lambda: _env("ffprobe", str(Path(_find_ffmpeg_full()).with_name("ffprobe"))))
    llm_order: list[str] = field(default_factory=lambda: _env("llm_order", "claude,ollama").split(","))
    claude_model: str = field(default_factory=lambda: _env("claude_model", "sonnet"))
    ollama_model: str = field(default_factory=lambda: _env("ollama_model", "qwen3:32b"))
    ollama_url: str = field(default_factory=lambda: _env("ollama_url", "http://127.0.0.1:11434"))
    asr_model: str = field(default_factory=lambda: _env("asr_model", "mlx-community/whisper-large-v3-turbo"))
    voxcpm_model: str = field(default_factory=lambda: _env("voxcpm_model", "mlx-community/VoxCPM2-8bit"))
    image_quantize: int = field(default_factory=lambda: int(_env("image_quantize", "8")))
    # QA thresholds (error rates after normalisation)
    qa_max_cer_vi: float = field(default_factory=lambda: float(_env("qa_max_cer_vi", "0.08")))
    qa_max_wer_en: float = field(default_factory=lambda: float(_env("qa_max_wer_en", "0.10")))
    # ACE-Step 1.5 sidecar (own venv; scripts/install_ace_step.sh) — optional "AI phối khí" accompaniment.
    ace_step_root: Path = field(default_factory=lambda: Path(_env("ace_step_root", "~/.local/share/mediaforge-sidecars/ace-step")).expanduser())
    qa_max_attempts: int = field(default_factory=lambda: int(_env("qa_max_attempts", "4")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mediaforge.sqlite3"

    def sub(self, *parts: str) -> Path:
        p = self.data_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
