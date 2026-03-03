from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import api_reports_download, api_reports_generate, api_reports_list, reports_page

router = APIRouter()

router.add_api_route("/reports", reports_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/reports", api_reports_list, methods=["GET"])
router.add_api_route("/api/reports/generate", api_reports_generate, methods=["POST"])
router.add_api_route("/api/reports/{report_id}/download", api_reports_download, methods=["GET"])
