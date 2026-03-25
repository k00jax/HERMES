# HERMES Home AI Core — Development Plan

*Branch: main (post-merge)*
*Date: 2026-03-20*
*Status: Phase 1 slice implemented; roadmap active*

---

## Design Philosophy

HERMES is the **local embodied layer**, not the final mind.

Its job:
- Collect home context from sensors and external inputs.
- Normalize and shape events into structured form.
- Compress observations without over-committing to conclusions.
- Score salience — but preserve optionality.
- Package memory candidates with enough provenance to be useful later.
- Route high-value packets to stronger downstream reasoning.
- Do not pretend to understand everything locally.

The local model (if used) is a **triage and compression tool**, not an analyst.

---

## Phase 1 — Target Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Hardware Layer                     │
│  nRF52840 (sensors) ──UART──► ESP32 ──UART──► Odroid│
└──────────────────────────────────────────────┬──────┘
                                               │ serial
                              ┌────────────────▼───────────────┐
                              │    hermes-logger (daemon)       │
                              │    serial → SQLite (14 tables)  │
                              └────────────────┬───────────────┘
                                               │ read
              ┌────────────────────────────────▼──────────────────────┐
              │                  home-ai pipeline (hermes-brain)       │
              │                                                        │
              │  ┌─────────────┐   ┌────────────────┐                 │
              │  │  normalizer │──►│ candidate_builder│                │
              │  │  (SQLite→   │   │ (window events  │                │
              │  │  HomeEvent) │   │  into bundles)  │                │
              │  └─────────────┘   └───────┬─────────┘                │
              │                            │                           │
              │  ┌─────────────┐   ┌───────▼─────────┐                │
              │  │ omi_adapter │──►│ salience_scorer  │                │
              │  │ (ext input) │   │ (rule-based +    │                │
              │  └─────────────┘   │  optional LLM)  │                │
              │                    └───────┬─────────┘                │
              │                            │                           │
              │  ┌─────────────────────────▼────────────────────────┐ │
              │  │             privacy_router                        │ │
              │  │   field allowlist → strips PII before leaving    │ │
              │  └────────────┬──────────────────────┬──────────────┘ │
              │               │ local store           │ escalate       │
              │  ┌────────────▼──────────┐  ┌────────▼───────────┐   │
              │  │  context_store        │  │ cloud_escalation   │   │
              │  │  (SQLite/JSONL)       │  │ client (async HTTP) │   │
              │  └────────────────────┬──┘  └────────────────────┘   │
              └───────────────────────┼───────────────────────────────┘
                                      │
              ┌───────────────────────▼───────────────────────────┐
              │          FastAPI dashboard (:8000)                 │
              │    /context/* routes (new, isolated router)        │
              │    existing routes unchanged                       │
              └────────────────────────────────────────────────────┘
```

---

## Phase 2 — Module Definitions

### Core Data Types

```python
# hermes-brain/app/pipeline/types.py

@dataclass
class HomeEvent:
    """Normalized single sensor observation."""
    ts_utc: str           # ISO 8601
    source: str           # "radar" | "env" | "air" | "light" | "mic" | "omi" | "system"
    kind: str             # "presence" | "temperature" | "co2" | "memory" | ...
    value: dict           # raw payload, no interpretation
    raw_ref: str | None   # reference to raw origin (table + row id), don't discard

@dataclass
class MemoryCandidate:
    """Grouped bundle of events that may be worth remembering."""
    candidate_id: str      # UUID
    ts_start: str          # window start
    ts_end: str            # window end
    events: list[HomeEvent]
    summary: str | None    # optional compressed text (compressor output)
    salience: float        # 0.0–1.0
    tags: list[str]        # ["presence", "air_quality_anomaly", ...]
    source_mix: list[str]  # which sources contributed
    escalate: bool         # whether to send upstream
    provenance: dict       # metadata for downstream trust assessment

@dataclass
class EscalationPacket:
    """Privacy-filtered bundle ready to send upstream."""
    packet_id: str
    created_at: str
    candidate_id: str
    summary: str
    tags: list[str]
    salience: float
    allowed_fields: list[str]  # what was permitted through privacy filter
    destination: str            # endpoint name
```

### Modules (Build Order)

#### 1. `hermes-brain/app/pipeline/types.py`
Core dataclasses. No logic. Built first.

#### 2. `hermes-brain/app/pipeline/normalizer.py`
Reads from SQLite (direct connection, read-only). Queries last N minutes of each sensor table. Emits `HomeEvent` list. No inference. Preserves `raw_ref`.

#### 3. `hermes-brain/app/pipeline/candidate_builder.py`
Groups `HomeEvent` items into time windows (configurable, default 5 min). Produces `MemoryCandidate` bundles. Detects notable transitions (presence onset, CO2 spike, temp drift). Does NOT conclude — flags patterns as candidates.

#### 4. `hermes-brain/app/pipeline/salience_scorer.py`
Scores `MemoryCandidate` items using rules first:
- Presence transitions → +0.4
- CO2 > threshold → +0.3
- Unusual time-of-day → +0.2
- Multiple simultaneous anomalies → +0.2
LLM scoring is optional and gated (only if model available and salience is ambiguous).

#### 5. `hermes-brain/app/ingest/omi_adapter.py`
Accepts Omi-exported blobs (JSON or text) via HTTP POST or file drop. Wraps as `HomeEvent(source="omi", kind="memory")`. Tags with Omi provenance. Injects into same pipeline as sensor events. Isolated — cannot contaminate sensor truth.

#### 6. `hermes-brain/app/pipeline/privacy_router.py`
Before any escalation: apply field allowlist. In v1, allowlist is: `[ts_utc, source, kind, tags, salience, summary]`. Strip `value` (raw sensor data) unless explicitly permitted. Log what was stripped.

#### 7. `hermes-brain/app/escalation/cloud_client.py`
Async HTTP POST of `EscalationPacket` to configurable endpoint. Queue locally if offline. Retry with backoff. Never block the pipeline.

#### 8. `hermes-brain/app/pipeline/context_store.py`
Persist `MemoryCandidate` items locally (JSONL, same pattern as `event_store.py`). Expose query interface (last N, by tag, by salience). This becomes the local memory.

#### 9. `hermes-brain/app/daemon.py`
Main pipeline loop. Run on configurable interval (default 60 s). Calls normalizer → candidate builder → salience scorer → privacy router → escalation client + local store.

#### 10. Dashboard — `/context` router
New FastAPI router at `hermes/linux/odroid/services/dashboard/dashboard/routes/context.py`.
Endpoints:
- `GET /context/status` — pipeline health, last run ts, candidate count
- `GET /context/candidates` — recent memory candidates (last N)
- `GET /context/packets` — escalated packets (last N)
- `POST /context/ingest` — accept external context (Omi adapter entry point)

---

## Phase 3 — First Build Slice (This Branch)

The minimal useful system that can actually run on the Odroid and produce observable output.

### Scope

| In Scope | Out of Scope |
|----------|-------------|
| Pipeline types (`HomeEvent`, `MemoryCandidate`, `EscalationPacket`) | Dynamic skill loading |
| Normalizer (SQLite → HomeEvent) | Embedding-based salience |
| Candidate builder (5 min windows) | Full agent orchestration |
| Rule-based salience scorer | Multi-zone presence tracking |
| Context store (JSONL) | LLM-based compression (stub only) |
| Omi ingestion (HTTP POST endpoint) | Camera integration |
| Privacy router (field allowlist) | Production cloud endpoint |
| Cloud escalation client (stub, configurable) | Persistent memory retrieval |
| Dashboard `/context` router (status + candidates list) | Fine-grained retention policies |
| Daemon process (60 s loop) | Self-modifying behavior |

### Files to Create

```
hermes-brain/app/pipeline/
├── __init__.py
├── types.py              # HomeEvent, MemoryCandidate, EscalationPacket
├── normalizer.py         # SQLite → HomeEvent
├── candidate_builder.py  # Events → MemoryCandidate
├── salience_scorer.py    # Rule-based scoring
├── privacy_router.py     # Field allowlist filter
└── context_store.py      # JSONL persistence + query

hermes-brain/app/escalation/
├── __init__.py
└── cloud_client.py       # Async POST + local queue

hermes-brain/app/ingest/omi_adapter.py   # Omi HTTP/file ingestion

hermes-brain/app/daemon.py               # Pipeline loop

hermes/linux/odroid/services/dashboard/dashboard/routes/context.py  # /context/* API
```

### Files to Modify (Minimally)

```
hermes-brain/app/config.py                          # Add pipeline config keys
hermes-brain/app/ingest/event_store.py             # Add structured dict output
hermes/linux/odroid/services/dashboard/dashboard/app.py  # Register /context router
```

### Files NOT to Touch

```
hermes/linux/logger/                    # Logger daemon — leave alone
hermes/linux/odroid/services/dashboard/legacy_app.py  # Leave alone
hermes/firmware/                        # All firmware — leave alone
hermes/linux/odroid/systemd/            # Existing systemd units — leave alone
All existing dashboard routes (home, history, analytics, etc.)
```

---

## Configuration Keys to Add

In `hermes-brain/app/config.py`:

```
HERMES_PIPELINE_INTERVAL_S = 60          # How often pipeline runs
HERMES_PIPELINE_WINDOW_MIN = 5           # Event grouping window (minutes)
HERMES_SALIENCE_THRESHOLD = 0.4          # Min salience to store candidate
HERMES_ESCALATION_THRESHOLD = 0.7        # Min salience to escalate
HERMES_ESCALATION_ENDPOINT = ""          # Cloud endpoint URL (empty = disabled)
HERMES_PRIVACY_ALLOWLIST = "ts_utc,source,kind,tags,salience,summary"
HERMES_OMI_ENABLED = false               # Enable Omi adapter
HERMES_OMI_PORT = 8001                   # Omi ingestion HTTP port
HERMES_CONTEXT_STORE_PATH = "data/context/candidates.jsonl"
```

---

## Behavior Rules for the Pipeline

1. **Never throw away raw_ref** — always preserve a pointer back to the source row.
2. **Never conclude, only flag** — salience scorer emits a score, not a label like "family present".
3. **Always tag source_mix** — downstream must know which sensors contributed.
4. **Privacy filter before any egress** — even to local disk if disk is shared.
5. **LLM is optional** — pipeline must run fully without a model loaded.
6. **Log what gets dropped** — if a candidate is below threshold and not stored, log why.
7. **Omi input is always tagged** — never mix with sensor truth silently.
8. **Cloud escalation is async** — never block the pipeline on network.
9. **Fail loudly on config errors** — missing required config should crash fast, not silently degrade.
10. **Preserve ambiguity** — if two interpretations exist, include both in tags, don't pick one.

---

## Definition of Done — First Slice

- [x] Pipeline types defined and importable
- [x] Normalizer reads from SQLite, returns `HomeEvent` list
- [x] Candidate builder groups into 5 min windows, detects presence transitions + CO2 spikes
- [x] Salience scorer assigns score using rules (no LLM required)
- [x] Context store persists candidates to JSONL
- [x] Privacy router applies field allowlist before any egress
- [x] Cloud client stubs (POST to endpoint, log-only if no endpoint configured)
- [x] Omi adapter accepts POST via dashboard ingestion route and queue adapter
- [x] Daemon runs pipeline loop on configurable interval
- [x] `/context/status` and `/context/candidates` endpoints live in dashboard
- [x] All new code has unit tests covering normalizer, builder, scorer, router
- [x] Daemon survives being started with no sensor data (empty tables)
- [x] Daemon survives being started with no model loaded
- [x] Core pipeline config values are defined in `config.py` and surfaced via env/YAML

---

## Future Phases

- **Phase 2 (harden)**: Improve daemon/operator controls and reliability under long-running load.
- **Phase 3 (enrich)**: Retrieval integration over stored candidates before escalation.
- **Phase 4 (expand)**: Multi-zone home context and richer home-state abstractions.
- **Phase 5 (modularize)**: Skill-style, pluggable modules that consume context packets.
- **Phase 6 (federate)**: Family AI integration where HERMES acts as a local cognition node.
