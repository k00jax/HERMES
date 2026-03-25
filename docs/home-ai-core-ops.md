# HERMES Home-AI Core — Operator Reference

*Branch: feature/home-ai-core*
*Schema version: 1*

---

## What the pipeline is

The home-AI pipeline runs inside `hermes-brain` as a background daemon.  It
reads sensor data from the existing SQLite database (written by the logger
daemon), groups it into time-windowed bundles, scores each bundle for
salience, applies a privacy filter, stores qualifying candidates locally, and
optionally delivers high-salience packets to a downstream cloud endpoint.

It does **not** replace any existing HERMES services.  It runs alongside
`hermes-logger` and `hermes-dashboard` without touching their data paths.

**Home mode:** This pipeline is intended for when the Odroid is **stationary and
powered** (dock, charger, shelf) — not optimized for on-wrist / bracer power
budgets.  See [HERMES_MASTER.md](../HERMES_MASTER.md) for the full tier model.

**systemd:** Install `hermes-brain.service` from
[hermes/linux/odroid/README.md](../hermes/linux/odroid/README.md) for always-on
operation on the Odroid.

---

## Running the daemon

### Normal mode (continuous loop)

```bash
cd ~/hermes-src/hermes-brain
python -m app.daemon
```

Runs until killed.  Polls the database every `HERMES_PIPELINE_INTERVAL_S`
seconds (default 60).  Writes a status file after every cycle.

### Single-cycle mode

```bash
python -m app.daemon --once
```

Runs one complete pipeline cycle then exits.  Useful for cron-based
scheduling, debugging, or first-run verification.

### Dry-run mode

```bash
python -m app.daemon --dry-run
```

Runs normalise → build → score → (optional LLM **compression** if enabled and a
`LocalLLM` is loaded) → privacy route, but **skips**:

- Writing candidates to the context store
- Sending escalation packets to the cloud endpoint

If `HERMES_COMPRESSION_ENABLED` is true and the model file exists, **compression
still runs** in dry-run (only store/deliver are skipped).  Turn compression off
to avoid LLM work during dry-run.

Logs what *would* have been stored and delivered.  Use this to verify
configuration before enabling writes.

### Combining flags

```bash
python -m app.daemon --once --dry-run
```

One cycle with no store/deliver.  Compression may still run if enabled (see
above).

---

## Configuration

All config values read from environment variables.  On a standard Odroid
install, defaults assume the logger database at `~/hermes-data/db/hermes.sqlite3`
and pipeline output under `~/hermes-data/` (same root the dashboard
`/context/*` routes read).  Override `HERMES_DATA_DIR` or `data_dir` in
`hermes-brain/config.yaml` if you want an isolated dev tree.

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_DB_PATH` | `~/hermes-data/db/hermes.sqlite3` | SQLite database path |
| `HERMES_DATA_DIR` | `~/hermes-data` | Root for all pipeline output |
| `HERMES_PIPELINE_INTERVAL_S` | `60` | Seconds between pipeline cycles |
| `HERMES_PIPELINE_WINDOW_MIN` | `5` | Minutes of sensor data per cycle |
| `HERMES_SALIENCE_THRESHOLD` | `0.0` | Min salience to store a candidate locally |
| `HERMES_ESCALATION_THRESHOLD` | `0.7` | Min salience to send a packet upstream |
| `HERMES_ESCALATION_ENDPOINT` | `""` | Cloud endpoint URL (empty = offline mode) |
| `HERMES_ESCALATION_DESTINATION` | `"default"` | Endpoint label stored in packets |
| `HERMES_PRIVACY_ALLOWLIST` | `ts_start,ts_end,source_mix,tags,salience,summary` | Fields permitted to leave HERMES |
| `HERMES_COMPRESSION_ENABLED` | `false` | When true, run optional LLM summarisation per candidate (requires model on disk) |
| `HERMES_MODEL_PATH` | `HERMES_DATA_DIR/models/model.gguf` (via `models_dir` in config) | Path to GGUF for `LocalLLM` |
| `HERMES_LLAMA_BIN` | `llama` | llama.cpp CLI binary on `PATH` |
| `HERMES_LOG_LEVEL` | `INFO` | Python logging level |

See [hermes-brain/app/config.py](../hermes-brain/app/config.py) for parsing.  You
can set `compression_enabled`, `model_path`, `llama_bin`, and other keys in
`hermes-brain/config.yaml` (or the file pointed to by `HERMES_CONFIG`); environment
variables take precedence over the YAML file.

---

## File outputs and paths

All paths are relative to `HERMES_DATA_DIR` (default `~/hermes-data`).

| Path | Written by | Contents |
|------|-----------|---------|
| `context/candidates_YYYY-MM-DD.jsonl` | daemon | MemoryCandidate objects, one JSON object per line |
| `escalation/queue_YYYY-MM-DD.jsonl` | daemon | EscalationPackets queued for retry |
| `omi_queue.jsonl` | dashboard `/context/ingest` | HomeEvents from Omi, drained by daemon each cycle |
| `pipeline_status.json` | daemon | Latest cycle status (atomically replaced) |

### Retention

| Store | Retention |
|-------|-----------|
| Candidate JSONL files | 7 days (configurable in `ContextStore(max_days=...)`) |
| Escalation queue files | 3 days |
| `pipeline_status.json` | Single file, replaced each cycle |

### Reading candidates manually

```bash
# Last 10 candidates
tail -n 10 ~/hermes-data/context/candidates_$(date -u +%Y-%m-%d).jsonl | python3 -m json.tool
```

```bash
# All candidates with salience >= 0.5
python3 -c "
import json, glob, sys
for f in sorted(glob.glob('$HOME/hermes-data/context/candidates_*.jsonl')):
    for line in open(f):
        d = json.loads(line)
        if (d.get('salience') or 0) >= 0.5:
            print(d['candidate_id'], d['ts_start'], d['salience'], d['tags'])
