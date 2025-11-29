#!/usr/bin/env python3
"""
Migration script to add created_at and last_modified columns to song table
"""

import os
import sys
import sqlite3
from datetime import datetime
from pathlib import Path
from models import db, Song
from app import app

def migrate_add_timestamp_columns():
    """Add created_at and last_modified columns to existing database"""
    db_path = 'instance/songs.db'
    
    if not os.path.exists(db_path):
        print("Database not found, creating new one...")
        with app.app_context():
            db.create_all()
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check and add created_at column
    try:
        cursor.execute("SELECT created_at FROM song LIMIT 1")
        print("created_at column already exists")
    except sqlite3.OperationalError:
        print("Adding created_at column to database...")
        cursor.execute("ALTER TABLE song ADD COLUMN created_at TIMESTAMP")
        conn.commit()
        print("created_at column added successfully")
    
    # Check and add last_modified column
    try:
        cursor.execute("SELECT last_modified FROM song LIMIT 1")
        print("last_modified column already exists")
    except sqlite3.OperationalError:
        print("Adding last_modified column to database...")
        cursor.execute("ALTER TABLE song ADD COLUMN last_modified TIMESTAMP")
        conn.commit()
        print("last_modified column added successfully")
    
    conn.close()

def get_folder_creation_time(song_id):
    """Get the creation time of the upload folder for a song"""
    upload_dir = Path('uploads') / str(song_id)
    
    if upload_dir.exists():
        # Get the modification time (closest to creation time on Linux)
        stat_info = upload_dir.stat()
        return datetime.fromtimestamp(stat_info.st_mtime)
    
    return None

def populate_timestamps():
    """Populate created_at and last_modified for all existing songs"""
    print("Populating timestamps for all songs...")
    
    with app.app_context():
        songs = Song.query.all()
        total = len(songs)
        updated = 0
        
        for i, song in enumerate(songs):
            # Get folder creation time
            folder_time = get_folder_creation_time(song.id)
            
            if folder_time:
                # Use folder creation time as both created_at and last_modified
                song.created_at = folder_time
                song.last_modified = folder_time
                updated += 1
            else:
                # Fallback to a default date if no folder exists
                # Using a date in the past to indicate unknown
                default_date = datetime(2024, 1, 1, 0, 0, 0)
                song.created_at = default_date
                song.last_modified = default_date
            
            if (i + 1) % 50 == 0:  # Progress indicator every 50 songs
                print(f"Processing song {i + 1}/{total} - ID {song.id}: {song.song_id} - {song.title}")
        
        db.session.commit()
        print(f"\nSuccessfully updated timestamps for {total} songs")
        print(f"  - {updated} songs with folder-based timestamps")
        print(f"  - {total - updated} songs with default timestamps (no folder found)")

if __name__ == '__main__':
    print("=" * 60)
    print("Starting timestamp migration...")
    print("=" * 60)
    
    migrate_add_timestamp_columns()
    populate_timestamps()
    
    print("=" * 60)
    print("Migration completed successfully!")
    print("=" * 60)
