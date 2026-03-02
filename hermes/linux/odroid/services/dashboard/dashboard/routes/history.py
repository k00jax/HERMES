from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import api_history, api_history_export_csv, history_page

router = APIRouter()

router.add_api_route("/history", history_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/history", api_history, methods=["GET"])
router.add_api_route("/api/history/export.csv", api_history_export_csv, methods=["GET"])