"
```

---

## Dashboard context endpoints

The dashboard exposes these endpoints under `/context`.  They are served by the
existing FastAPI app on port 8000 — the daemon is a **separate** process that
writes the files this API reads.

- `GET /context/status`
- `GET /context/candidates`
- `GET /context/packets`
- `POST /context/ingest`

### `GET /context/status`

Returns the last pipeline cycle status.

```json
{
  "daemon_status": "ok",
  "last_run": "2026-03-20T14:10:01.234567+00:00",
  "events_read": 287,
  "omi_events": 0,
  "candidates_built": 1,
  "candidates_stored": 1,
  "packets_queued": 1,
  "packets_delivered": 0,
  "duration_ms": 43,
  "error": null,
  "candidate_count_total": 47
}
```

`daemon_status` is `"not_started"` if the daemon has never run (status file
absent), `"ok"` if the last cycle completed without error, `"error"` if the
last cycle raised an exception.

### `GET /context/candidates`

Returns recent candidates, newest first.  Events within each candidate are
not included (use the JSONL files directly for full detail).

Query parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `20` | Maximum candidates to return (max 200) |
| `min_salience` | `0.0` | Only return candidates with salience ≥ this value |
| `tag` | — | If set, only return candidates with this tag |

```bash
curl 'http://localhost:8000/context/candidates?min_salience=0.5&limit=5'
curl 'http://localhost:8000/context/candidates?tag=presence_onset'
```

Example response:

```json
{
  "count": 2,
  "candidates": [
    {
      "candidate_id": "w300_5913521",
      "ts_start": "2026-03-20T14:05:00+00:00",
      "ts_end":   "2026-03-20T14:10:00+00:00",
      "salience": 0.8,
      "tags": ["presence_onset", "co2_elevated", "multi_source"],
      "source_mix": ["air", "env", "radar"],
      "summary": null,
      "escalate": true,
      "event_count": 287,
      "provenance": {
        "schema_version": "1",
        "pipeline_version": "0.1.0",
        "window_sec": 300
      }
    }
  ]
}
```

### `POST /context/ingest`

Accept an Omi memory blob or batch and queue it for the next daemon cycle.

Single blob:

```bash
curl -X POST http://localhost:8000/context/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "ts_utc": "2026-03-20T14:07:00+00:00",
    "kind": "memory",
    "text": "Kids arrived home from school",
    "meta": {"confidence": 0.9, "source": "omi_wearable"}
  }'
