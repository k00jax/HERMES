# XIAO Serial Protocol (HERMES)

## Overview
- Line-based ASCII protocol over USB serial.
- Each message is a single line terminated by `\n`.
- ODROID sends commands with prefix `CMD`.
- XIAO responds with `ACK` or `ERR` and the original command verb.
- Telemetry lines remain unchanged and are treated as sensor output.

## Commands (ODROID → XIAO)
```
CMD PING
CMD LED GREEN
CMD LED RED
CMD LED OFF
CMD VIBE SHORT
CMD VIBE LONG
CMD OLED CLEAR
CMD OLED TEXT|Air quality bad
CMD MODE SENTINEL
CMD MODE SCAN
```

## Responses (XIAO → ODROID)
- Acknowledge success:
```
ACK LED GREEN
ACK VIBE SHORT
ACK MODE SENTINEL
ACK PING
```

- Report errors:
```
ERR OLED TEXT|too_long
ERR MODE unknown
```

## OLED Notes
- Recommended maximum text length: 64 characters.
- If text is too long, respond with `ERR OLED TEXT|too_long`.

## Robustness
- Ignore unknown commands and respond with `ERR <cmd>`.
- After reconnects, send telemetry lines as usual.
- Tolerate partial lines: only process complete lines ending in `\n`.
