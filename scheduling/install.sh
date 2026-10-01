#!/bin/bash
# Install (or refresh) the launchd schedule for the poly-options collector.
#
#   ./scheduling/install.sh            install / refresh
#   ./scheduling/install.sh --uninstall  remove
#
# Nothing here needs hand-editing: the repository path is detected, and the
# scheduled jobs' wall-clock times are converted from America/New_York into this
# machine's local timezone. Re-run it after a US DST change to re-convert.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LABELS=(com.polyoptions.snapshot com.polyoptions.daily com.polyoptions.report)

uninstall() {
    for label in "${LABELS[@]}"; do
        if launchctl list | grep -q "$label"; then
            launchctl bootout "gui/$UID/$label" 2>/dev/null || launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
            echo "unloaded $label"
        fi
        rm -f "$AGENTS/$label.plist"
    done
    echo "uninstalled."
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
    exit 0
fi

if [[ ! -x "$REPO/.venv/bin/python" ]]; then
    echo "error: $REPO/.venv/bin/python not found. Create the venv first:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# ET -> local wall clock, via the tz database (never a hard-coded offset).
# 17:30 ET is the metadata refresh; 18:00 ET is the model report that follows it.
read -r DAILY_HOUR DAILY_MINUTE REPORT_HOUR REPORT_MINUTE <<<"$(
    "$REPO/.venv/bin/python" - <<'PY'
import datetime as dt
from zoneinfo import ZoneInfo

et = ZoneInfo("America/New_York")
today = dt.datetime.now(et).date()
out = []
for hour, minute in ((17, 30), (18, 0)):
    target = dt.datetime(today.year, today.month, today.day, hour, minute, tzinfo=et)
    local = target.astimezone()
    out += [local.hour, local.minute]
print(*out)
PY
)"

echo "repo:        $REPO"
echo "daily job:   17:30 America/New_York  ->  ${DAILY_HOUR}:$(printf '%02d' "$DAILY_MINUTE") local"
echo "report job:  18:00 America/New_York  ->  ${REPORT_HOUR}:$(printf '%02d' "$REPORT_MINUTE") local"
echo "snapshot:    every 15 min (job itself skips off-hour ticks and holidays)"

mkdir -p "$AGENTS" "$REPO/data/logs"

for label in "${LABELS[@]}"; do
    src="$REPO/scheduling/$label.plist"
    dst="$AGENTS/$label.plist"
    sed -e "s|__REPO__|$REPO|g" \
        -e "s|__DAILY_HOUR__|$DAILY_HOUR|g" \
        -e "s|__DAILY_MINUTE__|$DAILY_MINUTE|g" \
        -e "s|__REPORT_HOUR__|$REPORT_HOUR|g" \
        -e "s|__REPORT_MINUTE__|$REPORT_MINUTE|g" \
        "$src" > "$dst"
    plutil -lint "$dst" >/dev/null

    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$dst" 2>/dev/null || launchctl load "$dst"
    echo "installed $label"
done

echo
echo "installed. check with:  launchctl list | grep polyoptions"
echo "logs:                   $REPO/data/logs/"
echo "inspect data:           $REPO/.venv/bin/python $REPO/data/inspect.py"
echo "latest report:          $REPO/model/reports/"
