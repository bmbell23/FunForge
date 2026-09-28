"""Album model."""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base


class Album(Base):
    """Album model."""

    __tablename__ = "albums"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)
    album_artist = Column(String, nullable=True, index=True)  # Album artist for grouping
    year = Column(Integer, nullable=True)
    genre = Column(String, nullable=True)

    # Album art
    cover_art_path = Column(String, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    artist = relationship("Artist", back_populates="albums")
    tracks = relationship("Track", back_populates="album", cascade="all, delete-orphan", order_by="Track.track_number")

    def __repr__(self):
        return f"<Album(id={self.id}, title='{self.title}')>"

