from __future__ import annotations

from fastapi import FastAPI

from legacy_app import APP as _LEGACY_APP
from .routes.health import router as health_router
from .routes.settings import router as settings_router
from .routes.analytics import router as analytics_router
from .routes.calibration import router as calibration_router
from .routes.events import router as events_router
from .routes.flip import router as flip_router
from .routes.history import router as history_router
from .routes.home import router as home_router
from .routes.home2 import router as home2_router
from .routes.reports import router as reports_router

# ROUTE MAP TABLE (source: legacy_app.py)
# | Group                  | Endpoints                                                                                                  | Route Module              | Status   |
# |------------------------|------------------------------------------------------------------------------------------------------------|---------------------------|----------|
# | Infra                  | /healthz, /api/ready, /api/status, /api/health, /health, /readyz, /metrics                               | dashboard/routes/health.py| migrated |
# | Settings (+ Field Mode)| /settings, /field, /api/settings, /api/settings/reset, /api/chime/preview                                | dashboard/routes/settings.py | migrated |
# | Home                   | /, /app.js, /api/latest/{table}, /api/ts/{series}, /chart/{series}.png, /chart_overlay.png              | dashboard/routes/home.py  | mapped   |
# | Flip                   | /flip, /api/flip/status, /camera/stream, /camera/trigger, /camera/latest/meta, /camera/snapshot         | dashboard/routes/flip.py  | mapped   |
# | Home2                  | /home2                                                                                                     | dashboard/routes/home2.py | mapped   |
# | History                | /history, /api/history, /api/history/export.csv                                                           | dashboard/routes/history.py | mapped |
# | Events                 | /events, /api/events/*, /api/state_events                                                                 | dashboard/routes/events.py | mapped  |
# | Analytics              | /analytics, /api/analytics/*                                                                              | dashboard/routes/analytics.py | mapped |
# | Reports                | /reports, /api/reports, /api/reports/generate, /api/reports/{report_id}/download                         | dashboard/routes/reports.py | migrated |
# | Calibration            | /calibration, /api/radar/calibrate*, /api/radar/calibration/*, /api/buzzer/chime                         | dashboard/routes/calibration.py | mapped |
#
# Reports Inventory (from legacy_app.py)
# | Method | Path                               | Handler               | Response Class |
# |--------|------------------------------------|-----------------------|----------------|
# | GET    | /reports                           | reports_page          | HTMLResponse   |
# | GET    | /api/reports                       | api_reports_list      | default/json   |
# | POST   | /api/reports/generate              | api_reports_generate  | default/json   |
# | GET    | /api/reports/{report_id}/download  | api_reports_download  | FileResponse   |
#
# Migration order: Infra -> Settings -> Home -> Flip -> Home2 -> History -> Events -> Analytics -> Reports -> Calibration


_ROUTERS = (
  health_router,
  settings_router,
  home_router,
  flip_router,
  home2_router,
  history_router,
  events_router,
  analytics_router,
  reports_router,
  calibration_router,
)


def create_app() -> FastAPI:
  app = _LEGACY_APP
  for router in _ROUTERS:
    app.include_router(router)
  return app


app = create_app()
APP = app
