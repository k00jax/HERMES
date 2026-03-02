from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import (
	api_chime_preview,
	api_settings_get,
	api_settings_post,
	api_settings_reset,
	field_page,
	settings_page,
)

router = APIRouter()

router.add_api_route("/settings", settings_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/field", field_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/settings", api_settings_get, methods=["GET"])
router.add_api_route("/api/settings", api_settings_post, methods=["POST"])
router.add_api_route("/api/settings/reset", api_settings_reset, methods=["POST"])
router.add_api_route("/api/chime/preview", api_chime_preview, methods=["POST"])
