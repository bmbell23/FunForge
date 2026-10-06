"""Configuration settings for FunForge."""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Application settings."""

    # Database
    database_url: str = "sqlite:////app/data/fun_forge.db"

    # Server
    host: str = "0.0.0.0"
    port: int = 8006

    # Application
    app_name: str = "FunForge"
    app_url: str = "http://localhost:8006"

    # Media Settings
    music_dir: str = "/music"
    podcast_dir: str = "/podcasts"
    allowed_audio_extensions: str = "mp3,flac,m4a,aac,ogg,wav,wma"

    # Parent lock. Client-side by design — it keeps a toddler out of the delete
    # buttons, it is not an authentication boundary. Override with PARENT_PIN.
    parent_pin: str = "1234"

    # Everything the app writes lives under here: the DB, the cover art the
    # scanners extract, and the Immich face thumbnails. One directory means one
    # volume (one PVC on k3s), and nothing written at runtime sits in the code
    # tree, where a baked image would lose it on every restart (#35).
    data_dir: str = "/app/data"

    @property
    def covers_dir(self) -> Path:
        return Path(self.data_dir) / "covers"

    @property
    def faces_dir(self) -> Path:
        return Path(self.data_dir) / "faces"

    # Scanning
    scan_on_startup: bool = True
    scan_interval_minutes: int = 60

    class Config:
        env_file = ".env"
        # .env also carries keys for host-side scripts (IMMICH_* for
        # sync_immich_faces.py); forbidding them crash-loops the app (#29).
        extra = "ignore"


settings = Settings()

