"""Games routes.

One route per game, deliberately — the same shape `videos.py` and `podcasts.py`
already use. A scanned `games/` directory with a manifest would be tidier once
there are half a dozen games, but building that plugin layer before the second
game exists is how the abstraction ends up wrong. See issue #3.

The two match games share one template and differ only in where their pictures
come from, which is the whole reason `/games/api/pictures` takes a `source`.
"""

import json
import random
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from PIL import Image

from ..config import settings
from ..database import get_db
from ..models import Album
from ..templating import templates

router = APIRouter(prefix="/games")

# Board sizes the match game walks through, easiest first. It stops at the last
# one and stays there rather than getting harder forever: this is an endless toy,
# not a difficulty ladder, and a three-year-old who hits a wall just leaves.
MATCH_LEVELS = [2, 3, 4, 6]

# Written by scripts/sync_immich_faces.py, which runs on the host. The app never
# talks to Immich itself — it reads this file and the cached thumbnails beside it,
# so the family games keep working when Immich is down.
FAMILY_MANIFEST = Path(settings.data_dir) / "family_photos.json"

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# How many of a cover's 256 thumbnail bits may differ before it counts as a
# different picture. The five Bluey theme-song singles (English, German, Spanish,
# Italian, French) are separate files that differ only in a caption a toddler
# can't read, and they land within 9 of each other; the closest genuinely
# different pair in the library sits at 22.
LOOKALIKE_BITS = 12


# ---------------------------------------------------------------------------
# family photo manifest
# ---------------------------------------------------------------------------

def load_family_photos(approved_only: bool = True):
    """Cached family photos. Returns [] for every failure — a missing or broken
    manifest must degrade to "no family game", never to a 500 on the picker."""
    try:
        data = json.loads(FAMILY_MANIFEST.read_text())
    except (OSError, ValueError):
        return []
    photos = data.get("photos") or []
    if approved_only:
        photos = [p for p in photos if p.get("approved")]
    return photos


def save_family_approvals(approved_ids):
    """Write the approved set back to the manifest, leaving everything else be."""
    try:
        data = json.loads(FAMILY_MANIFEST.read_text())
    except (OSError, ValueError):
        return False
    wanted = set(approved_ids)
    for photo in data.get("photos", []):
        photo["approved"] = photo.get("id") in wanted
    try:
        FAMILY_MANIFEST.write_text(json.dumps(data, indent=2))
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# look-alike covers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1024)
def _cover_hash(path: str, mtime: float):
    """A 16x16 average hash of one cover: 256 bits, one per cell, set where the
    cell is brighter than the picture's mean. `mtime` is only there to key the
    cache, so a rescan that rewrites the art in place is hashed afresh."""
    with Image.open(path) as im:
        cells = list(im.convert("L").resize((16, 16)).getdata())
    mean = sum(cells) / len(cells)
    return sum(1 << i for i, v in enumerate(cells) if v > mean)


def cover_hash(url_path: str):
    """Hash for a `/static/...` cover, or None if it can't be read. An unreadable
    cover is not a reason to drop an album from the board."""
    try:
        rel = url_path.removeprefix("/static/")
        # Cover art is served from the data dir under its old /static/covers/
        # address (main.py), so the paths stored in the DB never had to change.
        disk = (settings.covers_dir / rel.removeprefix("covers/")
                if rel.startswith("covers/") else STATIC_DIR / rel)
        return _cover_hash(str(disk), disk.stat().st_mtime)
    except (OSError, ValueError):
        return None


def looks_like_any(h, taken) -> bool:
    return h is not None and any(bin(h ^ t).count("1") <= LOOKALIKE_BITS for t in taken)


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def games_home(request: Request):
    """The picker — every game the app has, as one tile each.

    The family game only appears once there are at least two approved photos:
    a tile that opens onto an empty board is worse than no tile, because a kid
    who taps it is then locked into nothing.
    """
    family_ready = len(load_family_photos()) >= 2
    return templates.TemplateResponse(
        request,
        "games/index.html",
        {"title": "Games", "family_ready": family_ready},
    )


@router.get("/cover-match/", response_class=HTMLResponse)
async def cover_match(request: Request):
    """Match the pairs, played with the covers out of the kid's own library."""
    return templates.TemplateResponse(
        request,
        "games/match.html",
        {"title": "Match the Covers", "levels": MATCH_LEVELS, "source": "covers"},
    )


