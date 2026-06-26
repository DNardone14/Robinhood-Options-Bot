#!/usr/bin/env bash
# Start the FastAPI mobile dashboard in background.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f options_engine/.env ]; then set -a; source options_engine/.env; set +a; fi
if pgrep -f "options_engine.webapp" >/dev/null; then echo "dashboard already running"; exit 0; fi
nohup python3 -m options_engine.assistant --dashboard >> options_engine/dashboard.log 2>&1 &
echo "started dashboard (pid $!). Open http://<server-ip>:${DASHBOARD_PORT:-8080}/"
