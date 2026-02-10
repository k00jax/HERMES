# HERMES

Multi-device sensing and logging stack for the HERMES project.

## Repos and Layout

- `hermes/` firmware, Odroid services, and tools
- `hermes-brain/` higher-level ingestion and retrieval

## Quick Pointers

- nRF firmware: `hermes/firmware/nrf`
- Odroid logger: `hermes/linux/logger`
- Flash tools: `hermes/tools/`

## Common Tasks

### Flash nRF (from PC)

```powershell
cd hermes/firmware/nrf
.\tools\flash-nrf.ps1
```

### Run Logger (on Odroid)

```bash
python3 ~/hermes-src/hermes/linux/logger/daemon.py
```

### Push OLED Context (on Odroid)

```bash
~/hermes-src/hermes/tools/push_oled_context.sh
```

## Docs

- Project docs: `hermes/docs/`
- HERMES brain docs: `hermes-brain/docs/`