@router.get("/family-match/", response_class=HTMLResponse)
async def family_match(request: Request):
    """The same game, played with approved photos of the family."""
    return templates.TemplateResponse(
        request,
        "games/match.html",
        {"title": "Match the Family", "levels": MATCH_LEVELS, "source": "family"},
    )


@router.get("/tic-tac-toe/", response_class=HTMLResponse)
async def tic_tac_toe(request: Request):
    """Noughts and crosses with a dog and a cat: two friends, or a friendly Robo."""
    return templates.TemplateResponse(
        request,
        "games/tictactoe.html",
        {"title": "Tic-Tac-Toe"},
    )


@router.get("/checkers/", response_class=HTMLResponse)
async def checkers(request: Request):
    """Checkers for two friends: dogs against cats, tap a piece, tap where it goes."""
    return templates.TemplateResponse(
        request,
        "games/checkers.html",
        {"title": "Checkers"},
    )


@router.get("/web-climb/", response_class=HTMLResponse)
async def web_climb(request: Request):
    """Web Climb: chutes and ladders for two spiders, spin and climb to the top.
    The ladders and slides live in the template, like the other games' rules."""
    return templates.TemplateResponse(
        request,
        "games/webclimb.html",
        {"title": "Web Climb"},
    )


@router.get("/spell/", response_class=HTMLResponse)
async def spell(request: Request):
    """Spell It: a picture, its letters, and a garden that grows. The word list
    lives in the template, like the other games' rules do."""
    return templates.TemplateResponse(
        request,
        "games/spell.html",
        {"title": "Spell It"},
    )


@router.get("/bubbles/", response_class=HTMLResponse)
async def bubbles(request: Request):
    """Bubble Pop: bubbles drift up, every tap pops one. Nothing to win or lose."""
    return templates.TemplateResponse(
        request,
        "games/bubbles.html",
        {"title": "Bubble Pop"},
    )


@router.get("/animals/", response_class=HTMLResponse)
async def animals(request: Request):
    """Animal Sounds: six big animals, each one says its piece when tapped."""
    return templates.TemplateResponse(
        request,
        "games/animals.html",
        {"title": "Animal Sounds"},
    )


@router.get("/photos/", response_class=HTMLResponse)
async def family_photos_admin(request: Request):
    """Parent screen: tick which cached photos may appear in a game.

    Nothing Immich hands over is approved by syncing. Face recognition returns
    *every* photo of a child — bath time, hospital trips, whatever else is in a
    family library — so a grown-up looks first.
    """
    photos = load_family_photos(approved_only=False)
    return templates.TemplateResponse(
        request,
        "games/photos.html",
        {
            "title": "Family Photos",
            "photos": photos,
            "approved_count": sum(1 for p in photos if p.get("approved")),
        },
    )


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------

@router.get("/api/pictures")
async def pictures(source: str = "covers", count: int = 6,
                   db: Session = Depends(get_db)):
    """`count` random pictures for a game board.

    Album covers are deduplicated by what they look like, not by album id or
    even by file: two albums can share one cover file, and separate files can
    carry the same picture (every language's Bluey theme single). A "pair" of two
    different albums wearing the same picture is unmatchable nonsense to a kid:
    the board ends with cards that look like they match and won't.
    """
    count = max(1, min(count, 24))

    if source == "family":
        photos = load_family_photos()
        random.shuffle(photos)
        return {"pictures": [
            {"id": p["id"], "title": p.get("person", ""), "cover": f"/static/{p['file']}"}
            for p in photos[:count]
        ]}

    rows = (
        db.query(Album.id, Album.title, Album.cover_art_path)
        .filter(Album.cover_art_path.isnot(None), Album.cover_art_path != "")
        .order_by(func.random())
        .limit(count * 3)          # over-fetch so dedupe still leaves enough
        .all()
    )

    seen, hashes, pics = set(), [], []
    for album_id, title, path in rows:
        if path in seen:
            continue
        seen.add(path)
        h = cover_hash(path)
        if looks_like_any(h, hashes):
            continue
        if h is not None:
            hashes.append(h)
        pics.append({"id": album_id, "title": title, "cover": path})
        if len(pics) == count:
            break

    return {"pictures": pics}


@router.post("/api/photos/approve")
async def approve_photos(request: Request):
    """Replace the approved set with whatever the parent screen ticked."""
    body = await request.json()
    ids = body.get("approved")
    if not isinstance(ids, list):
        return JSONResponse({"ok": False, "error": "approved must be a list"}, 400)
    if not save_family_approvals(ids):
        return JSONResponse({"ok": False, "error": "no photo manifest"}, 400)
    return {"ok": True, "approved": len(ids)}
