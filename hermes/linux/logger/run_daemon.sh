#!/usr/bin/env bash
set -euo pipefail

SOCK="/tmp/hermesd.sock"
OUT="/tmp/hermesd.out"
SERVICE="hermes-logger.service"

if command -v systemctl >/dev/null 2>&1; then
	if systemctl is-active --quiet "$SERVICE"; then
		echo "Refusing to start standalone daemon: $SERVICE is active."
		echo "Use: sudo systemctl restart $SERVICE"
		exit 1
	fi
fi

rm -f "$SOCK"
nohup python3 -u "$(dirname "$0")/daemon.py" >"$OUT" 2>&1 &
echo "Started hermesd (pid=$!)"
echo "Log: $OUT"
echo "Sock: $SOCK"
