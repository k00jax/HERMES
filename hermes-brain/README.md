# HERMES Brain

Offline-first local reasoning system for ODROID-M1S.

## Milestone 1: Local-only Q&A
- Local indexing of .txt/.md documents under knowledge/
- Simple keyword-based retrieval
- CLI answers questions using retrieved context

## Quickstart on ODROID
1) Install dependencies:

- Run scripts/setup_odroid.sh

2) Add knowledge files:

- Put .txt or .md files under knowledge/core or knowledge/deep

3) Build index and ask a question:

- Run scripts/run_cli.sh "What is in my knowledge base?"

If no local LLM is installed, the CLI returns a helpful placeholder response plus sources.

## Web augmentation (optional)
Offline mode is the default. Web access is only used when explicitly enabled and available.

- Enable web for a single query:
	- Run scripts/run_cli.sh --web "your question"

- Enable web globally:
	- Set HERMES_ALLOW_WEB=1 in your environment

Safety notes:
- Web retrieval is optional and gated by config and connectivity checks.
- Local retrieval is always preferred unless you use --web-only.

No-LLM behavior:
- If no local model is installed, the CLI returns retrieved context and notes that LLM is disabled.

## Sensor ingest (Milestone 2)
Run the serial ingestor to append sensor events to JSONL and inject a recent summary into prompts.

- Start ingest:
	- Run scripts/run_ingest.sh --port /dev/ttyACM0 --baudrate 115200

- The CLI automatically injects a "Recent sensor context" summary for the last 10 minutes.

## XIAO serial control
Find the serial port on Linux:
- Typical devices: /dev/ttyACM0 or /dev/ttyUSB0
- Check with: ls /dev/ttyACM* /dev/ttyUSB*

Ping test:
- Run: python -m app.main xiao --port /dev/ttyACM0 ping

Set LED and OLED:
- Run: python -m app.main xiao --port /dev/ttyACM0 led green
- Run: python -m app.main xiao --port /dev/ttyACM0 oled "Air quality bad"
