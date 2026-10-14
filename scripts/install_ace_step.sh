#!/usr/bin/env bash
# Install the ACE-Step 1.5 sidecar (MIT code + MIT weights) used for "AI phối khí" accompaniment.
# It lives in its own venv because its torch/transformers pins conflict with MediaForge's.
set -euo pipefail
DIR="${ACE_STEP_ROOT:-$HOME/.local/share/mediaforge-sidecars/ace-step}"
PIN="ca1e85f"   # tested 2026-10-14 on M4 Max (MLX DiT, ~9 s per 30 s clip warm; weights ~9.4 GB on first run)
if [ ! -d "$DIR/.git" ]; then
  mkdir -p "$(dirname "$DIR")"
  git clone https://github.com/ACE-Step/ACE-Step-1.5 "$DIR"
fi
git -C "$DIR" fetch -q origin && git -C "$DIR" checkout -q "$PIN"
cd "$DIR" && uv sync
echo "ACE-Step sidecar ready at $DIR (python: $DIR/.venv/bin/python)"
