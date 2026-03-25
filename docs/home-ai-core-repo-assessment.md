# HERMES Repo Assessment — Home AI Core (current)

*Updated: 2026-03-24*

This document describes **what the repository implements today** for the home-AI / home-mode pipeline. For operator commands and environment variables, see [home-ai-core-ops.md](home-ai-core-ops.md). For design intent and phased roadmap, see [home-ai-core-plan.md](home-ai-core-plan.md).

---

## What HERMES Is (stack)

HERMES is a **three-tier, offline-first** sensing platform on an **Odroid M1S** with **Tier 1** MCUs. The **home-mode cognition path** (below) is optional: it runs only when the **`hermes-brain` daemon** is started; it does not replace logging or the dashboard.

### Tier 1 — Embedded MCUs

| MCU | Role |
|-----|------|
| **nRF52840** | I2C sensor bus (SHT31, SGP30), OLED (2×128×64), buttons, buzzer, LEDs |
| **ESP32-S3** | LD2410B-P radar UART, throttled telemetry (≤10 Hz), optional WiFi |

Protocol: UART 115200, ASCII line-framed KV pairs. Frame types include `RADAR`, `HB`, `ENV`, `AIR`, `LIGHT`, `MIC`, `BTN`, `ACK`, `ESP`, `LOG`, `EVT`.

### Tier 2 — Edge compute (Odroid)

| Service | Mechanism | Role |
|---------|-----------|------|
| `hermes-logger` | Python systemd daemon | Serial → SQLite + raw JSONL |
| `hermes-dashboard` | FastAPI on :8000 | REST API + HTML UI + **`/context/*`** routes |
| `hermes-brain` (daemon) | `python -m app.daemon` ([hermes-brain/app/daemon.py](../hermes-brain/app/daemon.py)) | Home-AI pipeline loop (see Tier 3) |
| `hermes-events-emitter` | systemd timer | Anomaly detection (stale, recovery, reboot) |
| `hermes-oled-context` | systemd timer | Push delta context to nRF OLED |
| `hermes-dashboard-watchdog` | systemd timer | Health check + auto-restart |

**Database:** SQLite at `~/hermes-data/db/hermes.sqlite3` (logger-owned writes; pipeline **reads** sensor tables for normalization).

**Dashboard API:** Existing sensor/UI routes plus **context router** — `GET /context/status`, `GET /context/candidates`, `GET /context/packets`, `POST /context/ingest` ([dashboard/routes/context.py](../hermes/linux/odroid/services/dashboard/dashboard/routes/context.py)).

### Tier 3 — Home-mode pipeline (`hermes-brain`)

**Runtime:** Long-lived **daemon** (not the CLI `app.main`). **CLI** remains for Q&A, serial ingest, XIAO control ([hermes-brain/app/main.py](../hermes-brain/app/main.py)).

**One cycle (simplified):**

1. **Normalize** — SQLite rows → `HomeEvent` ([pipeline/normalizer.py](../hermes-brain/app/pipeline/normalizer.py))
2. **Omi queue** — Merge lines from `~/hermes-data/omi_queue.jsonl` (ingested via dashboard POST)
3. **Build candidates** — Time-window bundles ([pipeline/candidate_builder.py](../hermes-brain/app/pipeline/candidate_builder.py))
4. **Score** — Rule-based salience; `use_llm=False` in daemon ([pipeline/salience_scorer.py](../hermes-brain/app/pipeline/salience_scorer.py))
5. **Compress (optional)** — If `compression_enabled` and model path exists, `LocalLLM` summaries ([pipeline/compressor.py](../hermes-brain/app/pipeline/compressor.py))
6. **Privacy route** — Allowlist → `EscalationPacket` ([pipeline/privacy_router.py](../hermes-brain/app/pipeline/privacy_router.py))
7. **Store** — Candidates ≥ salience threshold → JSONL under `~/hermes-data/context/` ([pipeline/context_store.py](../hermes-brain/app/pipeline/context_store.py))
8. **Deliver** — Queue/send packets ([escalation/cloud_client.py](../hermes-brain/app/escalation/cloud_client.py)); empty endpoint = offline

**Config:** [hermes-brain/app/config.py](../hermes-brain/app/config.py) — env and optional `config.yaml` (pipeline interval, thresholds, escalation endpoint, `HERMES_COMPRESSION_ENABLED`, `HERMES_MODEL_PATH`, `HERMES_LLAMA_BIN`, etc.).

**Tests:** [hermes-brain/tests/](../hermes-brain/tests/) — pipeline modules, daemon cycle smoke, Omi adapter, compressor.

**Other `hermes-brain` modules (parallel concerns):** `app/retrieval/*` (TF-IDF over `knowledge/*.md`), `app/ingest/event_store.py`, `app/llm/local_llm.py`, `app/ingest/xiao_link.py` — used by CLI and tooling; not required for the daemon loop to run.

---

## Sensors (live data path)

| Sensor | Fields | Rate |
|--------|--------|------|
| LD2410B radar | `target`, `move_cm`, `stat_cm`, `detect_cm` | ≤10 Hz |
| SHT31 | `temp_c`, `hum_pct` | 1 Hz |
| SGP30 | `eco2_ppm`, `tvoc_ppb` | 1 Hz |
| Light | `light_level` | 1 Hz |
| Microphone | `mic_level` | 1 Hz |
| Heartbeat | `tick_ms`, `seq` | 1 Hz |

---

## Implemented vs not yet

### Implemented (home-AI scope)

- Normalized `HomeEvent` / `MemoryCandidate` pipeline and JSONL context store
- Rule-based salience; optional LLM **compression** (not salience LLM in daemon)
- Privacy allowlist and escalation client (queue + optional POST)
- Omi ingestion via dashboard queue + adapter
- Dashboard `/context/*` and `pipeline_status.json` written each cycle

### Not present / deferred

- Explicit **home vs wearable** mode flag (product framing: home-mode = docked/charging; pipeline is appropriate for that context)
- **Phase 3+** retrieval over stored candidates, multi-zone, skill registry ([home-ai-core-plan.md](home-ai-core-plan.md))
- Salience refinement via LLM (`use_llm=True` path exists but daemon keeps `False`)
- Rich **home-state** abstraction (rooms, devices) beyond tags/windows
- Camera routes remain stubs (`/flip`, `/camera/stream`)

---

## Stable components (touch carefully)

| Component | Notes |
|-----------|--------|
| Logger daemon, SQLite schema | Pipeline is read-only on DB |
| Existing dashboard routers | Context is a separate router |
| Firmware | Out of scope for brain changes |
| Watchdog / health | Production safety |

---

## Risks (current)

| Risk | Mitigation |
|------|------------|
| Odroid RAM/CPU with LLM | Compression opt-in; monitor load; cap candidates per cycle (future) |
| **Dry-run** still runs compression if LLM is loaded | Documented in ops; behavior change is optional |
| Concurrent SQLite | Logger writes; pipeline reads — avoid pipeline writes to logger tables |
| `legacy_app.py` | New work uses modular dashboard app + routers; avoid unrelated edits |

---

## Doc map

| Doc | Purpose |
|-----|---------|
| [home-ai-core-ops.md](home-ai-core-ops.md) | Run daemon, env vars, `/context` API |
| [home-ai-core-plan.md](home-ai-core-plan.md) | Design philosophy, phased future work |
| [HERMES_MASTER.md](../HERMES_MASTER.md) | System architecture (Tier 1–3) |
| [hermes/linux/odroid/README.md](../hermes/linux/odroid/README.md) | systemd install for Odroid services |
