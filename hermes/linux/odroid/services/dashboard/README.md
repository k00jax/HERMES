# HERMES Dashboard Service

This folder contains the live dashboard and telnet operator interface.

## Files

- `app.py`
  - thin entrypoint shim
  - keeps `python3 app.py` behavior stable
- `legacy_app.py`
  - existing monolithic FastAPI runtime preserved during refactor
  - contains current routes and lifecycle logic
- `telnet_portal.py`
  - compatibility import shim

## New module layout

The dashboard service now uses a package layout under `dashboard/` so code can be extracted incrementally without changing runtime behavior. Core directories are:

- `dashboard/app.py` (app factory / assembly)
- `dashboard/routes/` (route modules)
- `dashboard/db/` (sqlite connection/query helpers)
- `dashboard/services/` (calibration/report/export/health logic)
- `dashboard/net/telnet_portal.py` (telnet portal implementation)

Entrypoint remains unchanged: `python3 app.py`.

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
