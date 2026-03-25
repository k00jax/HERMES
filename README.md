<p align="center">
  <img src="docs/assets/hermes-logo-hermes.jpg" alt="HERMES logo" width="500" />
</p>

**Heuristic Environmental Real-time Monitoring & Engagement System**

HERMES is first and foremost an attempt at building a real-world "Pip-Boy"-style wearable: a device that gives you live environmental information about your surroundings.

Today, the project runs as an edge platform while the wearable form factor is being developed. The current "home mode" is a proof-of-concept phase that validates sensing, logging, UI, and intelligence workflows before the final wearable implementation.

## What HERMES Does Right Now

- Reads live environmental telemetry from distributed MCUs and sensors.
- Aggregates and logs data on an Odroid edge node (SQLite + JSONL).
- Serves a local FastAPI dashboard for live view, history, analytics, calibration, and settings.
- Supports an optional `hermes-brain` daemon for context processing and candidate memory generation.
- Exposes `/context/*` endpoints for context status, candidate inspection, and ingest.

## Core Direction

- Keep HERMES local-first and robust without mandatory cloud dependencies.
- Build toward a wearable UX and hardware package.
- Expand sensing capabilities over time.
- Treat "home mode" as an engineering bridge, not the end product.

## Project Status

- Stage: Prototype and proof-of-concept (home-mode validation on edge hardware)
- Primary target: Wearable environmental monitor ("Pip-Boy"-style device)
- Stable baseline: sensor ingestion -> logger -> dashboard -> optional brain pipeline
- Active work: hardening, modularization, and sensor expansion toward wearable constraints

## Upcoming Sensor Expansion

Planned near-term additions include:

- air pressure sensor
- dedicated/true CO2 sensor

More sensors are expected as the wearable platform matures.

## Current Architecture (Prototype Phase)

HERMES currently runs as a three-tier stack:

1. Tier 1 - Sensor/interface MCUs (`nRF52840`, `ESP32-S3`)
2. Tier 2 - Edge compute node (`Odroid M1S`, logger, dashboard, timers/services)
3. Tier 3 - Optional cognition services (`hermes-brain`)

Detailed reference: `HERMES_MASTER.md`.

## Repository Layout

- `hermes/` - firmware, Odroid services, Linux tooling, deployment scripts
- `hermes-brain/` - ingestion, pipeline, retrieval, daemon, tests
- `docs/` - design notes, operations, and roadmap docs

## Quick Start

### Start local dashboard preview

```bash
cd hermes/linux/odroid/services/dashboard
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open: `http://localhost:8000/`

### Run brain test suite

```bash
cd hermes-brain
pytest
```

### Run logger on Odroid

```bash
python3 ~/hermes-src/hermes/linux/logger/daemon.py
```

## Common Tasks

### Flash nRF (from PC)

```powershell
cd hermes/firmware/nrf
.\tools\flash-nrf.ps1
```

### Configure logger as a systemd service (Odroid)

```bash
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-logger.service
```

### Push OLED context (Odroid)

```bash
~/hermes-src/hermes/tools/push_oled_context.sh
```

## Development Workflow

`main` is the only long-lived branch.

Use short-lived topic branches, open PRs into `main`, and delete topic branches after merge.

## Documentation

- `HERMES_MASTER.md` - full system architecture
- `docs/home-ai-core-plan.md` - phased development plan
- `docs/home-ai-core-repo-assessment.md` - what is currently implemented
- `docs/home-ai-core-ops.md` - operator/runtime workflow
- `hermes/docs/` - platform/firmware documentation
- `hermes-brain/docs/` - brain module documentation
