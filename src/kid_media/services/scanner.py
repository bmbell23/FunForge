"""Music library scanner service."""

import os
import logging
import hashlib
from pathlib import Path
from typing import Optional, Set
from mutagen import File as MutagenFile
from sqlalchemy.orm import Session
from ..models import Album, Artist, Track
from ..config import settings

logger = logging.getLogger(__name__)

# Paths that the user has explicitly deleted — stored outside the container mount
# so they survive restarts. Paths are relative to music_dir (same as Track.file_path).
EXCLUDED_PATHS_FILE = Path("/app/data/excluded_paths.txt")


def get_excluded_paths() -> Set[str]:
    """Return the set of relative file paths that should never be re-scanned."""
    if EXCLUDED_PATHS_FILE.exists():
        return {line.strip() for line in EXCLUDED_PATHS_FILE.read_text().splitlines() if line.strip()}
    return set()


def add_excluded_paths(paths: list[str]) -> None:
    """Append relative file paths to the persistent exclusion list."""
    existing = get_excluded_paths()
    combined = existing | set(paths)
    EXCLUDED_PATHS_FILE.write_text('\n'.join(sorted(combined)) + '\n')


class MusicScanner:
    """Scans music directory and updates database."""

    def __init__(self, db: Session):
        self.db = db
        self.music_dir = Path(settings.music_dir)
        self.allowed_extensions = settings.allowed_audio_extensions.split(',')
        # Create covers directory if it doesn't exist
        self.covers_dir = Path("/app/src/kid_media/static/covers")
        self.covers_dir.mkdir(parents=True, exist_ok=True)

    def scan_library(self) -> dict:
        """Scan the entire music library and update database."""
        logger.info(f"Starting music library scan: {self.music_dir}")

        stats = {
            "files_scanned": 0,
            "tracks_added": 0,
            "tracks_updated": 0,
            "errors": 0,
            "excluded": 0,
        }

        if not self.music_dir.exists():
            logger.error(f"Music directory does not exist: {self.music_dir}")
            return stats

        excluded = get_excluded_paths()
        if excluded:
            logger.info(f"Skipping {len(excluded)} excluded path(s)")

        # Walk through all files in music directory
        for root, dirs, files in os.walk(self.music_dir):
            for filename in files:
                file_path = Path(root) / filename

                # Check if file has allowed extension
                if file_path.suffix.lower().lstrip('.') not in self.allowed_extensions:
                    continue

                # Skip user-deleted tracks
                relative_path = str(file_path.relative_to(self.music_dir))
                if relative_path in excluded:
                    stats["excluded"] += 1
                    continue

                stats["files_scanned"] += 1

                try:
                    # Process the audio file
                    if self._process_audio_file(file_path):
                        stats["tracks_added"] += 1
                    else:
                        stats["tracks_updated"] += 1
                    # Commit after each file to avoid rollback issues
                    self.db.commit()
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {e}")
                    stats["errors"] += 1
                    # Rollback the failed transaction
                    self.db.rollback()

        logger.info(f"Scan complete: {stats}")
        return stats

    def _process_audio_file(self, file_path: Path) -> bool:
        """Process a single audio file and add/update in database."""
        # Convert to relative path for storage
        relative_path = str(file_path.relative_to(self.music_dir))

        # Check if track already exists
        existing_track = self.db.query(Track).filter(
            Track.file_path == relative_path
        ).first()

        # Extract metadata
        metadata = self._extract_metadata(file_path)

        if not metadata:
            logger.warning(f"Could not extract metadata from {file_path}")
            return False

        # Get or create artist (handle None values)
        # Prefer album artist for grouping, fall back to artist
        album_artist_name = metadata.get("album_artist") or metadata.get("artist") or "Unknown Artist"
        artist_name = metadata.get("artist") or "Unknown Artist"
        artist = self._get_or_create_artist(artist_name)

        # Get or create album (handle None values)
        album_title = metadata.get("album") or "Unknown Album"
        album = self._get_or_create_album(
            title=album_title,
            artist=artist,
            album_artist=album_artist_name,
            year=metadata.get("year"),
            genre=metadata.get("genre"),
            cover_art_data=metadata.get("cover_art")
        )

        # Handle None values in metadata
        track_title = metadata.get("title") or file_path.stem
        disc_number = metadata.get("disc_number") or 1

        if existing_track:
            # Update existing track
            existing_track.title = track_title
            existing_track.album_id = album.id
            existing_track.artist_id = artist.id
            existing_track.track_number = metadata.get("track_number")
            existing_track.disc_number = disc_number
            existing_track.duration = metadata.get("duration")
            existing_track.file_format = file_path.suffix.lower().lstrip('.')
            existing_track.bitrate = metadata.get("bitrate")
            return False
        else:
            # Create new track
            track = Track(
                title=track_title,
                album_id=album.id,
                artist_id=artist.id,
                track_number=metadata.get("track_number"),
                disc_number=disc_number,
                duration=metadata.get("duration"),
                file_path=relative_path,
                file_format=file_path.suffix.lower().lstrip('.'),
                bitrate=metadata.get("bitrate")
            )
            self.db.add(track)
            return True

    def _extract_metadata(self, file_path: Path) -> Optional[dict]:
        """Extract metadata from audio file using mutagen."""
        try:
            audio = MutagenFile(file_path)
            if audio is None:
                return None

            metadata = {}

            # Extract common tags
            if hasattr(audio, 'tags') and audio.tags:
                tags = audio.tags

                # Title
                metadata["title"] = self._get_tag(tags, ["title", "TIT2", "\xa9nam"])

                # Artist
                metadata["artist"] = self._get_tag(tags, ["artist", "TPE1", "\xa9ART"])

                # Album Artist (preferred for grouping)
                metadata["album_artist"] = self._get_tag(tags, ["albumartist", "album artist", "TPE2", "aART"])

                # Album
                metadata["album"] = self._get_tag(tags, ["album", "TALB", "\xa9alb"])

                # Track number
                track_num = self._get_tag(tags, ["tracknumber", "TRCK", "trkn"])
                if track_num:
                    # Handle "1/10" format
                    metadata["track_number"] = int(str(track_num).split('/')[0])

                # Year
                year = self._get_tag(tags, ["date", "year", "TDRC", "\xa9day"])
                if year:
                    metadata["year"] = int(str(year)[:4])

                # Genre
                metadata["genre"] = self._get_tag(tags, ["genre", "TCON", "\xa9gen"])

                # Extract album cover art
                metadata["cover_art"] = self._extract_cover_art(audio, tags)

            # Duration
            if hasattr(audio.info, 'length'):
                metadata["duration"] = audio.info.length

            # Bitrate
            if hasattr(audio.info, 'bitrate'):
                metadata["bitrate"] = audio.info.bitrate

            return metadata

        except Exception as e:
            logger.error(f"Error extracting metadata from {file_path}: {e}")
            return None

    def _get_tag(self, tags, tag_names: list) -> Optional[str]:
        """Get tag value from multiple possible tag names."""
        for tag_name in tag_names:
            if tag_name in tags:
                value = tags[tag_name]
                if isinstance(value, list) and len(value) > 0:
                    return str(value[0])
                return str(value)
        return None

    def _get_or_create_artist(self, name: str) -> Artist:
        """Get existing artist or create new one."""
        artist = self.db.query(Artist).filter(Artist.name == name).first()
        if not artist:
            artist = Artist(name=name, sort_name=name)
            self.db.add(artist)
            self.db.flush()  # Get the ID
        return artist

    def _get_or_create_album(self, title: str, artist: Artist, album_artist: str = None, year: Optional[int] = None, genre: Optional[str] = None, cover_art_data: Optional[bytes] = None) -> Album:
        """Get existing album or create new one."""
        # Use album_artist for grouping
        album = self.db.query(Album).filter(
            Album.title == title,
            Album.album_artist == album_artist
        ).first()

        if not album:
            # Save cover art if provided
            cover_art_path = None
            if cover_art_data:
                cover_art_path = self._save_cover_art(cover_art_data, title, album_artist or artist.name)

            album = Album(
                title=title,
                artist_id=artist.id,
                album_artist=album_artist,
                year=year,
                genre=genre,
                cover_art_path=cover_art_path
            )
            self.db.add(album)
            self.db.flush()  # Get the ID
        elif cover_art_data and not album.cover_art_path:
            # Update existing album with cover art if it doesn't have one
            cover_art_path = self._save_cover_art(cover_art_data, title, artist.name)
            album.cover_art_path = cover_art_path

        return album

    def _extract_cover_art(self, audio, tags) -> Optional[bytes]:
        """Extract cover art from audio file."""
        try:
            # MP3 files (ID3 tags)
            if hasattr(tags, 'getall'):
                for key in tags.getall('APIC'):
                    return key.data

            # MP4/M4A files
            if 'covr' in tags:
                return bytes(tags['covr'][0])

            # FLAC files
            if hasattr(audio, 'pictures') and audio.pictures:
                return audio.pictures[0].data

            # OGG/Vorbis files
            if 'metadata_block_picture' in tags:
                import base64
                from mutagen.flac import Picture
                pic_data = base64.b64decode(tags['metadata_block_picture'][0])
                picture = Picture(pic_data)
                return picture.data

        except Exception as e:
            logger.debug(f"Could not extract cover art: {e}")

        return None

    def _save_cover_art(self, cover_data: bytes, album_title: str, artist_name: str) -> str:
        """Save cover art to static directory and return path."""
        try:
            # Create a unique filename based on album and artist
            unique_str = f"{artist_name}_{album_title}".encode('utf-8')
            filename = hashlib.md5(unique_str).hexdigest() + ".jpg"
            file_path = self.covers_dir / filename

            # Save the image
            with open(file_path, 'wb') as f:
                f.write(cover_data)

            # Return the web-accessible path
            return f"/static/covers/{filename}"
        except Exception as e:
            logger.error(f"Error saving cover art: {e}")
            return None

