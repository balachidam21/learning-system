#!/usr/bin/env bash
# Install learning-system cron entries idempotently.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/cron/learning-system.crontab"
TMP_RENDERED=$(mktemp)
trap "rm -f $TMP_RENDERED" EXIT

sed "s|__USER__|$USER|g" "$TEMPLATE" > "$TMP_RENDERED"

# Pull current crontab (empty if none) and strip any prior learning-system block
# bounded by the sentinel comments. This preserves any unrelated entries.
EXISTING=$(crontab -l 2>/dev/null | awk '
  /^# learning-system cron entries/ {skip=1; next}
  /^# end learning-system cron entries/ {skip=0; next}
  !skip {print}
') || true

{ echo "$EXISTING"; echo; cat "$TMP_RENDERED"; } | crontab -
echo "Installed learning-system cron entries. Verify: crontab -l"
