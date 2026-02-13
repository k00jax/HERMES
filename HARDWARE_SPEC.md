# HERMES HARDWARE SPECIFICATION
Version: 0.1
Status: Baseline Hardware Reference
Last Updated: 2026-02-13

---

## CORE COMPUTE

### ODROID-M1S
- 8GB RAM
- Ubuntu Server 20.04
- 64GB onboard storage
- NVMe upgrade planned

Role:
Primary Linux compute node and AI host.

---

## MICROCONTROLLERS

### XIAO nRF52840 Sense
- BLE
- 6-axis IMU
- Low power profile
- Environmental sensor integration
- Primary always-on board

### XIAO ESP32-S3 Sense
- WiFi
- Camera support
- Audio processing
- Higher compute tasks

---

## COMMUNICATION LINKS

- UART between nRF and ESP32
- USB serial to ODROID
- BLE optional external interface

---

## DISPLAY

- OLED module
- Multi-page display system
- Context-driven updates

---

## POWER (CURRENT STATE)

- Battery ~3000mAh target
- USB powered during development
- No finalized power management board yet

---

## KNOWN HARDWARE GAPS

- Dedicated PMIC selection
- Battery protection circuit finalization
- Thermal modeling for SBC enclosure
- Rangefinder module integration (future)
