# HERMES Repo Assessment — Home AI Core

*Branch: feature/home-ai-core*
*Date: 2026-03-20*

---

## What HERMES Currently Is

HERMES is a **three-tier, offline-first local sensing and observation platform** running on an Odroid M1S. It is not yet an AI system — it is a well-instrumented sensor substrate with a nascent reasoning layer (`hermes-brain`) that has been deliberately kept lightweight.

### Tier 1 — Embedded MCUs

| MCU | Role |
|-----|------|
| **nRF52840** | I2C sensor bus host (SHT31 temp/humidity, SGP30 air quality), OLED display (2×128×64), buttons (3), buzzer, LEDs |
| **ESP32-S3** | LD2410B-P mmWave radar parsing, throttled telemetry (≤10 Hz), optional WiFi |

Protocol: UART 115200 baud, ASCII line-framed KV pairs (`PREFIX,key=val,...`). Frame types: `RADAR`, `HB`, `ENV`, `AIR`, `LIGHT`, `MIC`, `BTN`, `ACK`, `ESP`, `LOG`, `EVT`.

### Tier 2 — Edge Compute (Odroid, always-on)

| Service | Mechanism | Role |
|---------|-----------|------|
| `hermes-logger` | Python systemd daemon | Serial → SQLite + raw JSONL logs |
| `hermes-dashboard` | FastAPI on :8000 | REST API + HTML UI |
| `hermes-events-emitter` | systemd timer (10 s) | Anomaly detection (stale, recovery, reboot) |
| `hermes-oled-context` | systemd timer | Push delta context to nRF OLED |
| `hermes-dashboard-watchdog` | systemd timer (10 s) | Health check + auto-restart |

**Database**: SQLite at `~/hermes-data/db/hermes.sqlite3` — 14 tables covering raw lines, heartbeats, env, air, light, mic, radar, calibration, events, settings, reports, OLED status, acks.

**API surface**: ~40 REST endpoints covering health, time-series reads, event CRUD, analytics, reports, calibration, settings, history/export.

### Tier 3 — Reasoning Layer (hermes-brain, partial)

| Module | What it does |
|--------|-------------|
| `app/ingest/event_store.py` | JSONL event persistence + min/max/avg/count summarization |
| `app/ingest/serial_ingest.py` | Serial listener → events.jsonl |
| `app/retrieval/local_index.py` | TF-IDF index over `knowledge/*.md` |
| `app/retrieval/local_retriever.py` | TF-IDF chunk scoring (threshold 0.15, top-5) |
| `app/llm/local_llm.py` | llama.cpp subprocess, 2048 ctx, 4 threads, graceful fallback |
| `app/ingest/xiao_link.py` | Bidirectional CMD/ACK serial control (LED, OLED, vibe) |
| `app/main.py` | CLI: Q&A, serial ingest, XIAO control |

`hermes-brain` is **not integrated with the dashboard**. It is a standalone CLI tool that runs independently.

### Sensors Available (Live)

| Sensor | Fields | Rate |
|--------|--------|------|
| LD2410B mmWave radar | `target`, `move_cm`, `stat_cm`, `detect_cm` | ≤10 Hz |
| SHT31 | `temp_c`, `hum_pct` | 1 Hz |
| SGP30 | `eco2_ppm`, `tvoc_ppb` | 1 Hz |
| Light (LDR/phototransistor) | `light_level` | 1 Hz |
| Microphone | `mic_level` | 1 Hz |
| Heartbeat | `tick_ms`, `seq` | 1 Hz |

### What Is NOT Present Yet

- No home-state abstraction (devices, presence zones, occupancy models)
- No event normalization layer (raw telemetry goes direct to SQLite)
- No context packaging or structured output for downstream reasoning
- No privacy filtering or routing layer
- No cloud escalation interface
- No Omi memory ingestion
- No salience scoring
- No memory candidate pipeline
- Camera routes exist as stubs only (`/flip`, `/camera/stream`)
- No skill/module registry

---

## Reuse / Adapt / Replace / Leave Alone

### REUSE AS-IS

| Component | Why |
|-----------|-----|
| Logger daemon (`hermes/linux/logger/daemon.py`) | Stable serial ingest to SQLite. Clean separation. |
| SQLite database + schema | Well-structured, indexed, all sensor types covered. |
| FastAPI app factory + router structure | Clean, modular, already has 10 routers. |
| systemd service infrastructure | Production-grade daemon management. |
| `events` table | Already a first-class concept — extend, don't replace. |
| Health/metrics endpoints | `/healthz`, `/readyz`, `/metrics` — keep exactly as-is. |
| Firmware (nRF + ESP32) | Hardware stable, protocol proven. |
| `hermes-brain/app/ingest/event_store.py` | JSONL persistence + summarization — directly reusable. |
| `hermes-brain/app/retrieval/` (TF-IDF index + retriever) | Lightweight local retrieval — right weight class for Odroid. |
| `hermes-brain/app/llm/local_llm.py` | Correct approach (subprocess, graceful fallback). |

### ADAPT

| Component | Adaptation Needed |
|-----------|-------------------|
| `hermes-brain` overall | Currently CLI-only. Needs a persistent process mode with an internal loop (not just a one-shot Q&A runner). |
| `events` table + emitter | Currently only detects stale/recovery/reboot. Adapt to emit richer structured context events from the new pipeline. |
| `event_store.py` summarization | Currently a flat string. Should produce structured dict output (JSON-serializable) for downstream packaging. |
| FastAPI routes | Add a new `context` router for context packet ingestion, query, and status without breaking existing routes. |
| `hermes-brain/app/config.py` | Extend to include context pipeline config (salience thresholds, cloud endpoint, Omi port, etc.). |
| OLED context pusher | Could surface context packet status / memory candidate count on OLED display. |
| Local LLM | Currently used for Q&A. Adapt role to triage/compression compressor (classify salience, condense observations). |

