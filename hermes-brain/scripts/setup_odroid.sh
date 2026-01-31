#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip build-essential

python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
