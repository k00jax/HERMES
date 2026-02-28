# HERMES Monorepo

HERMES runs as an Odroid-hosted telemetry stack with dashboard/API + telnet operator console.

## Runtime Architecture (current)

```text
GitHub repo
	└─ hermes/
			├─ linux/odroid/services/dashboard/
			│   ├─ app.py                 # FastAPI app + lifecycle + API routes
			│   └─ telnet_portal.py       # Telnet UI/controls on :8023
			├─ linux/odroid/systemd/
			│   └─ hermes-dashboard.service
			└─ linux/odroid/README.md

Odroid runtime
	├─ systemd: hermes-dashboard.service
	├─ HTTP UI/API: 0.0.0.0:8000
	└─ Telnet UI:   0.0.0.0:8023
```

## Where code runs vs where you edit

- When using VS Code Remote-SSH (`odroid` target), edits are on Odroid disk.
- Git commits are local to the Odroid clone until pushed.
- GitHub becomes source-of-truth after `git push`.

## Typical dev/deploy flow

```bash
# on development machine
git commit -m "..."
git push

# on odroid
cd ~/hermes-src/hermes
git pull
sudo systemctl restart hermes-dashboard.service
```

## Quick health checks

```bash
sudo systemctl status hermes-dashboard.service --no-pager
sudo ss -ltnp | egrep ':8000|:8023'
curl -sS http://127.0.0.1:8000/api/health
```

## Firmware note

nRF: build on PC (`build_nrf.ps1`) and flash from Odroid (`./flash-nrf`).
