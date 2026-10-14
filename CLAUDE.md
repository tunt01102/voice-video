# MediaForge — notes for Claude

- Design doc: docs/SDD.md (keep it in sync when changing architecture or engines). User guide: README.md.
- Python 3.12 via uv (`uv run …`). Package in src/mediaforge. Data (voices, outputs, sqlite) in data/ — git-ignored, contains biometric voice recordings: never commit, never send audio to an LLM.
- Single GPU worker thread runs every model call (jobs.py). Don't call models from request handlers; submit a job.
- TTS engines live behind tts/base.py `TTSEngine`; add new engines as adapters + registry, then route via DEFAULT_ROUTE / voice profile.
- VieNeu must use backend="pytorch", device="mps" (its ONNX path breaks on HF-cache symlinks).
- FFmpeg: use settings.ffmpeg (keg-only ffmpeg-full with libass), not the PATH ffmpeg.
- Nested `claude -p` calls need CLAUDECODE / CLAUDE_CODE_* stripped from env (llm.py does this).
- MCP SDK is v2 (`from mcp.server import MCPServer`); do not copy v1 FastMCP snippets.
- Service: scripts/service.sh install|restart|status|logs. Server binds 127.0.0.1:8765 only.
- Licences: only Apache/MIT/BSD engines; see SDD §3 "Rejected" before adding any model.
