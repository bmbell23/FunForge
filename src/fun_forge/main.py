"""Main FastAPI application for FunForge."""

import logging
import asyncio
import time
from typing import Optional
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from pydantic import BaseModel
from pathlib import Path
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

from .config import settings
from .database import engine, Base, get_db
from .templating import templates
from .routes import music, admin
from .routes import podcasts, videos
from .services.scanner import MusicScanner
from .services.podcast_scanner import PodcastScanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Background task for periodic scanning
background_tasks = set()


async def periodic_scan():
    """Background task to scan music and podcast libraries every 10 minutes."""
    from .database import SessionLocal

    while True:
        try:
            await asyncio.sleep(600)

            logger.info("Running periodic library scan...")
            db = SessionLocal()
            try:
                stats = MusicScanner(db).scan_library()
                logger.info(f"Periodic music scan complete: {stats}")
                podcast_stats = PodcastScanner(db).scan_library()
                logger.info(f"Periodic podcast scan complete: {podcast_stats}")
            except Exception as e:
                logger.error(f"Error during periodic scan: {e}")
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.info("Periodic scan task cancelled")
            break
        except Exception as e:
            logger.error(f"Unexpected error in periodic scan: {e}")
            await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events for the application."""
    # Startup
    logger.info("Starting FunForge application...")

    # Create database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")

    # Run initial scan if configured
    if settings.scan_on_startup:
        logger.info("Running initial music library scan...")
        from .database import SessionLocal
        db = SessionLocal()
        try:
            scanner = MusicScanner(db)
            stats = scanner.scan_library()
            logger.info(f"Initial music scan complete: {stats}")
        except Exception as e:
            logger.error(f"Error during initial music scan: {e}")
        finally:
            db.close()

        logger.info("Running initial podcast library scan...")
        db = SessionLocal()
        try:
            podcast_stats = PodcastScanner(db).scan_library()
            logger.info(f"Initial podcast scan complete: {podcast_stats}")
        except Exception as e:
            logger.error(f"Error during initial podcast scan: {e}")
        finally:
            db.close()

    # Start periodic scanning task
    logger.info("Starting periodic scan task (every 10 minutes)...")
    task = asyncio.create_task(periodic_scan())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    yield

    # Shutdown
    logger.info("Shutting down FunForge application...")
    # Cancel background tasks
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)


# Initialize FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="A kid-friendly touchscreen media server for music, movies, and shows",
    version="0.1.0",
    lifespan=lifespan
)

# Setup paths
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Ensure directories exist
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    """Tell caches what to do with /static — StaticFiles says nothing at all.

    Android's WebView treats a response with no Cache-Control as free to keep for
    as long as it likes, so it served a stale style.css alongside freshly
    rendered HTML and drew the new markup with none of its rules.

    Assets requested through templating.static_url() carry a ?v= stamp that
    changes whenever the file does, so those are safe to keep forever. Anything
    else (cover art referenced straight from the database) must revalidate, which
    is a cheap 304 against the ETag StaticFiles already sends.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if "v" in request.query_params
            else "public, max-age=0, must-revalidate"
        )
    elif response.headers.get("content-type", "").startswith("text/html"):
        # The pages carry the ?v= stamps, so a cached page means cached stamps —
        # the markup and the stylesheet must never be able to drift apart.
        response.headers["Cache-Control"] = "no-cache"
    return response

# Include routers
app.include_router(music.router, tags=["music"])
app.include_router(admin.router, tags=["admin"])
app.include_router(podcasts.router, tags=["podcasts"])
app.include_router(videos.router, tags=["videos"])


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page — three big buttons: Songs, Stories, Videos.

    No longer counts rows: the home page used to show a library-statistics grid,
    which meant five COUNT(*) queries on every load of a page a toddler taps
    straight through.
    """
    return templates.TemplateResponse(
        request,
        "index.html",
        {"title": settings.app_name},
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Android app distribution — build-apk.sh drops the APK and its version manifest
# into data/, which is bind-mounted into the container. The installed app polls
# version.json and self-updates; a fresh phone installs the APK from a browser.
# ---------------------------------------------------------------------------

DATA_DIR = Path("/app/data") if Path("/app/data").is_dir() else Path("data")


@app.get("/download/{filename}")
async def download_file(filename: str):
    """Serve the Android APK and its version manifest. Nothing else in data/."""
    if filename == "funforge.apk":
        media_type = "application/vnd.android.package-archive"
    elif filename == "version.json":
        media_type = "application/json"
    else:
        raise HTTPException(status_code=404, detail="Not found")

    file_path = DATA_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
        # The updater compares versionCode, so a cached manifest would hide new
        # builds until the WebView's HTTP cache happened to expire.
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Remote player control — in-memory state, resets on restart (intentional:
# playback state is transient just like guest sessions).
# ---------------------------------------------------------------------------

class PlayerState(BaseModel):
    playing: bool = False
    track_id: Optional[int] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    cover_art: Optional[str] = None
    duration: Optional[float] = None
    position: Optional[float] = None

class PlayerCommand(BaseModel):
    command: str   # "play" | "pause" | "toggle" | "next" | "prev" | "stop"

_player_state: dict = {"playing": False}
_pending_command: Optional[str] = None


@app.post("/api/player/state")
async def push_player_state(state: PlayerState):
    """Main app reports its current playback state (called every few seconds)."""
    global _player_state
    _player_state = {**state.model_dump(), "updated_at": time.time()}
    return {"ok": True}


@app.get("/api/player/state")
async def get_player_state():
    """Remote control page polls this to show current track and status."""
    return _player_state


@app.post("/api/player/command")
async def send_command(cmd: PlayerCommand):
    """Remote sends a command; the main app picks it up on its next poll."""
    global _pending_command
    if cmd.command not in ("play", "pause", "toggle", "next", "prev", "stop"):
        return {"error": "invalid command"}
    _pending_command = cmd.command
    return {"ok": True, "command": cmd.command}


@app.get("/api/player/poll")
async def poll_command():
    """Main app polls this every ~1.5 s. Returns the pending command then clears it."""
    global _pending_command
    cmd, _pending_command = _pending_command, None
    return {"command": cmd}


@app.get("/remote", response_class=HTMLResponse)
async def remote_page(request: Request):
    """Serve the remote control page (works in any browser — phone, tablet, watch)."""
    return templates.TemplateResponse(request, "remote.html")


@app.post("/api/scan")
async def trigger_scan(db: Session = Depends(get_db)):
    """Manually trigger a library scan."""
    try:
        scanner = MusicScanner(db)
        stats = scanner.scan_library()
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.error(f"Error during manual scan: {e}")
        return {"status": "error", "message": str(e)}

