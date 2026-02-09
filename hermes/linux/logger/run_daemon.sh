#!/usr/bin/env bash
set -euo pipefail

SOCK=/tmp/hermesd.sock
rm -f "$SOCK"

nohup python3 "$(dirname "$0")/daemon.py" >/tmp/hermesd.out 2>&1 &
