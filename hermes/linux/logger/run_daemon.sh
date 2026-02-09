#!/usr/bin/env bash
set -euo pipefail

SOCK="/tmp/hermesd.sock"
OUT="/tmp/hermesd.out"

rm -f "$SOCK"
nohup python3 -u "$(dirname "$0")/daemon.py" >"$OUT" 2>&1 &
echo "Started hermesd (pid=$!)"
echo "Log: $OUT"
echo "Sock: $SOCK"
