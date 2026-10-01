"""Music API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from pathlib import Path
from typing import List
import os

from ..database import get_db
from ..models import Album, Artist, Track
from ..config import settings
from ..templating import templates

router = APIRouter(prefix="/music")

@router.get("/", response_class=HTMLResponse)
async def music_home(request: Request, db: Session = Depends(get_db)):
    """Music home page - shows all albums."""
    albums = db.query(Album).order_by(Album.title).all()
    artists = db.query(Artist).order_by(Artist.name).all()

    return templates.TemplateResponse(
        request,
        "music/index.html",
        {
            "albums": albums,
            "artists": artists,
            "title": "Music Library"
        }
    )


@router.get("/albums", response_class=HTMLResponse)
async def list_albums(request: Request, db: Session = Depends(get_db)):
    """List all albums."""
    albums = db.query(Album).order_by(Album.title).all()

    return templates.TemplateResponse(
        request,
        "music/albums.html",
        {
            "albums": albums,
            "title": "Albums"
        }
    )


@router.get("/tracks/bulk-edit", response_class=HTMLResponse)
async def bulk_edit_tracks_page(request: Request, ids: str, db: Session = Depends(get_db)):
    """Show bulk edit page for multiple tracks."""
    track_ids = [int(id.strip()) for id in ids.split(',') if id.strip()]

    if not track_ids:
        raise HTTPException(status_code=400, detail="No track IDs provided")

    tracks = db.query(Track).filter(Track.id.in_(track_ids)).order_by(Track.title).all()

    return templates.TemplateResponse(
        request,
        "music/bulk_edit_songs.html",
        {
            "tracks": tracks,
            "title": f"Bulk Edit {len(tracks)} Songs"
        }
    )


@router.get("/albums/bulk-edit", response_class=HTMLResponse)
async def bulk_edit_albums_page(request: Request, ids: str, db: Session = Depends(get_db)):
    """Show bulk edit page for multiple albums."""
    album_ids = [int(id.strip()) for id in ids.split(',') if id.strip()]

    if not album_ids:
        raise HTTPException(status_code=400, detail="No album IDs provided")

    albums = db.query(Album).filter(Album.id.in_(album_ids)).all()

    return templates.TemplateResponse(
        request,
        "music/bulk_edit.html",
        {
            "albums": albums,
            "title": f"Bulk Edit {len(albums)} Albums"
        }
    )


@router.get("/albums/{album_id}", response_class=HTMLResponse)
async def view_album(album_id: int, request: Request, db: Session = Depends(get_db)):
    """View a specific album with its tracks."""
    album = db.query(Album).filter(Album.id == album_id).first()

    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # Convert tracks to dictionaries for JSON serialization
    tracks_data = [
        {
            "id": track.id,
            "title": track.title,
            "track_number": track.track_number,
            "duration": track.duration
        }
        for track in album.tracks
    ]

    return templates.TemplateResponse(
        request,
        "music/album.html",
        {
            "album": album,
            "tracks_data": tracks_data,
            "title": album.title
        }
    )


@router.get("/artists", response_class=HTMLResponse)
async def list_artists(request: Request, db: Session = Depends(get_db)):
    """List all artists."""
    artists = db.query(Artist).order_by(Artist.name).all()

    return templates.TemplateResponse(
        request,
        "music/artists.html",
        {
            "artists": artists,
            "title": "Artists"
        }
    )


@router.get("/artists/{artist_id}", response_class=HTMLResponse)
async def view_artist(artist_id: int, request: Request, db: Session = Depends(get_db)):
    """View a specific artist with their albums."""
    artist = db.query(Artist).filter(Artist.id == artist_id).first()

    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")

    return templates.TemplateResponse(
        request,
        "music/artist.html",
        {
            "artist": artist,
            "title": artist.name
        }
    )


@router.get("/stream/{track_id}")
async def stream_track(track_id: int, db: Session = Depends(get_db)):
    """Stream an audio track."""
    track = db.query(Track).filter(Track.id == track_id).first()

    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # Construct full file path
    file_path = Path(settings.music_dir) / track.file_path

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Determine media type based on file extension
    media_types = {
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "ogg": "audio/ogg",
        "wav": "audio/wav",
        "wma": "audio/x-ms-wma"
    }

    media_type = media_types.get(track.file_format, "audio/mpeg")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=f"{track.title}.{track.file_format}"
    )


@router.get("/api/albums")
async def api_list_albums(db: Session = Depends(get_db)):
    """API endpoint to list all albums."""
    albums = db.query(Album).order_by(Album.title).all()
    return [
        {
            "id": album.id,
            "title": album.title,
            "artist": album.artist.name if album.artist else "Unknown",
            "year": album.year,
            "track_count": len(album.tracks)
        }
        for album in albums
    ]


@router.get("/api/tracks")
async def api_list_tracks(db: Session = Depends(get_db)):
    """API endpoint to list all tracks with album art info."""
    tracks = db.query(Track).order_by(Track.title).all()
    return [
        {
            "id": track.id,
            "title": track.title,
            "artist": track.artist.name if track.artist else (
                track.album.album_artist or
                (track.album.artist.name if track.album and track.album.artist else "Unknown")
            ),
            "album": track.album.title if track.album else "Unknown",
            "album_id": track.album_id,
            "artist_id": track.artist_id,
            "cover_art": track.album.cover_art_path if track.album else None,
            "duration": track.duration,
            "track_number": track.track_number,
        }
        for track in tracks
    ]
