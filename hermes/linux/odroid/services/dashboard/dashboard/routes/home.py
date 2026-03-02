from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import (
	api_latest,
	api_ts,
	app_js,
	chart_overlay_png,
	chart_png,
	index,
)

router = APIRouter()

router.add_api_route("/app.js", app_js, methods=["GET"])
router.add_api_route("/", index, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/latest/{table}", api_latest, methods=["GET"])
router.add_api_route("/api/ts/{series}", api_ts, methods=["GET"])
router.add_api_route("/chart/{series}.png", chart_png, methods=["GET"])
router.add_api_route("/chart_overlay.png", chart_overlay_png, methods=["GET"])
