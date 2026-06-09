#!/usr/bin/env bash
# Install learning-system LaunchAgents idempotently. macOS only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
TEMPLATES_DIR="$SCRIPT_DIR/launchd"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "install_launchd.sh: macOS only (this script uses launchctl)." >&2
  exit 1
fi

mkdir -p "$LAUNCH_DIR"

# Uninstall any existing cron entries (sentinel-bounded block) to avoid duplicates
echo "Removing any prior learning-system cron entries..."
if crontab -l 2>/dev/null | grep -q "learning-system cron entries"; then
  crontab -l > "/tmp/crontab-backup-pre-launchd-$(date +%s).txt"
  crontab -l | awk '
    /^# learning-system cron entries/ {skip=1; next}
    /^# end learning-system cron entries/ {skip=0; next}
    /^PATH=/ && skip==0 {next}
    !skip {print}
  ' | crontab -
  echo "  Cron entries removed. Backup at /tmp/crontab-backup-pre-launchd-*.txt"
else
  echo "  No learning-system cron entries found."
fi

for TASK in extractor aggregator drift-monitor; do
  TEMPLATE="$TEMPLATES_DIR/$TASK.plist.template"
  LABEL="com.$USER.learning-system.$TASK"
  DEST="$LAUNCH_DIR/$LABEL.plist"

  if [[ ! -f "$TEMPLATE" ]]; then
    echo "  template missing: $TEMPLATE" >&2
    continue
  fi

  # Unload prior version if loaded
  launchctl unload "$DEST" 2>/dev/null || true

  # Render template -> destination
  sed "s|__USER__|$USER|g" "$TEMPLATE" > "$DEST"

  # Load the agent
  launchctl load "$DEST"
  echo "  installed: $LABEL"
done

# Register the SessionStart hook (event-driven extractor trigger)
echo "Registering SessionStart hook in ~/.claude/settings.json..."
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/install_hook.py" install

echo ""
echo "Done. Verify with:"
echo "  launchctl list | grep learning-system"
echo ""
echo "Manually trigger a job for testing:"
echo "  launchctl start com.$USER.learning-system.extractor"
