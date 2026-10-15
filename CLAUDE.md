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
- Project skills in .claude/skills (review: docs/SKILLS-REVIEW.md). Animated slides = anime.js + three.js in
  video/scene_web, rendered by video/animate.py (Playwright → FFmpeg). Owner decisions 2026-10-16 (personal test
  project): Remotion (free individual licence) and GSAP ("no charge" licence) are allowed; MPL-2.0 libs are
  licence-OK but not adopted (no gain yet). Existing slide templates stay on anime.js; never build a no-code visual
  animation editor on GSAP (prohibited use). Licences: docs/SKILLS-REVIEW.md §Licence decisions.
- Tests: `uv run --with pytest python -m pytest -q` (pytest is not in the venv). E2E/GPU tests only on a copy of the
  data store (`MEDIAFORGE_DATA_DIR=<copy> uv run mediaforge serve --port 8766`; symlink `music/` before starting) and
  only when `/api/health` shows `running: null`. Deploy = `scripts/service.sh update`, then check /api/health + logs.
- Dashboard screenshots: headless Chrome hangs on SSE — wrap it in `perl -e 'alarm 30; exec @ARGV' … --headless=new
  --virtual-time-budget=8000 --screenshot=…`. New elements inside `.job-out` (flex) need `flex: 1 0 100%`.
