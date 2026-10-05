"""Admin/Parent mode routes for metadata editing."""

import logging
import os
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from pathlib import Path
from typing import List
import shutil

from ..database import get_db
from ..models import Album, Artist, Track
from ..services.scanner import add_excluded_paths
from ..config import settings
from ..templating import templates


def _delete_music_file(relative_path: str) -> None:
    """Delete a music file from disk. Logs but does not raise on failure."""
    if not relative_path:
        return
    full_path = Path(settings.music_dir) / relative_path
    try:
        if full_path.exists():
            full_path.unlink()
            logger.info(f"Deleted music file: {full_path}")
        else:
            logger.warning(f"File already gone: {full_path}")
    except OSError as e:
        logger.error(f"Could not delete file {full_path}: {e}")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/music")


@router.post("/api/tracks/bulk-delete")
async def bulk_delete_tracks(request: Request, db: Session = Depends(get_db)):
    """Delete multiple tracks."""
    try:
        data = await request.json()
        track_ids = data.get("track_ids", [])

        if not track_ids:
            return JSONResponse({"status": "error", "message": "No tracks selected"})

        deleted_count = 0
        excluded = []
        for track_id in track_ids:
            track = db.query(Track).filter(Track.id == int(track_id)).first()
            if track:
                excluded.append(track.file_path)
                db.delete(track)
                deleted_count += 1

        db.commit()
        for fp in excluded:
            _delete_music_file(fp)
        if excluded:
            add_excluded_paths(excluded)

        return JSONResponse({
            "status": "success",
            "deleted_count": deleted_count
        })
    except Exception as e:
        logger.error(f"Error deleting tracks: {e}")
        db.rollback()
        return JSONResponse({"status": "error", "message": str(e)})


@router.post("/tracks/bulk-edit")
async def bulk_update_tracks(request: Request, db: Session = Depends(get_db)):
    """Update multiple tracks at once (title and track_number per track)."""
    try:
        data = await request.json()
        updates = data.get("updates", [])  # [{track_id, title, track_number}]

        if not updates:
            return JSONResponse({"status": "error", "message": "No updates provided"})

        updated_count = 0
        for update in updates:
            track = db.query(Track).filter(Track.id == int(update["track_id"])).first()
            if track:
                if update.get("title"):
                    track.title = update["title"]
                track.track_number = update.get("track_number") or None
                updated_count += 1

        db.commit()

        return JSONResponse({
            "status": "success",
            "updated_count": updated_count
        })
    except Exception as e:
        logger.error(f"Error bulk updating tracks: {e}")
        db.rollback()
        return JSONResponse({"status": "error", "message": str(e)})


@router.post("/api/albums/bulk-delete")
async def bulk_delete_albums(request: Request, db: Session = Depends(get_db)):
    """Delete multiple albums."""
    try:
        data = await request.json()
        album_ids = data.get("album_ids", [])

        if not album_ids:
            return JSONResponse({"status": "error", "message": "No albums selected"})

        deleted_count = 0
        excluded = []
        for album_id in album_ids:
            album = db.query(Album).filter(Album.id == int(album_id)).first()
            if album:
                excluded.extend(t.file_path for t in album.tracks if t.file_path)
                db.delete(album)
                deleted_count += 1

        db.commit()
        for fp in excluded:
            _delete_music_file(fp)
        if excluded:
            add_excluded_paths(excluded)

        return JSONResponse({
            "status": "success",
            "deleted_count": deleted_count
        })
    except Exception as e:
        logger.error(f"Error deleting albums: {e}")
        db.rollback()
        return JSONResponse({"status": "error", "message": str(e)})


@router.post("/albums/bulk-edit")
async def bulk_update_albums(
    request: Request,
    db: Session = Depends(get_db)
):
    """Update multiple albums at once."""
    try:
        data = await request.json()
        album_artist = data.get("album_artist")
        year = data.get("year")
        genre = data.get("genre")
        album_ids = data.get("album_ids", [])

        if not album_ids:
            return JSONResponse({"status": "error", "message": "No albums selected"})

        updated_count = 0
        for album_id in album_ids:
            album = db.query(Album).filter(Album.id == int(album_id)).first()
            if album:
                if album_artist:
                    album.album_artist = album_artist
                if year:
                    album.year = int(year)
                if genre:
                    album.genre = genre
                updated_count += 1

        db.commit()

        return JSONResponse({
            "status": "success",
            "updated_count": updated_count
        })
    except Exception as e:
        logger.error(f"Error bulk updating albums: {e}")
        db.rollback()
        return JSONResponse({"status": "error", "message": str(e)})


@router.get("/albums/{album_id}/edit", response_class=HTMLResponse)
async def edit_album_page(album_id: int, request: Request, db: Session = Depends(get_db)):
    """Show album edit page."""
    album = db.query(Album).filter(Album.id == album_id).first()

    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    artists = db.query(Artist).order_by(Artist.name).all()

    return templates.TemplateResponse(
        request,
        "music/edit_album.html",
        {
            "album": album,
            "artists": artists,
            "title": f"Edit {album.title}"
        }
    )


@router.post("/albums/{album_id}/edit")
async def update_album(
    album_id: int,
    title: str = Form(...),
    album_artist: str = Form(...),
    year: int = Form(None),
    genre: str = Form(None),
    cover_art: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    """Update album metadata."""
    try:
        album = db.query(Album).filter(Album.id == album_id).first()

        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        # Update basic fields
        album.title = title
        album.album_artist = album_artist
        album.year = year if year else None
        album.genre = genre if genre else None

        # Handle cover art upload
        if cover_art and cover_art.filename:
            covers_dir = settings.covers_dir
            covers_dir.mkdir(parents=True, exist_ok=True)

            # Save uploaded file
            file_ext = Path(cover_art.filename).suffix or ".jpg"
            filename = f"album_{album_id}{file_ext}"
            file_path = covers_dir / filename

            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(cover_art.file, buffer)

            album.cover_art_path = f"/static/covers/{filename}"

        db.commit()

        return RedirectResponse(url="/music/", status_code=303)
    except Exception as e:
        logger.error(f"Error updating album: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tracks/{track_id}/delete")
async def delete_track(track_id: int, db: Session = Depends(get_db)):
    """Delete a single track."""
    try:
        track = db.query(Track).filter(Track.id == track_id).first()

        if not track:
            return JSONResponse({"status": "error", "message": "Track not found"})

        file_path = track.file_path
        db.delete(track)
        db.commit()
        if file_path:
            _delete_music_file(file_path)
            add_excluded_paths([file_path])

        return JSONResponse({"status": "success"})
    except Exception as e:
        logger.error(f"Error deleting track: {e}")
        db.rollback()
        return JSONResponse({"status": "error", "message": str(e)})


@router.post("/tracks/{track_id}/edit")
async def update_track(
    track_id: int,
    title: str = Form(...),
    track_number: int = Form(None),
    db: Session = Depends(get_db)
):
    """Update track metadata."""
    try:
        track = db.query(Track).filter(Track.id == track_id).first()

        if not track:
            raise HTTPException(status_code=404, detail="Track not found")

        track.title = title
        track.track_number = track_number if track_number else None

        db.commit()

        return JSONResponse({"status": "success"})
    except Exception as e:
        logger.error(f"Error updating track: {e}")
        db.rollback()
        return JSONResponse({"status": "error", "message": str(e)})

