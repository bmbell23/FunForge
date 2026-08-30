#!/usr/bin/env python3
"""Migration script to add album_artist column to albums table."""

import sqlite3
import sys
from pathlib import Path

def migrate_database(db_path: str):
    """Add album_artist column to albums table."""
    print(f"Migrating database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(albums)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'album_artist' in columns:
            print("✓ album_artist column already exists")
            return
        
        # Add the column
        print("Adding album_artist column...")
        cursor.execute("ALTER TABLE albums ADD COLUMN album_artist VARCHAR")
        
        # Populate album_artist from artist names
        print("Populating album_artist from existing artist data...")
        cursor.execute("""
            UPDATE albums
            SET album_artist = (
                SELECT name FROM artists WHERE artists.id = albums.artist_id
            )
            WHERE artist_id IS NOT NULL
        """)
        
        conn.commit()
        print("✓ Migration complete!")
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    # Default database path
    db_path = "/home/brandon/projects/KidMedia/data/kid_media.db"
    
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    if not Path(db_path).exists():
        print(f"✗ Database not found: {db_path}")
        sys.exit(1)
    
    migrate_database(db_path)

