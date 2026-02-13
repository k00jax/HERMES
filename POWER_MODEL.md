# HERMES POWER MODEL
Version: 0.1
Status: Early Planning
Last Updated: 2026-02-13

---

## DESIGN GOAL

Create tiered energy consumption.

Low Power Idle → Sensor Only  
Medium Load → ESP32 Assist  
High Load → ODROID Active + LLM  

---

## ASSUMED POWER PROFILES

nRF:
Very low continuous draw

ESP32:
Moderate burst usage

ODROID:
High draw under compute load

---

## STRATEGY

1. nRF runs continuously
2. ESP32 only wakes when triggered
3. ODROID idles when no heavy processing needed
4. LLM inference scheduled, not constant

---

## FUTURE OPTIMIZATIONS

- SBC sleep state exploration
- Compute batching
- Thermal-aware throttling
- Adaptive sampling intervals

---

Power efficiency determines wearable viability.
This is a primary constraint, not a side feature.
