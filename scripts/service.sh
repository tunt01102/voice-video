#!/bin/zsh
# Manage the MediaForge background service (launchd user agent, starts at login).
# Usage: scripts/service.sh install | update | uninstall | restart | status | logs
#
# macOS privacy (TCC) blocks background agents from reading ~/Documents, so the service runs
# a non-editable copy installed with `uv tool install` and keeps its data in ~/.local/share/mediaforge
# (the project's data/ is a symlink to it). After changing code, run `scripts/service.sh update`.
set -euo pipefail
LABEL="ai.block8.mediaforge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
DATA="$HOME/.local/share/mediaforge"
BIN="$HOME/.local/bin/mediaforge"
mkdir -p "$DATA/logs"

install_tool() {
  local reqs; reqs="$(mktemp)"
  "$UV" export --project "$ROOT" --frozen --no-hashes --no-emit-project --no-header -q > "$reqs"
  "$UV" tool install --force --python 3.12 --with-requirements "$reqs" "$ROOT"
  rm -f "$reqs"
}

case "${1:-status}" in
  install|update)
    install_tool
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$BIN</string><string>serve</string>
  </array>
  <key>WorkingDirectory</key><string>$DATA</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>$HOME</string>
    <key>MEDIAFORGE_DATA_DIR</key><string>$DATA</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$DATA/logs/server.log</string>
  <key>StandardErrorPath</key><string>$DATA/logs/server.log</string>
</dict></plist>
PL
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed $PLIST — http://127.0.0.1:8765" ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"; echo "removed" ;;
  restart)
    launchctl kickstart -k "gui/$(id -u)/$LABEL"; echo "restarted" ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|pid" | head -3 || echo "not installed"
    curl -s --max-time 2 http://127.0.0.1:8765/api/health || echo "server not responding" ;;
  logs)
    tail -n 100 -f "$DATA/logs/server.log" ;;
  *) echo "usage: $0 install|update|uninstall|restart|status|logs"; exit 1 ;;
esac
