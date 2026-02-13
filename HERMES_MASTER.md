# HERMES MASTER REFERENCE
Version: 0.9  
Status: Living Architecture Document  
Owner: Kyle Fonger  
Last Updated: 2026-02-13  

---

## 1. CORE IDENTITY

**Project Name:** HERMES  
**Primary Form:** Wearable + Edge AI System  
**Philosophy:** Offline-first, context-aware, modular intelligence  
**Design Principle:** Distributed microcontrollers feeding a central reasoning core  

### Working Acronym Variants
- Heuristic Environmental Real-time Monitoring & Engagement System  
- Hybrid Edge Reasoning & Environmental Monitoring System  

The acronym may evolve. The name remains.

---

## 2. SYSTEM ARCHITECTURE OVERVIEW

HERMES is a three-tier system.

### Tier 1 – Sensor Layer
Ultra low power microcontrollers responsible for environmental sensing, gesture, mic input, and wake logic.

Primary MCU:
- Seeed Studio XIAO nRF52840 Sense

Secondary MCU:
- Seeed Studio XIAO ESP32-S3 Sense

---

### Tier 2 – Edge Compute Layer
Single Board Computer running Linux.

Primary SBC:
- ODROID-M1S (8GB RAM)
- Ubuntu Server 20.04
- 64GB onboard storage
- NVMe planned

---

### Tier 3 – AI / Memory Layer
Runs on ODROID.

Responsibilities:
- Sensor log ingestion
- Structured event storage
- Context modeling
- OLED state pushes
- Local automation
- Future LLM inference
- Future RAG system
- Planned local vector database

---

## 3. CURRENT WORKING STATE

### Operational

- nRF USB command path: `OLED,STATUS/STACK/PAGE/CONTEXT/TIME`
- ACK/NACK handling functional
- OLED page system working
- 60-minute deltas on Air Trends page
- Host time fallback via `OLED,TIME,epoch`
- Logger daemon running as systemd service
- Raw + structured logs
- Context push script via systemd timer
- Raw log retention policy

---

### Linux Components

- `hermesd` logger daemon
- `run_daemon.sh`
- systemd service configuration
- `push_oled_context.sh`
- SQLite logging tables

---

### Microcontroller Communications

- UART between ESP32 and nRF
- USB serial to ODROID

---

## 4. DESIGN PRINCIPLES

1. Offline First  
   System must function without internet.

2. Modular Replaceability  
   Any MCU can be replaced without rewriting architecture.

3. Low Power Always Listening  
   nRF handles ambient monitoring.

4. Event Escalation Model  
   Low power MCU escalates to ESP32 or ODROID only when needed.

5. Context Over Raw Data  
   Store meaning, not noise.

6. Survivability Mode  
   System should operate as a field intelligence device without network dependency.

---

## 5. ROLE OF EACH BOARD

### XIAO nRF52840
- Always on
- BLE capability
- IMU
- Environmental sensors
- Mic wake logic
- Heartbeats to host

---

### XIAO ESP32-S3 Sense
- Camera snapshots
- Higher bandwidth tasks
- WiFi operations
- Audio capture and processing
- Assists nRF when triggered

---

### ODROID-M1S
- Linux brain
- Storage
- AI inference
- Logging daemon
- Automation scripts
- Future RAG + LLM engine

---

## 6. DATA FLOW MODEL

Environment  
→ nRF sampling  
→ event classification  
→ UART to ESP32 (if needed)  
→ USB to ODROID  
→ hermesd logger  
→ SQLite tables  
→ context extraction  
→ OLED update  

Future Flow:
→ vector DB update  
→ local LLM reasoning  
→ proactive insight generation  

---

## 7. FUTURE EXPANSION TRACKS

### A. Local LLM Deployment
- Quantized 7B class models
- Designed for 8GB RAM
- Offline RAG

### B. Knowledge Vault
- Civilization reboot archive
- Compressed scientific corpus
- Manuals
- Engineering references

### C. Gesture Interface
- Air mouse via IMU
- BLE HID mode

### D. Survival Mode UI
- Minimal OLED display
- Environmental anomaly alerts
- Signal detection

### E. Power Optimization
- Battery scaling
- Duty cycling
- Thermal modeling

---

## 8. DEVELOPMENT ENVIRONMENT

### ODROID
- Ubuntu Server 20.04
- systemd services
- Python daemon
- SQLite

### MCUs
- Arduino + PlatformIO
- USB flashing
- Dedicated firmware per board

### Version Control Structure

Recommended repo layout:

/firmware/nrf
/firmware/esp32
/linux/logger
/tools
/docs

## 9. GOVERNANCE RULE

This document is mutable.

If architecture changes:
1. Identify affected section.
2. Revise explicitly.
3. Increment version number.
4. Update Last Updated date.

No silent modifications.

---

## 10. META OPERATING INSTRUCTIONS

When referencing HERMES in future sessions:

- Treat this document as architectural baseline.
- Confirm before altering core structure.
- Flag proposals that break design principles.
- Keep system layers conceptually separated.
- Update this document whenever architecture shifts.

---

## 11. PROJECT INTENT

HERMES is a distributed edge AI platform combining:

- Wearable computing
- Embedded systems
- Linux systems engineering
- Offline AI reasoning
- Context-aware logging
- Human-centered design
- Survival-grade redundancy

It is not a gadget.  
It is a cognition amplifier.

---

END OF DOCUMENT
