"""Video routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter(prefix="/videos")


@router.get("/", response_class=HTMLResponse)
async def videos_home(request: Request):
    """Videos landing page."""
    return templates.TemplateResponse(
        request,
        "videos/index.html",
        {"title": "Videos"},
    )