```

Batch:

```bash
curl -X POST http://localhost:8000/context/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "items": [
      {"ts_utc": "...", "kind": "memory", "text": "..."},
      {"ts_utc": "...", "kind": "memory", "text": "..."}
    ]
  }'
```

Response:

```json
{"queued": 1}
```

The daemon drains the queue on its next cycle (within `PIPELINE_INTERVAL_S`
seconds).  Every queued event receives:
- `_omi_batch_id` — UUID4 linking all items from the same POST
- `_omi_item_index` — 0-based position within the batch
- `_omi_payload_hash` — SHA-256 of the original blob (for dedup/verification)
- `_omi_received_at` — wall-clock time HERMES received the request

---

## Behavior when escalation endpoint is empty

When `HERMES_ESCALATION_ENDPOINT` is empty (the default), the pipeline runs
in **offline mode**:

- All pipeline stages run normally (normalise, build, score, filter).
- Candidates are stored locally in the candidate JSONL files.
- High-salience packets are constructed and privacy-filtered.
- Packets are logged at INFO level instead of being transmitted:
  ```
  cloud_client: [offline] packet=<id> salience=0.800 tags=['presence_onset', ...] summary=None
  ```
- Nothing is queued for retry (the queue is only used for failed deliveries
  when an endpoint is configured).

To verify offline mode is working:

```bash
python -m app.daemon --once --dry-run
# Look for "[offline]" lines in the output
```

To enable cloud escalation later, set the endpoint and restart:

```bash
export HERMES_ESCALATION_ENDPOINT="https://your-endpoint/ingest"
python -m app.daemon
```

The retry queue (`escalation/queue_*.jsonl`) will be flushed on startup.

---

## Candidate ID format

Candidate IDs are deterministic:

```
w{window_sec}_{bucket_index}
```

e.g. `w300_5913521` — a 5-minute (300 s) bucket.

The `bucket_index` is `int(unix_epoch // window_sec)`, aligned to UTC.
The same sensor data processed with the same `window_sec` always produces
the same candidate_id.  The context store uses this for deduplication —
re-running the daemon over an overlapping time window will not double-store
candidates.

If `window_sec` changes between daemon restarts, existing candidates are
unaffected (different prefix → different IDs → no false dedup).

---

## Privacy filter

The `HERMES_PRIVACY_ALLOWLIST` controls which fields are included in
escalation packets sent upstream.  Default:

```
ts_start,ts_end,source_mix,tags,salience,summary
```

Raw sensor readings (`temp_c`, `eco2_ppm`, `detect_cm`, etc.) are **not** in
the default allowlist.  They are stored locally in candidate files but do not
leave HERMES unless you explicitly add the field name to the allowlist.

Every EscalationPacket records both `allowed_fields` and `stripped_fields`.
`stripped_fields` is stored locally only — it is never transmitted upstream.

---

## Candidate salience tags

Tags are assigned by the candidate builder.  The scorer applies weights.

| Tag | What triggered it | Scorer weight |
|-----|------------------|---------------|
| `presence_onset` | Radar: target changed from 0 → nonzero | +0.40 |
| `presence_cleared` | Radar: target changed from nonzero → 0 | +0.30 |
| `co2_elevated` | Any CO2 reading > 1000 ppm | +0.25 |
| `co2_spike` | CO2 range within window > 300 ppm | +0.30 |
| `temp_drift` | Temp range within window > 1.5 °C | +0.15 |
| `multi_source` | 3+ distinct sensor sources in window | +0.10 |
| `omi_present` | At least one Omi event in the window | +0.20 |
| *(any tag)* | Baseline bonus for any tag at all | +0.05 |

Scores are summed and clamped to [0.0, 1.0].

A candidate with `presence_onset + co2_elevated + multi_source` scores
0.05 + 0.40 + 0.25 + 0.10 = **0.80** — above the default escalation
threshold of 0.70.

---

## Schema version

Current schema version: **1** (stored in `SCHEMA_VERSION` in `types.py` and
in every candidate's `provenance.schema_version`).

If the schema is ever changed, the version increments and `context_store.py`
must be updated to handle both old and new shapes when reading JSONL files.
