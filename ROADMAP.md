---

# ROADMAP.md

Version: 2.0
Status: Active Development Roadmap
Last Updated: 2026-02-21

---

## CURRENT SYSTEM STATE

HERMES is no longer a concept prototype.
It is now a functioning distributed sensing platform with:

* Dual-MCU architecture
* Radar presence detection via UART
* Environmental sensing
* SQLite persistence
* Multi-page dashboard
* Analytics
* Calibration workflow
* Report generation
* CSV export
* Settings persistence
* Field mode interface

The system is operational but not yet production hardened.

---

# PHASE COMPLETION HISTORY

## Phase 0 – Hardware Bring-up

Complete

* nRF + ESP32 operational
* I2C bus stable
* OLED functioning
* 74HC595 LED expansion working
* Passive buzzer integrated
* Three-button navigation functional

---

## Phase 1 – Radar Integration

Complete

* LD2410B-P wired via UART
* 5V isolated to radar
* Frame parsing validated
* Throttled emission implemented
* Alive detection implemented
* Serial1 production-only telemetry

---

## Phase 2 – Dashboard Architecture

Complete

* Multi-page routing
* Home, History, Events, Analytics, Calibration, Settings
* Field mode added
* Settings persistence
* Chart slot configuration
* RSSI signal strength border
* Diagnostics button placement corrected

---

## Phase 3 – Reports + Calibration + History

Complete

* HTML report generator with presets
* Range-bounded queries (max 31 days)
* Calibration workflow with 60s empty-room capture
* Baseline + noise computation
* Calibration persistence
* Event logging on calibration
* Buzzer chime on completion
* History explorer with CSV export
* Bounded query enforcement

---

# CURRENT PRIORITIES

## 1. Power Characterization (Critical)

Before enclosure design:

* Measure idle draw
* Measure radar active draw
* Measure OLED peak draw
* Determine duty-cycle model
* Define radar gating strategy

No enclosure until power is quantified.

---

## 2. Radar Duty-Cycle Strategy

Options to evaluate:

* Software sleep
* MOSFET load switching
* Scheduled sampling
* Motion-triggered activation
* Field-mode-only activation

Target: wearable-friendly idle state.

---

## 3. UI Refinement

* Improve report clarity (motion vs still overlap labeling)
* Improve calibration noise reliability
* Improve radar visualization (2D proximity model instead of fake directional radar)
* Improve graph readability and spacing
* Font and layout polish

---

## 4. Data Model Hardening

* Index high-volume tables
* Ensure radar table indexed by ts_utc
* Ensure events table indexed by ts_utc + severity
* Validate analytics query performance under load

---

## 5. Hardware Evolution Path

Stage 1:

* Stabilized breadboard layout

Stage 2:

* Perma-proto board

Stage 3:

* Custom PCB revision 1

Stage 4:

* Power-validated wearable enclosure

No PCB design until pin assignments freeze.

---

## 6. AI Layer Integration (Future)

LLM integration must:

* Be modular
* Be optional
* Not be required for base functionality
* Degrade gracefully

Potential uses:

* Event anomaly detection
* Trend summarization
* Field report narrative generation
* Context clustering

AI is enhancement, not dependency.

---

## 7. Manufacturing Mindset Transition

Before public release:

* Hardware revision control
* Firmware version tagging
* Configurable settings export/import
* Calibration export/import
* Self-diagnostics mode
* Factory reset procedure

Architecture drift must stop now.

---

# META PRINCIPLE

Design as if:

1,000 will replicate
100 will modify
10 will depend
1 will take it into the wilderness

This is no longer a hobby bench experiment.
It is a platform in formation.

---

End of document.

---