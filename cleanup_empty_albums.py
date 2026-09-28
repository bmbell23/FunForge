#!/usr/bin/env python3
"""Script to clean up albums with no tracks."""

import sqlite3
import sys
from pathlib import Path

def cleanup_empty_albums(db_path: str):
    """Delete albums that have no associated tracks."""
    print(f"Cleaning up database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Find albums with no tracks
        cursor.execute("""
            SELECT albums.id, albums.title, albums.album_artist
            FROM albums
            LEFT JOIN tracks ON tracks.album_id = albums.id
            WHERE tracks.id IS NULL
        """)
        
        empty_albums = cursor.fetchall()
        
        if not empty_albums:
            print("✓ No empty albums found")
            return
        
        print(f"Found {len(empty_albums)} empty album(s):")
        for album_id, title, album_artist in empty_albums:
            print(f"  - ID {album_id}: {title} by {album_artist}")
        
        # Delete empty albums
        cursor.execute("""
            DELETE FROM albums
            WHERE id IN (
                SELECT albums.id
                FROM albums
                LEFT JOIN tracks ON tracks.album_id = albums.id
                WHERE tracks.id IS NULL
            )
        """)
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        print(f"✓ Deleted {deleted_count} empty album(s)")
        
    except Exception as e:
        print(f"✗ Cleanup failed: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    # Default database path
    db_path = "/home/brandon/projects/FunForge/data/fun_forge.db"
    
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    if not Path(db_path).exists():
        print(f"✗ Database not found: {db_path}")
        sys.exit(1)
    
    cleanup_empty_albums(db_path)

