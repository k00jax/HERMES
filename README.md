<img src="docs/assets/hermes-logo-h.jpg" alt="HERMES H Logo" width="140" />

# HERMES

Multi-device sensing and logging stack for the HERMES project.

## Repos and Layout

- `hermes/` firmware, Odroid services, and tools
- `hermes-brain/` higher-level ingestion and retrieval

## Git workflow

**`main` is the only long-lived branch.** Use short-lived topic branches for work, open pull requests into `main`, then delete the topic branch after merge. Do not rely on permanent `feature/*` branches on the remote.

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

### Logger (systemd)

```bash
sudo cp ~/hermes-src/hermes/linux/odroid/systemd/hermes-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-logger.service
```

### Push OLED Context (on Odroid)

```bash
~/hermes-src/hermes/tools/push_oled_context.sh
```

## Docs

- Project docs: `hermes/docs/`
- HERMES brain docs: `hermes-brain/docs/`
