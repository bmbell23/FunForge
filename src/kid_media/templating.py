"""Shared Jinja2 environment.

Every module that renders HTML uses this one instance. Previously main.py and each
of the four route modules built their own Jinja2Templates, and `format_duration`
was defined three separate times — so a template global added in one place
(`parent_pin`, which base.html needs on every single page) silently went missing
on pages rendered by the others.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def static_url(path: str) -> str:
    """URL for a static asset, stamped with the file's modification time.

    Without this the Android WebView keeps its cached copy of style.css for as
    long as it likes — nothing on /static sends Cache-Control — and pairs it with
    freshly rendered HTML. Ship markup that depends on new CSS and the phone
    renders the new elements with no rules at all: unstyled links where the home
    tiles should be, and an album cover at its natural 800px because .pc-art
    never existed in the cached sheet.

    The stamp changes with the file, so a changed asset is a different URL and
    can never be answered from cache. Templates use it via {{ static_url(...) }}.
    """
    try:
        stamp = int((STATIC_DIR / path).stat().st_mtime)
    except OSError:
        # Asset is missing; let the 404 be visible rather than papering over it.
        return f"/static/{path}"
    return f"/static/{path}?v={stamp}"


def format_duration(seconds) -> str:
    """Format a duration in seconds as M:SS."""
    if not seconds:
        return "0:00"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


templates.env.filters["format_duration"] = format_duration

# base.html hands this to the parent-lock keypad.
templates.env.globals["parent_pin"] = settings.parent_pin
templates.env.globals["static_url"] = static_url
