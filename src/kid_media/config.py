"""Configuration settings for KidMedia."""

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    """Application settings."""

    # Database
    database_url: str = "sqlite:////app/data/kid_media.db"

    # Server
    host: str = "0.0.0.0"
    port: int = 8006

    # Application
    app_name: str = "KidMedia"
    app_url: str = "http://localhost:8006"

    # Media Settings
    music_dir: str = "/music"
    podcast_dir: str = "/podcasts"
    allowed_audio_extensions: str = "mp3,flac,m4a,aac,ogg,wav,wma"

    # Parent lock. Client-side by design — it keeps a toddler out of the delete
    # buttons, it is not an authentication boundary. Override with PARENT_PIN.
    parent_pin: str = "1234"

    # Scanning
    scan_on_startup: bool = True
    scan_interval_minutes: int = 60

    class Config:
        env_file = ".env"


settings = Settings()

