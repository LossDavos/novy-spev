#!/usr/bin/env python3
"""
Migration script to add song_key column to song table
"""

import os
import sqlite3
from models import db
from app import app


def migrate_add_song_key_column():
    """Add song_key column to existing database"""
    db_path = 'instance/songs.db'

    if not os.path.exists(db_path):
        print("Database not found, creating new one...")
        with app.app_context():
            db.create_all()
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT song_key FROM song LIMIT 1")
        print("song_key column already exists")
    except sqlite3.OperationalError:
        print("Adding song_key column to database...")
        cursor.execute("ALTER TABLE song ADD COLUMN song_key TEXT")
        conn.commit()
        print("song_key column added successfully")

    conn.close()


if __name__ == '__main__':
    print("=" * 60)
    print("Starting song_key migration...")
    print("=" * 60)
    migrate_add_song_key_column()
    print("=" * 60)
    print("Migration completed successfully!")
    print("=" * 60)
