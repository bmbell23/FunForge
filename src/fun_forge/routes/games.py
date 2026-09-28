"""Games routes.

Just the door for now: the home page's fourth tile needs somewhere to land. How
games are actually built and run is decided in issue #3.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter(prefix="/games")


@router.get("/", response_class=HTMLResponse)
async def games_home(request: Request):
    """Games landing page."""
    return templates.TemplateResponse(
        request,
        "games/index.html",
        {"title": "Games"},
    )
