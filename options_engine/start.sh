#!/usr/bin/env bash
# Start the options engine in the background (loop + log). Run from /root/optbot.
set -euo pipefail
cd "$(dirname "$0")/.."          # parent of options_engine/ so -m works

if [ -f options_engine/.env ]; then set -a; source options_engine/.env; set +a; fi

if pgrep -f "options_engine.main" >/dev/null; then
  echo "engine already running (pid $(pgrep -f options_engine.main))"; exit 0
fi

nohup python3 -m options_engine.main --no-dashboard >> options_engine/engine.log 2>&1 &
echo "started options engine (pid $!) -> options_engine/engine.log"
