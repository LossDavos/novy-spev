#!/usr/bin/env python3
"""
Migration script to add search_text column and populate it for existing songs
"""

import os
import sys
import sqlite3
from sqlalchemy import create_engine, text
from models import db, Song
from app import app
from unidecode import unidecode
import json
import re

def normalize_text(text_input):
    """Normalize text for searching - same logic as in models.py"""
    if not text_input:
        return ""
    # First remove chord brackets [C], [Am], [G7], etc. - replace with empty string to avoid splitting words
    text_no_chords = re.sub(r'\[[^\]]*\]', '', text_input)
    # Then normalize: remove diacritics, punctuation, normalize whitespace  
    return unidecode(text_no_chords.lower()).replace(",", " ").replace(".", " ").replace("-", " ").replace("_", " ").replace(";", " ").strip()

def migrate_add_search_column():
    """Add search_text column to existing database"""
    # Check if we need to add the column
    db_path = 'instance/songs.db'
    if not os.path.exists(db_path):
        print("Database not found, creating new one...")
        with app.app_context():
            db.create_all()
        return
    
    # Check if column already exists
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT search_text FROM song LIMIT 1")
        print("search_text column already exists")
    except sqlite3.OperationalError:
        print("Adding search_text column to database...")
        cursor.execute("ALTER TABLE song ADD COLUMN search_text TEXT")
        conn.commit()
        print("Column added successfully")
    
    conn.close()

def populate_search_text():
    """Populate search_text for all existing songs"""
    print("Populating search_text for all songs...")
    
    with app.app_context():
        songs = Song.query.all()
        total = len(songs)
        
        for i, song in enumerate(songs):
            # Collect all searchable text
            parts = []
            
            # Basic song info
            parts.append(normalize_text(song.title or ""))
            parts.append(normalize_text(song.version_name or ""))
            parts.append(normalize_text(song.author or ""))
            parts.append(normalize_text(song.title_original or ""))
            parts.append(normalize_text(song.author_original or ""))
            
            # Alternative titles
            if song.alternative_titles:
                alt_titles = song.alternative_titles.split(';;')
                for alt_title in alt_titles:
                    parts.append(normalize_text(alt_title))
            
            # Song parts (lyrics)
            if song.song_parts:
                try:
                    song_data = json.loads(song.song_parts)
                    for part in song_data:
                        if isinstance(part, dict) and 'lines' in part:
                            for line in part['lines']:
                                parts.append(normalize_text(line))
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Join all parts with spaces and normalize whitespace
            search_text = " ".join(filter(None, parts))
            search_text = re.sub(r'\s+', ' ', search_text).strip()
            
            song.search_text = search_text
            
            if i % 50 == 0:  # Progress indicator every 50 songs
                print(f"Processing song {i + 1}/{total} - {song.song_id}: {song.title}")
        
        db.session.commit()
        print(f"Successfully updated search_text for {total} songs")

if __name__ == '__main__':
    print("Starting search text migration...")
    migrate_add_search_column()
    populate_search_text()
    print("Migration completed!")
