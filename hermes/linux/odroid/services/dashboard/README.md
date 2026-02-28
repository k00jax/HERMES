# HERMES Dashboard Service

This folder contains the live dashboard and telnet operator interface.

## Files

- `app.py`
  - FastAPI app
  - dashboard pages and API routes
  - startup/shutdown lifecycle for telnet portal
- `telnet_portal.py`
  - telnet server and keypad UI
  - telnet negotiation compatibility (KaiOS/Mocha style clients)
  - menu/page rendering logic

## Runtime wiring

```text
systemd: hermes-dashboard.service
  -> uvicorn app:APP --host 0.0.0.0 --port 8000 --app-dir .../services/dashboard
      -> FastAPI routes (dashboard/API)
      -> starts HermesTelnetPortal on :8023
```

## Network endpoints

- Dashboard/UI/API: `http://<odroid-ip>:8000`
- Telnet UI: `<odroid-ip>:8023`

## Telnet controls

- `*` = MENU
- `0` = REFRESH
- `#` = EXIT

## Validation

```bash
cd ~/hermes-src/hermes/linux/odroid/services/dashboard
python3 -m py_compile app.py telnet_portal.py
sudo systemctl restart hermes-dashboard.service
sudo ss -ltnp | egrep ':8000|:8023'
```
