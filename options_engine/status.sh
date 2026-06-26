#!/usr/bin/env bash
# Show engine status + recent log lines.
cd "$(dirname "$0")/.."
if pgrep -f "options_engine.main" >/dev/null; then
  echo "● running (pid $(pgrep -f options_engine.main))"
else
  echo "○ not running"
fi
echo "---- last 20 log lines ----"
tail -n 20 options_engine/engine.log 2>/dev/null || echo "(no log yet)"
