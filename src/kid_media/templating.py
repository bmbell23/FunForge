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

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
