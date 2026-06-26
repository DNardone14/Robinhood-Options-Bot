#!/usr/bin/env bash
# Start the assistant scheduler service (briefing + alerts + reports) in background.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f options_engine/.env ]; then set -a; source options_engine/.env; set +a; fi
if pgrep -f "options_engine.scheduler\|assistant --schedule" >/dev/null; then
  echo "assistant already running"; exit 0
fi
nohup python3 -m options_engine.assistant --schedule >> options_engine/assistant.log 2>&1 &
echo "started assistant scheduler (pid $!) -> options_engine/assistant.log"
