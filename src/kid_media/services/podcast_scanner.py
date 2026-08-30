"""Podcast library scanner service."""

import os
import logging
import hashlib
from pathlib import Path
from typing import Optional
from mutagen import File as MutagenFile
from sqlalchemy.orm import Session
from ..models import PodcastShow, PodcastEpisode
from ..config import settings

logger = logging.getLogger(__name__)

# Cover art filenames to look for inside a show directory (priority order)
COVER_ART_NAMES = ["cover.jpg", "cover.png", "folder.jpg", "folder.png",
                   "poster.jpg", "poster.png", "artwork.jpg", "artwork.png",
                   "show.jpg", "show.png", "thumbnail.jpg", "thumbnail.png"]


class PodcastScanner:
    """Scans podcast directory and updates database.

    Expected layout::

        <podcast_dir>/
            My Podcast Show/
                cover.jpg          ← optional show art
                episode01.mp3
                episode02.mp3
            Another Show/
                folder.jpg
                s01e01.mp3
    """

    def __init__(self, db: Session):
        self.db = db
        self.podcast_dir = Path(settings.podcast_dir)
        self.allowed_extensions = settings.allowed_audio_extensions.split(",")
        self.covers_dir = Path("/app/src/kid_media/static/covers")
        self.covers_dir.mkdir(parents=True, exist_ok=True)

    def scan_library(self) -> dict:
        """Scan the podcast directory and sync the database."""
        logger.info(f"Starting podcast library scan: {self.podcast_dir}")

        stats = {
            "shows_found": 0,
            "episodes_added": 0,
            "episodes_updated": 0,
            "errors": 0,
        }

        if not self.podcast_dir.exists():
            logger.warning(f"Podcast directory does not exist: {self.podcast_dir}")
            return stats

        for item in sorted(self.podcast_dir.iterdir()):
            if not item.is_dir():
                continue  # skip loose files at root

            stats["shows_found"] += 1
            try:
                show = self._get_or_create_show(item)
                self.db.commit()
            except Exception as e:
                logger.error(f"Error processing show directory {item}: {e}")
                self.db.rollback()
                stats["errors"] += 1
                continue

            for file in sorted(item.iterdir()):
                if not file.is_file():
                    continue
                if file.suffix.lower().lstrip(".") not in self.allowed_extensions:
                    continue

                try:
                    added = self._process_episode(file, show)
                    self.db.commit()
                    if added:
                        stats["episodes_added"] += 1
                    else:
                        stats["episodes_updated"] += 1
                except Exception as e:
                    logger.error(f"Error processing episode {file}: {e}")
                    self.db.rollback()
                    stats["errors"] += 1

        logger.info(f"Podcast scan complete: {stats}")
        return stats

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_or_create_show(self, directory: Path) -> PodcastShow:
        """Return the PodcastShow for *directory*, creating it if needed."""
        relative = str(directory.relative_to(self.podcast_dir))
        show = self.db.query(PodcastShow).filter(
            PodcastShow.directory_path == relative
        ).first()

        cover_art_path = self._find_cover_art(directory)

        if not show:
            title = self._dir_to_title(directory.name)
            show = PodcastShow(
                title=title,
                directory_path=relative,
                cover_art_path=cover_art_path,
            )
            self.db.add(show)
            self.db.flush()
            logger.info(f"Created podcast show: {title}")
        elif cover_art_path and not show.cover_art_path:
            show.cover_art_path = cover_art_path

        return show

    def _find_cover_art(self, directory: Path) -> Optional[str]:
        """Look for a cover art image in *directory* and copy it to static."""
        for name in COVER_ART_NAMES:
            candidate = directory / name
            if candidate.exists():
                return self._copy_cover_art(candidate, directory.name)
        return None

    def _copy_cover_art(self, src: Path, show_name: str) -> Optional[str]:
        """Copy a cover image to the static covers dir and return its web path."""
        try:
            unique_str = f"podcast_{show_name}".encode("utf-8")
            filename = hashlib.md5(unique_str).hexdigest() + src.suffix.lower()
            dest = self.covers_dir / filename
            if not dest.exists():
                import shutil
                shutil.copy2(src, dest)
            return f"/static/covers/{filename}"
        except Exception as e:
            logger.error(f"Error copying cover art {src}: {e}")
            return None

    def _process_episode(self, file: Path, show: PodcastShow) -> bool:
        """Add or update a podcast episode. Returns True if newly created."""
        relative = str(file.relative_to(self.podcast_dir))

        existing = self.db.query(PodcastEpisode).filter(
            PodcastEpisode.file_path == relative
        ).first()

        metadata = self._extract_metadata(file)

        title = (metadata.get("title") if metadata else None) or file.stem
        episode_number = (metadata.get("track_number") if metadata else None)
        duration = (metadata.get("duration") if metadata else None)
        bitrate = (metadata.get("bitrate") if metadata else None)
        file_format = file.suffix.lower().lstrip(".")

        if existing:
            existing.title = title
            existing.episode_number = episode_number
            existing.duration = duration
            existing.file_format = file_format
            existing.bitrate = bitrate
            return False
        else:
            episode = PodcastEpisode(
                show_id=show.id,
                title=title,
                emoji="🎙️",
                episode_number=episode_number,
                file_path=relative,
                file_format=file_format,
                duration=duration,
                bitrate=bitrate,
            )
            self.db.add(episode)
            return True

    def _extract_metadata(self, file: Path) -> Optional[dict]:
        """Extract basic metadata using mutagen."""
        try:
            audio = MutagenFile(file)
            if audio is None:
                return None

            meta: dict = {}
            if hasattr(audio, "tags") and audio.tags:
                tags = audio.tags
                meta["title"] = self._get_tag(tags, ["title", "TIT2", "\xa9nam"])
                track_num = self._get_tag(tags, ["tracknumber", "TRCK", "trkn"])
                if track_num:
                    try:
                        meta["track_number"] = int(str(track_num).split("/")[0])
                    except ValueError:
                        pass

            if hasattr(audio.info, "length"):
                meta["duration"] = audio.info.length
            if hasattr(audio.info, "bitrate"):
                meta["bitrate"] = audio.info.bitrate

            return meta
        except Exception as e:
            logger.debug(f"Could not extract metadata from {file}: {e}")
            return None

    @staticmethod
    def _get_tag(tags, names: list) -> Optional[str]:
        for name in names:
            if name in tags:
                val = tags[name]
                if isinstance(val, list) and val:
                    return str(val[0])
                return str(val)
        return None

    @staticmethod
    def _dir_to_title(name: str) -> str:
        """Convert a directory name to a human-readable show title."""
        return name.replace("_", " ").replace("-", " ").strip()
