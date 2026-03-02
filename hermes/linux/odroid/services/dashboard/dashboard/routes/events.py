from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import (
	api_events_ack,
	api_events_ack_bulk,
	api_events_clear_snooze_kind,
	api_events_latest,
	api_events_note,
	api_events_since,
	api_events_snooze,
	api_events_snooze_bulk,
	api_state_events,
	events_page,
)

router = APIRouter()

router.add_api_route("/events", events_page, methods=["GET"], response_class=HTMLResponse)
router.add_api_route("/api/events/latest", api_events_latest, methods=["GET"])
router.add_api_route("/api/events", api_events_since, methods=["GET"])
router.add_api_route("/api/events/ack", api_events_ack, methods=["POST"])
router.add_api_route("/api/events/snooze", api_events_snooze, methods=["POST"])
router.add_api_route("/api/events/note", api_events_note, methods=["POST"])
router.add_api_route("/api/events/ack_bulk", api_events_ack_bulk, methods=["POST"])
router.add_api_route("/api/events/snooze_bulk", api_events_snooze_bulk, methods=["POST"])
router.add_api_route("/api/events/clear_snooze_kind", api_events_clear_snooze_kind, methods=["POST"])
router.add_api_route("/api/state_events", api_state_events, methods=["GET"])
