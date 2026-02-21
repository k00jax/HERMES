# nRF52840 Firmware

nRF52840 firmware must be built on PC.
UF2 is generated via post-build script.
Do not attempt to build this project on Odroid.

## One-command flash (PC -> Odroid)

From PowerShell in `firmware/nrf`:

```powershell
.\tools\flash-nrf.ps1
```

What it does:

1. Builds the nRF firmware and generates `firmware.uf2`
2. Copies the UF2 to the Odroid at `~/incoming/firmware.uf2`
3. Waits for the UF2 bootloader to appear (double-tap reset on the XIAO)
4. Triggers flashing on the Odroid

If your Odroid IP changes:

```powershell
.\tools\flash-nrf.ps1 -ODROID_HOST "odroid@10.0.0.80"
```

## OLED Command Protocol (USB CDC)

Commands arrive over the USB CDC device (Odroid `/dev/hermes-nrf`) and responses are emitted on the same stream.

Supported command prefixes:

- `OLED,...` for UI/status commands
- `BUZZER,...` for passive buzzer control

Supported commands:

- `OLED,STATUS` -> `ACK,kind=OLED,op=STATUS`
- `OLED,STACK,USER` -> toggles to user stack, `ACK,kind=OLED,op=STACK`
- `OLED,STACK,DEBUG` -> toggles to debug stack, `ACK,kind=OLED,op=STACK`
- `OLED,PAGE,NEXT` -> next page, `ACK,kind=OLED,op=PAGE`
- `OLED,PAGE,PREV` -> previous page, `ACK,kind=OLED,op=PAGE`
- `OLED,ALERT,STALE,ON` -> enables D3 stale alert pattern (3 quick flashes, 3s off), `ACK,kind=OLED,op=ALERT`
- `OLED,ALERT,STALE,OFF` -> disables stale alert pattern, `ACK,kind=OLED,op=ALERT`

- `BUZZER,BEEP,<dur_ms>` -> short 2kHz beep, `ACK,kind=BUZZER,op=BEEP`
- `BUZZER,JINGLE,cal_done` -> calibration-complete jingle, `ACK,kind=BUZZER,op=JINGLE`
- `BUZZER,JINGLE,<name>` -> fallback jingle, `ACK,kind=BUZZER,op=JINGLE`

Errors:

- Unknown or malformed OLED commands -> `NACK,kind=OLED,op=<OP>,reason=unknown_cmd`
- Unknown or malformed BUZZER commands -> `NACK,kind=BUZZER,op=<OP>,reason=unknown_cmd`
- RX buffer overflow -> `NACK,kind=OLED,reason=overflow`

## Input/Feedback Pin Roles

- D0: passive buzzer
- D1: left button
- D2: select/cycle button
- D3: right button
