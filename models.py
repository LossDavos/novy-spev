from flask_sqlalchemy import SQLAlchemy
from flask import Flask, render_template, request, send_file, redirect, url_for, current_app
from flask_sqlalchemy import SQLAlchemy
import os
import json
import subprocess
from tempfile import mkdtemp
from shutil import rmtree, copy2
from sqlalchemy import event
from pathlib import Path
from unidecode import unidecode
import re
import os
from sqlalchemy import UniqueConstraint
import logging

logging.basicConfig(level=logging.INFO)

db = SQLAlchemy()

class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    song_id = db.Column(db.String(10), nullable=False)  # Format: A-001
    version_name = db.Column(db.String(100), nullable=True)
    title = db.Column(db.String, nullable=False)
    author = db.Column(db.String, nullable=True)
    title_original = db.Column(db.String, nullable=True)
    author_original = db.Column(db.String, nullable=True)
    categories = db.Column(db.String, nullable=True)
    # Store alternative titles as comma-separated string
    alternative_titles = db.Column(db.String, nullable=True)
    song_parts = db.Column(db.Text, nullable=False)
    # checked = db.Column(db.Boolean, default=False)
    admin_checked = db.Column(db.Boolean, default=False)
    printed = db.Column(db.Boolean, default=False)

    pdf_lyrics_path = db.Column(db.String(200))
    pdf_chords_path = db.Column(db.String(200))
    tex_path = db.Column(db.String(200))

    midi_paths = db.Column(db.Text)  # stored as JSON list
    mp3_paths = db.Column(db.Text)
    sheet_pdf_paths = db.Column(db.Text)
    sheet_mscz_paths = db.Column(db.Text)

    # Add normalized search fields for fast searching
    search_text = db.Column(db.Text, nullable=True)  # Pre-normalized searchable text

    __table_args__ = (
        UniqueConstraint('song_id', 'version_name', name='uix_song_id_version'),
    )

    def update_search_text(self):
        """Update the normalized search text field"""
        def normalize_text(text):
            if not text:
                return ""
            # First remove chord brackets [C], [Am], [G7], etc. - replace with empty string to avoid splitting words
            text_no_chords = re.sub(r'\[[^\]]*\]', '', text)
            # Then normalize: remove diacritics, punctuation, normalize whitespace
            return unidecode(text_no_chords.lower()).replace(",", " ").replace(".", " ").replace("-", " ").replace("_", " ").replace(";", " ").strip()
        
        # Collect all searchable text
        parts = []
        
        # Basic song info
        parts.append(normalize_text(self.title or ""))
        parts.append(normalize_text(self.version_name or ""))
        parts.append(normalize_text(self.author or ""))
        parts.append(normalize_text(self.title_original or ""))
        parts.append(normalize_text(self.author_original or ""))
        
        # Alternative titles
        if self.alternative_titles:
            alt_titles = self.alternative_titles.split(';;')
            for alt_title in alt_titles:
                parts.append(normalize_text(alt_title))
        
        # Song parts (lyrics)
        if self.song_parts:
            try:
                song_data = json.loads(self.song_parts)
                for part in song_data:
                    if isinstance(part, dict) and 'lines' in part:
                        for line in part['lines']:
                            parts.append(normalize_text(line))
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Join all parts with spaces and normalize whitespace
        self.search_text = " ".join(filter(None, parts))
        self.search_text = re.sub(r'\s+', ' ', self.search_text).strip()
from sqlalchemy import event
from sqlalchemy.orm import Session
import json

def generate_song_id(mapper, connection, target):
    session = Session.object_session(target)
    with session.no_autoflush:
        normalized_title = unidecode(target.title).strip()
        if not normalized_title:
            logging.error("Missing title, cannot generate song_id")
            raise ValueError(f"Title {target.title} is required to generate song_id")

        letter = normalized_title[0].upper()
        # logger.debug(f"Normalized title: {normalized_title}, initial letter: {letter}")

        # Get all existing song_ids from DB
        all_ids = session.query(Song.song_id).all()
        # logger.debug(f"All song_ids from DB: {all_ids}")

        pattern = re.compile(f"^{re.escape(letter)}-\\d{{3}}$")
        # logger.debug(f"Regex pattern: {pattern.pattern}")

        # Filter and parse IDs that match the pattern
        existing_ids = [sid[0] for sid in all_ids if sid[0] and pattern.match(sid[0])]
        # logger.info(f"Existing IDs for letter {letter}: {existing_ids}")

        used_numbers = sorted([
            int(match.group(1))
            for sid in existing_ids
            if (match := re.search(r'-(\d{3})$', sid))
        ])
        # logger.info(f"Used numbers for {letter}: {used_numbers}")

        # Find first available number
        new_number = 1
        for num in sorted(set(used_numbers)):  # remove duplicates
            if num == new_number:
                new_number += 1
            else:
                break

        target.song_id = f"{letter}-{new_number:03d}"
        # logger.info(f"Assigned song_id: {target.song_id}")

def handle_song_update(mapper, connection, target):
    session = Session.object_session(target)
    state = db.inspect(target)

    if 'title' in state.attrs and state.attrs.title.history.has_changes():
        old_title = state.attrs.title.history.deleted[0]
        new_title = target.title

        # Normalize and compare initial letters
        old_initial = unidecode(old_title.strip())[0].upper() if old_title else ''
        new_initial = unidecode(new_title.strip())[0].upper() if new_title else ''

        # Only regenerate if initial letter changed
        if old_initial != new_initial:
            generate_song_id(mapper, connection, target)
        # If letter stayed the same but title changed, keep existing song_id

def update_search_text_listener(mapper, connection, target):
    """Update search text before inserting or updating"""
    target.update_search_text()

event.listen(Song, 'before_insert', generate_song_id)
event.listen(Song, 'before_update', handle_song_update)
event.listen(Song, 'before_insert', update_search_text_listener)
event.listen(Song, 'before_update', update_search_text_listener)

