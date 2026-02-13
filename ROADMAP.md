# HERMES ROADMAP
Version: 0.1
Status: Directional, Editable
Last Updated: 2026-02-13

---

## PHASE 0 — FOUNDATION (CURRENT)

Objective: Stable embedded + Linux architecture.

Completed:
- nRF USB command path
- OLED page system
- Logger daemon (systemd)
- Structured + raw logs
- Context push automation
- Time fallback handling

In Progress:
- OLED stale-context guard
- Structured event categorization cleanup
- Robust error handling in daemon
- Camera freeze note: XIAO S3 Sense OV2640 SCCB remains nonresponsive (`i2c_found=0`, `sccb.endTransmission_rc=2`). Camera path is disabled by default (`ENABLE_CAMERA=0`) until alternate camera hardware is available.

---

## PHASE 1 — STABILITY HARDENING

Objective: Make system reliable under long runtime.

Targets:
- Watchdog timers (MCU + Linux)
- Serial reconnection logic
- Power brownout detection
- Log rotation verification
- Thermal monitoring

Exit Criteria:
- 7-day continuous runtime with no crash

---

## PHASE 2 — LOCAL INTELLIGENCE

Objective: Deploy lightweight offline LLM + RAG.

Targets:
- Quantized 7B model benchmarking
- Vector DB integration
- Context embedding pipeline
- Structured memory tagging

Exit Criteria:
- Local reasoning over logged environmental history

---

## PHASE 3 — INTERFACE EVOLUTION

Objective: Human interaction layer upgrade.

Targets:
- Gesture recognition via IMU
- BLE HID air-mouse mode
- Contextual notifications
- Minimalist survival display mode

---

## PHASE 4 — FIELD MODE

Objective: Survival-capable edge device.

Targets:
- Knowledge vault dataset
- Offline documentation archive
- Signal anomaly detection
- Energy optimized idle mode

---

## PHASE 5 — MINIATURIZATION

Objective: Refine wearable form factor.

Targets:
- Power system redesign
- Enclosure CAD
- Heat dissipation strategy
- Battery scaling

---

This roadmap is strategic, not fixed.
Phases may overlap or compress.
