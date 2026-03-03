from fastapi import APIRouter, Body, Query
from fastapi.responses import HTMLResponse

from ..services.reporting import (
	api_reports_generate,
	api_reports_list,
	render_reports_page,
	resolve_report_download_path,
)
from fastapi.responses import FileResponse

router = APIRouter()


def reports_page() -> HTMLResponse:
	return HTMLResponse(render_reports_page())


def reports_list(limit: int = Query(10, ge=1, le=50)) -> dict:
	return api_reports_list(limit)


def reports_generate(payload: dict = Body(default={})) -> dict:
	return api_reports_generate(payload)


def reports_download(report_id: int) -> FileResponse:
	file_path = resolve_report_download_path(report_id)
	return FileResponse(path=str(file_path), media_type="text/html", filename=file_path.name)


router.add_api_route("/reports", reports_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/reports", reports_list, methods=["GET"])
router.add_api_route("/api/reports/generate", reports_generate, methods=["POST"])
router.add_api_route("/api/reports/{report_id}/download", reports_download, methods=["GET"])
