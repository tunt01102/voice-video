#!/bin/zsh
# Re-run `mediaforge music sync` every 10 minutes until every library track is downloaded.
# Wikimedia throttles bursts (HTTP 429, retry-after ~600 s), so a few rounds may be needed.
# Usage: scripts/music_sync_retry.sh [max_attempts=12] [interval_seconds=600]
set -uo pipefail
MAX="${1:-12}"
INTERVAL="${2:-600}"
BIN="$HOME/.local/bin/mediaforge"

for ((i = 1; i <= MAX; i++)); do
  echo "[$(date '+%H:%M:%S')] attempt $i/$MAX"
  if "$BIN" music sync; then
    echo "[$(date '+%H:%M:%S')] all tracks downloaded"
    exit 0
  fi
  ((i < MAX)) && { echo "some tracks still missing; retrying in $((INTERVAL / 60)) min"; sleep "$INTERVAL"; }
done
echo "[$(date '+%H:%M:%S')] gave up after $MAX attempts; run scripts/music_sync_retry.sh again later" >&2
exit 1
