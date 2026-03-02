from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import (
	analytics_page,
	api_analytics_eco2_vs_presence,
	api_analytics_event_counts,
	api_analytics_presence_by_hour,
)

router = APIRouter()

router.add_api_route("/analytics", analytics_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/analytics/presence_by_hour", api_analytics_presence_by_hour, methods=["GET"])
router.add_api_route("/api/analytics/eco2_vs_presence", api_analytics_eco2_vs_presence, methods=["GET"])
router.add_api_route("/api/analytics/event_counts", api_analytics_event_counts, methods=["GET"])
