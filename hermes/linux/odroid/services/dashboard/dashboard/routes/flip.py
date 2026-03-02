from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import (
  api_flip_status,
  camera_latest_meta,
  camera_snapshot,
  camera_stream,
  camera_trigger,
  flip_page,
)

router = APIRouter()

router.add_api_route("/api/flip/status", api_flip_status, methods=["GET"])
router.add_api_route("/flip", flip_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/camera/stream", camera_stream, methods=["GET"])
router.add_api_route("/camera/trigger", camera_trigger, methods=["POST"])
router.add_api_route("/camera/latest/meta", camera_latest_meta, methods=["GET"])
router.add_api_route("/camera/snapshot", camera_snapshot, methods=["GET"])