### REPLACE

| Component | Reason |
|-----------|--------|
| `app/llm/prompt_templates.py` | Currently a generic Q&A prompt. Replace with role-specific prompts: context compression, salience scoring, escalation triage. |
| `hermes-brain/app/main.py` (CLI entry) | Keep CLI for dev/debug, but the main runtime should become a daemon, not a CLI. |

### LEAVE ALONE

| Component | Why |
|-----------|-----|
| All firmware (`hermes/firmware/`) | Stable, not in scope for AI layer. |
| All existing dashboard routes (home, history, analytics, reports, calibration, settings, field) | Don't break what works. New routes go in a new router. |
| `legacy_app.py` | Being refactored separately; don't touch during this branch. |
| Raw log files / retention logic | Infrastructure concern, leave as-is. |
| `hermes-brain/app/retrieval/` | Already the right approach — lightweight TF-IDF. |
| Watchdog / health system | Production safety critical — leave alone. |

---

## Insertion Points for New Modules

### 1. Local Context Compressor

**Where**: `hermes-brain/app/context/compressor.py` (new)

**Feeds from**: SQLite (read directly via `hermes/linux/odroid/services/dashboard/dashboard/db/`) or via dashboard REST API (`/api/latest/{table}`, `/api/ts/{series}`).

**Design**: Reads recent sensor windows (e.g., last 5 min), applies salience rules, invokes local LLM only when worth compressing (batch, not per-event). Outputs structured `ContextPacket`.

### 2. Memory/Event Pipeline

**Where**: `hermes-brain/app/pipeline/` (new directory)

**Composition**:
- `normalizer.py` — ingests raw SQLite rows, emits normalized `HomeEvent` dicts
- `candidate_builder.py` — groups events into `MemoryCandidate` bundles
- `salience_scorer.py` — scores bundles (rule-based first, LLM optionally)

**DB integration**: Read from existing SQLite (already available) via the dashboard's DB layer or direct SQLite queries. No new DB needed initially.

### 3. Omi Ingestion Adapter

**Where**: `hermes-brain/app/ingest/omi_adapter.py` (new)

**Design**: Simple HTTP listener or file watcher that accepts Omi-exported memory blobs, wraps them as `MemoryCandidate` items, and injects them into the same pipeline as local sensor events. Must be clearly isolated — Omi context is an _input_, not trusted ground truth.

### 4. Cloud Escalation Interface

**Where**: `hermes-brain/app/escalation/` (new)

**Design**: Takes `MemoryCandidate` bundles above salience threshold → packages into `EscalationPacket` → POSTs to configurable cloud endpoint. Must be async, non-blocking. Should fail gracefully (queue if offline). Privacy filter runs before any packet leaves.

### 5. Future Skill/Module Registry

**Where**: `hermes-brain/app/skills/` (stub only, not built yet)

**Design**: Simple dict mapping skill names to callables. No dynamic loading in v1. Leave as a placeholder directory with a `README.md` describing the interface contract.

---

## Key Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| `legacy_app.py` (~9400 lines) is the real implementation; refactor incomplete | Breaking dashboard accidentally | New AI routes go in a *separate router only*. Never edit `legacy_app.py` in this branch. |
| Odroid M1S — 8 GB RAM ceiling | OOM if local LLM + pipeline run simultaneously | Run LLM only on batch (not per-event). Set hard memory limits on llama.cpp. Don't use embedding models. |
| `hermes-brain` is not a daemon yet | Pipeline needs an always-on loop | Create minimal daemon mode with loop + sleep. Don't over-architect. |
| SQLite concurrent writes | Logger + pipeline both writing | Pipeline is read-only from SQLite (reads only existing data). Logger owns writes. No contention. |
| Salience thresholds are unknowns | May flood or starve cloud escalation | Start conservative (high threshold). Make threshold a config value, not a constant. |
| Privacy boundary is not defined | Sensitive home data could leak to cloud | Define privacy filter before escalation client. Even a simple field-allowlist is sufficient in v1. |
| Omi data provenance is unclear | Contaminating local context | Keep Omi-derived candidates tagged with `source: "omi"`. Never merge with sensor observations without explicit tagging. |

---

## Questions Answered

**Where does home state already enter the system?**
Via the logger daemon writing to SQLite. Home state is implicitly the union of `radar`, `env`, `air`, `light`, `mic` tables — but there is no explicit "home state" abstraction.

**How are sensor events represented?**
As raw SQLite rows (typed per-sensor tables). No normalized event schema exists yet.

**What existing services can host context compression?**
`hermes-brain` is the right home. It already has event summarization in `event_store.py`. Extend that module rather than creating parallel infrastructure.

**Is there already a dashboard/API surface for context packets?**
No. The dashboard exposes sensor time series and anomaly events. Context packets are a new concept. A new FastAPI router (`/context`) should be added.

**What existing abstractions can be extended?**
1. `events` table — extend to accept AI-generated context events.
2. `event_store.py` — extend summarization to produce structured JSON output.
3. `local_llm.py` — extend to support compression/triage prompts.
4. FastAPI router structure — add new router without breaking existing ones.

**What parts of HERMES are stable enough to become substrate?**
- Logger daemon (serial ingest to SQLite) — fully stable.
- SQLite schema (14 tables, all indexed) — fully stable.
- FastAPI app factory + router pattern — stable.
- systemd service infrastructure — stable.
- nRF/ESP32 firmware + protocol — stable.
