---

# HARDWARE_SPEC.md

Version: 2.0
Status: Active Prototype Configuration
Last Updated: 2026-02-21

---

## 1. SYSTEM OVERVIEW

HERMES hardware is currently composed of:

* nRF52840 (Primary Interface MCU)
* ESP32-S3 (Radar + telemetry MCU)
* Odroid M1S (Edge compute)
* LD2410B-P 24GHz radar sensor
* SHT temperature/humidity sensor
* SGP ECO2 air quality sensor
* Dual OLED displays
* 74HC595 shift register
* RGB LED
* Multiple discrete LEDs
* Passive buzzer
* Three-button navigation interface
* Breadboard power rails
* Decoupling capacitors

This document reflects real wiring, not theoretical design.

---

## 2. POWER ARCHITECTURE

### Rails

* 3.3V rail:

  * nRF52840
  * ESP32 logic
  * SHT
  * SGP
  * OLEDs
  * 74HC595
  * LEDs
  * Buttons
  * Buzzer (logic drive)

* 5V rail:

  * LD2410B-P radar ONLY

Ground is shared across entire system.

---

### Decoupling Strategy

Across main rails:

* 100µF electrolytic capacitor across 3.3V and GND
* 100µF electrolytic across 5V and GND (if separate feed)

Near modules:

* 0.1µF ceramic (104) at:

  * nRF 3V3/GND
  * ESP32 3V3/GND
  * SHT VIN/GND
  * SGP VIN/GND
  * Each OLED VCC/GND
  * 74HC595 VCC/GND

Purpose:

* Suppress transient dips
* Reduce digital switching noise
* Stabilize radar UART integrity

---

## 3. nRF52840 PIN MAPPING

### I2C Bus

SDA: D4
SCL: D5

Connected:

* SHT
* SGP
* OLED #1
* OLED #2

All devices share bus.

---

### Buttons (Navigation)

D1 → Previous
D2 → Select
D3 → Next

Wired:

* One side to pin
* Other side to GND
* Firmware uses INPUT_PULLUP

Slide switch removed. Any prior slide function must be implemented via button combination.

---

### Passive Buzzer

Pin: D0

Type: Passive buzzer
Purpose:

* UI feedback
* Calibration complete melody
* System alerts

Driven by tone generation in firmware.

---

### Shift Register (74HC595)

Used to expand digital outputs.

Pin orientation:
Notch left.
Bottom row left→right = 1–8
Top row right→left = 9–16

Connections:

Pin 16 (VCC) → 3.3V
Pin 8 (GND) → GND
Pin 14 (DS / SER) → nRF data pin
Pin 11 (SH_CP / SRCLK) → nRF clock pin
Pin 12 (ST_CP / RCLK) → nRF latch pin
Pin 13 (OE) → GND
Pin 10 (MR) → 3.3V

Outputs:
QA–QH → LED resistors → LEDs → GND

---

### RGB LED

Type: Common cathode (verified by test)

Common cathode → GND
R/G/B pins → 74HC595 outputs (with resistors)

Used for:

* Boot strobe cycle
* Status color modes

---

### Discrete LEDs

Colors:

* Red
* Green
* Blue
* Yellow
* Clear

All controlled via 74HC595 through current-limiting resistors.

The “uh-oh” red LED is now handled via shift register, not direct MCU pin.

---

## 4. ESP32-S3 PIN MAPPING

### UART to nRF

Cross-wired:

ESP32 TX → nRF RX
ESP32 RX → nRF TX

Used for inter-MCU communication.

---

### LD2410B-P Radar

Pinout (as oriented during wiring):

OUT
TX
RX
GND
VCC

Wiring:

LD2410 VCC → ESP32 5V
LD2410 GND → GND
LD2410 TX → ESP32 RX
LD2410 RX → ESP32 TX

OUT pin unused.

Radar communicates over UART only.

---

## 5. LD2410B-P INTEGRATION NOTES

* Operates at 5V supply
* UART used for full frame decoding
* OUT pin intentionally not used
* Emission throttled in firmware
* Not architected for permanent always-on duty yet

Future requirement:
Radar power gating or duty cycling for wearable use.

---

## 6. ODROID CONNECTIONS

* Both MCUs connected via USB hub
* Serial devices exposed as:

  * /dev/hermes-esp
  * /dev/hermes-nrf

Odroid runs:

* Ingest daemon
* Dashboard service
* SQLite storage

---

## 7. CURRENT PHYSICAL STATE

System currently built on breadboard.

Known limitations:

* Mechanical button looseness
* Wire density increasing
* Not enclosure-ready
* Not shock-resistant
* No PCB yet

This is still prototype stage hardware.

---

## 8. NEXT HARDWARE EVOLUTION

Planned progression:

1. Stabilized breadboard layout
2. Perma-proto board
3. Custom PCB
4. Power modeling validation
5. Enclosure thermal validation
6. Radar duty-cycle control
7. Wearable form factor revision

No PCB should be designed until pin assignments are considered stable.

---

## 9. HARDWARE GOVERNANCE

Before adding new hardware:

Ask:

* Can firmware achieve the same outcome?
* Does this increase signal quality?
* Does it justify BOM cost?
* Does it scale to production?
* What does this do to idle current?

Prototype-friendly is not production-ready.

---

End of document.

---