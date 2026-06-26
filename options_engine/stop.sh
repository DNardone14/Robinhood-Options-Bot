#!/usr/bin/env bash
# Stop the background options engine.
if pgrep -f "options_engine.main" >/dev/null; then
  pkill -f "options_engine.main"
  echo "stopped options engine"
else
  echo "engine not running"
fi
