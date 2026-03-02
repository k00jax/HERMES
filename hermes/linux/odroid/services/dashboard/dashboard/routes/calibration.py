from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import (
	api_buzzer_chime,
	api_radar_calibrate_cancel,
	api_radar_calibrate_start,
	api_radar_calibrate_status,
	api_radar_calibration_history,
	api_radar_calibration_latest,
	api_radar_calibration_note,
	calibration_page,
)

router = APIRouter()

router.add_api_route("/calibration", calibration_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/radar/calibrate", api_radar_calibrate_start, methods=["POST"])
router.add_api_route("/api/radar/calibrate/{session_id}", api_radar_calibrate_status, methods=["GET"])
router.add_api_route("/api/radar/calibrate/{session_id}/cancel", api_radar_calibrate_cancel, methods=["POST"])
router.add_api_route("/api/radar/calibration/{calibration_id}/note", api_radar_calibration_note, methods=["POST"])
router.add_api_route("/api/radar/calibration/history", api_radar_calibration_history, methods=["GET"])
router.add_api_route("/api/radar/calibration/latest", api_radar_calibration_latest, methods=["GET"])
router.add_api_route("/api/buzzer/chime", api_buzzer_chime, methods=["POST"])
