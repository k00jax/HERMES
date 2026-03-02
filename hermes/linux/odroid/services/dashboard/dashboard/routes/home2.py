from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from legacy_app import home2_page

router = APIRouter()

router.add_api_route("/home2", home2_page, methods=["GET"], response_class=HTMLResponse)
