---

# POWER_MODEL.md

Version: 2.0
Status: Prototype Power Characterization
Last Updated: 2026-02-21

---

## 1. PURPOSE

This document defines:

* Current prototype power topology
* Known consumption assumptions
* Radar impact
* Risk areas
* Future wearable constraints

Power is a first-class constraint.

---

## 2. CURRENT POWER TOPOLOGY

### 3.3V Rail

Powered via:

* MCU 3.3V outputs (development board regulators)
* Breadboard rail distribution

Loads:

* nRF52840
* ESP32 logic
* SHT sensor
* SGP sensor
* Dual OLED displays
* 74HC595
* LEDs (via resistors)
* Passive buzzer (logic-level drive)
* Buttons

---

### 5V Rail

Source:

* ESP32 5V pin (USB-derived)

Load:

* LD2410B-P radar ONLY

Ground shared between rails.

---

## 3. ESTIMATED CURRENT DRAW (PROTOTYPE)

These are rough estimates and must be measured with inline meter for accuracy.

### nRF52840

* Idle: ~5–10 mA
* Active with I2C + LEDs: ~15–25 mA

### ESP32-S3

* Idle: ~20–40 mA
* With active UART + processing: ~40–80 mA

### LD2410B-P Radar

* Typical: ~60–90 mA
* Spikes possible during detection

This is the dominant continuous load.

### OLED (each)

* ~10–20 mA depending on brightness

### SHT + SGP

* Low single-digit mA each

### LEDs

* Depends on number and resistor values
* 2–10 mA per LED typical

### Passive Buzzer

* Negligible except during tone playback

---

## 4. PROTOTYPE TOTAL ESTIMATE

Without radar:
~60–120 mA

With radar active:
~130–200+ mA

This is USB-powered safe, but wearable hostile.

---

## 5. RADAR POWER STRATEGY

Current state:
Radar always powered when system powered.

Declared architecture:
Radar is NOT intended to be always-on in production.

Future strategies:

1. Duty cycling radar power
2. Enabling radar only during:

   * Field mode
   * Motion suspicion
   * Periodic scan windows
3. Hardware power switch via transistor or load switch
4. Software command to radar sleep mode if supported

Wearable viability requires radar idle strategy.

---

## 6. CAPACITOR STRATEGY

Currently deployed:

* 100µF electrolytic across 3.3V rail
* 100µF across 5V rail (recommended)
* 0.1µF ceramic near:

  * MCUs
  * Sensors
  * OLEDs
  * 74HC595

Purpose:

* Smooth transients
* Prevent UART corruption
* Reduce brownout risk

Future:
PCB-level placement required for stability.

---

## 7. WEARABLE TARGET CONSTRAINTS

For wearable viability:

Target idle:
<30 mA

Target peak:
<150 mA short bursts

Thermal envelope:
No sustained heat rise above safe skin threshold.

Battery:
If using 2000mAh Li-ion:
200 mA draw → ~10 hours theoretical
100 mA draw → ~20 hours theoretical
30 mA draw → ~60+ hours theoretical

Radar continuous use currently destroys these targets.

---

## 8. NEXT REQUIRED ACTIONS

Before enclosure design:

1. Measure real current draw:

   * Idle
   * Radar active
   * OLED brightness max
   * Report generation spike
2. Log duty cycle over 24h.
3. Determine radar usage percentage.
4. Evaluate radar gating method.

Power modeling must precede enclosure CAD.

---

## 9. RISK AREAS

* Radar 5V rail noise coupling into 3.3V logic
* Breadboard voltage drop under load
* LED simultaneous current draw
* ESP32 WiFi activation if enabled later
* USB hub current limits

---

## 10. GOVERNANCE RULE

Any new hardware addition must include:

* Idle draw estimate
* Peak draw estimate
* Thermal implication
* Battery implication

If this is not evaluated, the feature is incomplete.

---

End of document.

---