"""Podcast API routes."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session
from pathlib import Path
from pydantic import BaseModel

from ..database import get_db
from ..models import PodcastShow, PodcastEpisode
from ..config import settings
from ..services.podcast_scanner import PodcastScanner
from ..templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/podcasts")

# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def podcasts_home(request: Request, db: Session = Depends(get_db)):
    shows = db.query(PodcastShow).order_by(PodcastShow.title).all()
    return templates.TemplateResponse(
        request,
        "podcasts/index.html",
        {"shows": shows, "title": "Stories"},
    )


@router.get("/shows/{show_id}", response_class=HTMLResponse)
async def view_show(show_id: int, request: Request, db: Session = Depends(get_db)):
    show = db.query(PodcastShow).filter(PodcastShow.id == show_id).first()
    if not show:
        raise HTTPException(status_code=404, detail="Podcast show not found")

    episodes_data = [
        {
            "id": ep.id,
            "title": ep.title,
            "emoji": ep.emoji,
            "episode_number": ep.episode_number,
            "duration": ep.duration,
            "show_id": ep.show_id,
        }
        for ep in show.episodes
    ]

    return templates.TemplateResponse(
        request,
        "podcasts/show.html",
        {"show": show, "episodes_data": episodes_data, "title": show.title},
    )


# ---------------------------------------------------------------------------
# Audio streaming
# ---------------------------------------------------------------------------

MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "wma": "audio/x-ms-wma",
}


@router.get("/stream/{episode_id}")
async def stream_episode(episode_id: int, db: Session = Depends(get_db)):
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")

    file_path = Path(settings.podcast_dir) / episode.file_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    media_type = MEDIA_TYPES.get(episode.file_format or "", "audio/mpeg")
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=f"{episode.title}.{episode.file_format}",
    )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@router.get("/api/shows")
async def api_list_shows(db: Session = Depends(get_db)):
    shows = db.query(PodcastShow).order_by(PodcastShow.title).all()
    return [
        {
            "id": show.id,
            "title": show.title,
            "cover_art": show.cover_art_path,
            "episode_count": len(show.episodes),
        }
        for show in shows
    ]


@router.get("/api/episodes")
async def api_list_episodes(db: Session = Depends(get_db)):
    episodes = db.query(PodcastEpisode).order_by(PodcastEpisode.title).all()
    return [
        {
            "id": ep.id,
            "title": ep.title,
            "emoji": ep.emoji,
            "show_id": ep.show_id,
            "show_title": ep.show.title if ep.show else "Unknown",
            "cover_art": ep.show.cover_art_path if ep.show else None,
            "duration": ep.duration,
            "episode_number": ep.episode_number,
        }
        for ep in episodes
    ]


class EmojiUpdate(BaseModel):
    emoji: str


@router.post("/api/episodes/{episode_id}/emoji")
async def update_episode_emoji(
    episode_id: int,
    body: EmojiUpdate,
    db: Session = Depends(get_db),
):
    episode = db.query(PodcastEpisode).filter(PodcastEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    episode.emoji = body.emoji
    db.commit()
    return {"status": "ok", "emoji": episode.emoji}


@router.post("/api/scan")
async def trigger_scan(db: Session = Depends(get_db)):
    try:
        scanner = PodcastScanner(db)
        stats = scanner.scan_library()
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.error(f"Error during podcast scan: {e}")
        return {"status": "error", "message": str(e)}
