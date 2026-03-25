<img src="docs/assets/hermes-logo-h.jpg" alt="HERMES H Logo" width="140" />

# HERMES

HERMES is an offline-first edge sensing and home-context platform that runs on local hardware, not cloud dependency. It combines MCU sensor ingestion, an Odroid edge data stack, a local dashboard, and an optional home-mode cognition layer (`hermes-brain`) for context building and memory candidate generation.

## Why HERMES

Most home intelligence systems start in the cloud and treat local devices as thin clients. HERMES inverts that:

- data collection and operational awareness happen locally first
- the system remains useful without internet or AI model access
- privacy controls are explicit before any data egress
- higher-level AI is optional and additive, not a hard requirement

## What It Does Today

- Collects live telemetry from distributed MCUs (radar, temperature/humidity, air quality, light, microphone, heartbeat).
- Logs telemetry on an Odroid edge node to SQLite plus raw JSONL.
- Runs a FastAPI dashboard for live status, history, analytics, calibration, reports, and settings.
- Runs an optional `hermes-brain` daemon that normalizes events, builds memory candidates, scores salience, and stores local context.
- Exposes dashboard `/context/*` routes to inspect pipeline health, candidates, packets, and ingest external context.

## System Architecture

HERMES is a three-tier stack:

1. Tier 1 - Sensor/interface MCUs (`nRF52840`, `ESP32-S3`)
2. Tier 2 - Edge compute node (`Odroid M1S`, logger, dashboard, timers/services)
3. Tier 3 - Optional home-mode cognition (`hermes-brain`)

Detailed reference: `HERMES_MASTER.md`.

## Repository Layout

- `hermes/` - firmware, Odroid services, Linux tooling, deployment scripts
- `hermes-brain/` - ingestion, pipeline, retrieval, daemon, tests
- `docs/` - design notes, operations, roadmap docs

## Get Started (5 Minutes)

### 1) Start local dashboard preview

```bash
cd hermes/linux/odroid/services/dashboard
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open: `http://localhost:8000/`

### 2) Run brain test suite

```bash
cd hermes-brain
pytest
```

### 3) Run logger on Odroid

```bash
python3 ~/hermes-src/hermes/linux/logger/daemon.py
```

## Current Status

### Implemented and stable

- MCU-to-Odroid ingestion and logger-to-SQLite path
- Multi-page dashboard with context API surface
- First home-mode pipeline slice in `hermes-brain`:
  - normalization
  - candidate building
  - salience scoring
  - local context store
  - privacy routing
  - optional compression/escalation plumbing

### In progress / next phases

- Retrieval over stored memory candidates
- Richer home-state abstraction (zones, devices, transitions)
- Multi-zone sensing support
- Incremental skill-style modules for context reasoning
- Safer, more expressive integration with downstream intelligence systems

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

Use short-lived topic branches, open PRs into `main`, and delete topic branches after merge. For larger changes, prefer phased rollouts with clear checkpoints and test coverage.

## Vision and Public Roadmap

HERMES is evolving toward a local cognition node for home and field contexts:

- reliable local sensing and memory as the foundation
- privacy-first data shaping and explicit egress policy
- modular architecture that can grow without breaking core logging paths
- optional AI layers that improve outcomes but never become a single point of failure

Long term, HERMES is intended to be one local component in a larger family/agent intelligence graph while retaining local control and graceful degradation.

## Documentation

- `HERMES_MASTER.md` - full system architecture
- `docs/home-ai-core-plan.md` - phased development plan
- `docs/home-ai-core-repo-assessment.md` - what is currently implemented
- `docs/home-ai-core-ops.md` - operator/runtime workflow
- `hermes/docs/` - platform/firmware documentation
- `hermes-brain/docs/` - brain module documentation
