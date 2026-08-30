"""PodcastShow model."""

from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base


class PodcastShow(Base):
    """A podcast show, corresponding to a directory under podcast_dir."""

    __tablename__ = "podcast_shows"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    directory_path = Column(String, nullable=False, unique=True)  # relative to podcast_dir
    cover_art_path = Column(String, nullable=True)
    description = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    episodes = relationship(
        "PodcastEpisode",
        back_populates="show",
        cascade="all, delete-orphan",
        order_by="PodcastEpisode.episode_number, PodcastEpisode.title",
    )

    def __repr__(self):
        return f"<PodcastShow(id={self.id}, title='{self.title}')>"
