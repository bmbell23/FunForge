"""PodcastEpisode model."""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base


class PodcastEpisode(Base):
    """A single podcast episode (audio file) belonging to a PodcastShow."""

    __tablename__ = "podcast_episodes"

    id = Column(Integer, primary_key=True, index=True)
    show_id = Column(Integer, ForeignKey("podcast_shows.id"), nullable=False)
    title = Column(String, nullable=False, index=True)
    emoji = Column(String, nullable=False, default="🎙️")
    episode_number = Column(Integer, nullable=True)

    # File information
    file_path = Column(String, nullable=False, unique=True)  # relative to podcast_dir
    file_format = Column(String, nullable=True)
    duration = Column(Float, nullable=True)  # seconds
    bitrate = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    show = relationship("PodcastShow", back_populates="episodes")

    def __repr__(self):
        return f"<PodcastEpisode(id={self.id}, title='{self.title}')>"
