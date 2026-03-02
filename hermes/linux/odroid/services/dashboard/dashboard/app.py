from __future__ import annotations

from fastapi import FastAPI

from legacy_app import APP as _LEGACY_APP
from .routes.analytics import router as analytics_router
from .routes.calibration import router as calibration_router
from .routes.events import router as events_router
from .routes.field import router as field_router
from .routes.history import router as history_router
from .routes.home import router as home_router
from .routes.reports import router as reports_router
from .routes.settings import router as settings_router


_ROUTERS = (
  home_router,
  history_router,
  events_router,
  analytics_router,
  calibration_router,
  settings_router,
  reports_router,
  field_router,
)


def create_app() -> FastAPI:
  app = _LEGACY_APP
  for router in _ROUTERS:
    app.include_router(router)
  return app


app = create_app()
APP = app
