# Bench Setup Checklist

## Wiring
- Power ESP32 separately from nRF; share GND.
- UART:
  - ESP32 D6 (TX) -> nRF D7 (RX)
  - ESP32 D7 (RX) -> nRF D6 (TX)
- LED:
  - nRF D1 -> series resistor (220-1000 ohm) -> LED -> GND
- Mouse microswitch:
  - COM -> GND
  - NO -> nRF D0 (internal pullup enabled)

## Behavior Checklist
- LED:
  - Fast blink on boot or when no valid SENS frames (age > 1500ms or linesOK == 0).
  - Solid ON when link is healthy (age <= 1500ms and no recent parse errors).
  - Slow blink when SENS frames are flowing but RSSI is not connected (999).
  - Double pulse every 2 seconds if parse errors increase.
- Button:
  - Short press cycles display modes (Default -> Link Debug -> Env Big -> Default).
  - Double press forces immediate redraw and logs "BTN: refresh now" to USB serial.
  - Long press (>800ms) toggles focus mode and logs "BTN: focus ON/OFF".
- Focus mode:
  - OLED refresh rate drops to 1 Hz for 5 minutes, then returns to normal.

## Quick Test Flow
1. Power ESP32 and nRF with common GND.
2. Confirm LED fast blinks during boot, then settles based on link state.
3. Short press to cycle display modes.
4. Double press to force redraw and USB log line.
5. Long press to toggle focus mode and observe slower OLED updates.
