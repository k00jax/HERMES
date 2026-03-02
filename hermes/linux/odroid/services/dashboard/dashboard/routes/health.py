from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from legacy_app import api_health, api_ready, api_status, health, healthz, metrics, readyz

router = APIRouter()

router.add_api_route("/healthz", healthz, methods=["GET"])
router.add_api_route("/api/ready", api_ready, methods=["GET"])
router.add_api_route("/api/status", api_status, methods=["GET"])
router.add_api_route("/api/health", api_health, methods=["GET"])
router.add_api_route("/health", health, methods=["GET"])
router.add_api_route("/readyz", readyz, methods=["GET"])
router.add_api_route("/metrics", metrics, methods=["GET"], response_class=PlainTextResponse)
