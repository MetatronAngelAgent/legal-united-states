#!/bin/sh
# install_daily.sh — schedule update_legal.py to run every day.
#
#   macOS: a launchd agent (~/Library/LaunchAgents/com.legal-united-states.daily.plist).
#          launchd runs a missed job when the Mac wakes, so a laptop that was asleep at the
#          scheduled time still updates that day.
#   Linux: a crontab line.
#
# Usage:  sh install_daily.sh [--root ~/Documents/OpenKnowledge/Cuh/legal] [--time 05:40]
#         sh install_daily.sh --uninstall
#
# Secrets (optional) go in ~/.config/legal-united-states/env, e.g.
#   COURTLISTENER_TOKEN=xxxxxxxx        # daily court opinions (free token at courtlistener.com)
#   LEGAL_US_CONTACT=you@example.com    # contact shown in the crawler User-Agent
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT="$HOME/Documents/OpenKnowledge/Cuh/legal"
TIME="05:40"
UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --time) TIME="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
HOUR=$(echo "$TIME" | cut -d: -f1 | sed 's/^0//'); [ -z "$HOUR" ] && HOUR=0
MIN=$(echo "$TIME" | cut -d: -f2 | sed 's/^0//'); [ -z "$MIN" ] && MIN=0
PY=$(command -v python3)
LABEL="com.legal-united-states.daily"

mkdir -p "$HOME/.config/legal-united-states"
ENVF="$HOME/.config/legal-united-states/env"
[ -f "$ENVF" ] || { printf '# COURTLISTENER_TOKEN=\n# LEGAL_US_CONTACT=\n' > "$ENVF"; chmod 600 "$ENVF"; }

if [ "$(uname)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  if [ "$UNINSTALL" = 1 ]; then rm -f "$PLIST"; echo "removed $LABEL"; exit 0; fi
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$HERE/update_legal.py</string>
    <string>--root</string><string>$ROOT</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/legal-united-states-update.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/legal-united-states-update.log</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "installed: runs daily at $TIME — log: ~/Library/Logs/legal-united-states-update.log"
  echo "run now:   launchctl kickstart gui/$(id -u)/$LABEL"
else
  CRON="$MIN $HOUR * * * $PY $HERE/update_legal.py --root $ROOT >> \$HOME/.cache/legal-united-states-update.log 2>&1"
  ( crontab -l 2>/dev/null | grep -v 'update_legal.py' || true; [ "$UNINSTALL" = 1 ] || echo "$CRON" ) | crontab -
  [ "$UNINSTALL" = 1 ] && echo "removed cron entry" || echo "installed cron entry: $CRON"
fi
echo "reports: $ROOT/references/updates/"
