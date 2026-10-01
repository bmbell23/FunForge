"""Track model."""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from ..database import Base


class Track(Base):
    """Track model."""
    
    __tablename__ = "tracks"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    album_id = Column(Integer, ForeignKey("albums.id"), nullable=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)
    
    # Track metadata
    track_number = Column(Integer, nullable=True)
    disc_number = Column(Integer, default=1)
    duration = Column(Float, nullable=True)  # Duration in seconds
    
    # File information
    file_path = Column(String, nullable=False, unique=True)
    file_format = Column(String, nullable=True)
    bitrate = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    album = relationship("Album", back_populates="tracks")
    artist = relationship("Artist", back_populates="tracks")
    
    def __repr__(self):
        return f"<Track(id={self.id}, title='{self.title}')>"

