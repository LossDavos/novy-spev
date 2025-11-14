#!/usr/bin/env python3
"""
Migrate files to organized folder structure:
- MuseScore files (.mscz) -> <song_id>/mscz/
- Sheet PDFs -> <song_id>/sheets/
- Keep in root: .tex, lyrics.pdf, lyrics_chords.pdf
"""

import os
import sqlite3
import json
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'instance/songs.db')

def migrate_files():
    """Move files to organized subfolders"""
    moved_count = 0
    
    # Get all song folders
    for song_folder in os.listdir(UPLOADS_DIR):
        song_path = os.path.join(UPLOADS_DIR, song_folder)
        
        # Skip if not a directory or not a numeric song ID
        if not os.path.isdir(song_path):
            continue
        
        print(f"\nProcessing song folder: {song_folder}")
        
        # Get all files in the root of song folder
        for filename in os.listdir(song_path):
            file_path = os.path.join(song_path, filename)
            
            # Skip if it's a directory
            if os.path.isdir(file_path):
                continue
            
            # Skip files that should stay in root
            if filename in ['lyrics.pdf', 'lyrics_chords.pdf'] or filename.endswith('.tex'):
                print(f"  Keeping in root: {filename}")
                continue
            
            # Move MuseScore files to mscz/ subfolder
            if filename.endswith('.mscz'):
                mscz_folder = os.path.join(song_path, 'mscz')
                os.makedirs(mscz_folder, exist_ok=True)
                
                new_path = os.path.join(mscz_folder, filename)
                if not os.path.exists(new_path):
                    print(f"  Moving MuseScore: {filename} -> mscz/{filename}")
                    shutil.move(file_path, new_path)
                    moved_count += 1
                else:
                    print(f"  WARNING: {filename} already exists in mscz/, skipping")
            
            # Move PDF files (that are not lyrics/chords) to sheets/ subfolder
            elif filename.endswith('.pdf'):
                sheets_folder = os.path.join(song_path, 'sheets')
                os.makedirs(sheets_folder, exist_ok=True)
                
                new_path = os.path.join(sheets_folder, filename)
                if not os.path.exists(new_path):
                    print(f"  Moving sheet PDF: {filename} -> sheets/{filename}")
                    shutil.move(file_path, new_path)
                    moved_count += 1
                else:
                    print(f"  WARNING: {filename} already exists in sheets/, skipping")
    
    print(f"\n✓ Moved {moved_count} files to organized subfolders")
    return moved_count

def update_database_paths():
    """Update database paths to new relative format"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all songs with file paths
    cursor.execute("""
        SELECT id, song_id, sheet_pdf_paths, sheet_mscz_paths 
        FROM song 
        WHERE sheet_pdf_paths IS NOT NULL OR sheet_mscz_paths IS NOT NULL
    """)
    
    updated_count = 0
    
    for row in cursor.fetchall():
        song_db_id, song_id, sheet_pdf_paths_json, sheet_mscz_paths_json = row
        updated = False
        
        # Update sheet PDF paths
        if sheet_pdf_paths_json and sheet_pdf_paths_json != '[]':
            sheet_paths = json.loads(sheet_pdf_paths_json)
            new_paths = []
            
            for path in sheet_paths:
                # Extract just the filename
                filename = os.path.basename(path)
                
                # Create new relative path with sheets/ subfolder
                new_path = f"{song_db_id}/sheets/{filename}"
                new_paths.append(new_path)
                print(f"Song {song_id} (ID {song_db_id}): Sheet PDF {filename} -> {new_path}")
            
            if new_paths != sheet_paths:
                cursor.execute(
                    "UPDATE song SET sheet_pdf_paths = ? WHERE id = ?",
                    (json.dumps(new_paths, ensure_ascii=False), song_db_id)
                )
                updated = True
        
        # Update MuseScore paths
        if sheet_mscz_paths_json and sheet_mscz_paths_json != '[]':
            mscz_paths = json.loads(sheet_mscz_paths_json)
            new_paths = []
            
            for path in mscz_paths:
                # Extract just the filename
                filename = os.path.basename(path)
                
                # Create new relative path with mscz/ subfolder
                new_path = f"{song_db_id}/mscz/{filename}"
                new_paths.append(new_path)
                print(f"Song {song_id} (ID {song_db_id}): MuseScore {filename} -> {new_path}")
            
            if new_paths != mscz_paths:
                cursor.execute(
                    "UPDATE song SET sheet_mscz_paths = ? WHERE id = ?",
                    (json.dumps(new_paths, ensure_ascii=False), song_db_id)
                )
                updated = True
        
        if updated:
            updated_count += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n✓ Updated {updated_count} songs in database")
    return updated_count

if __name__ == '__main__':
    print("=" * 70)
    print("MIGRATING FILES TO ORGANIZED FOLDER STRUCTURE")
    print("=" * 70)
    
    # Step 1: Move files
    print("\n[1/2] Moving files to organized subfolders...")
    files_moved = migrate_files()
    
    # Step 2: Update database
    print("\n[2/2] Updating database paths...")
    songs_updated = update_database_paths()
    
    print("\n" + "=" * 70)
    print("MIGRATION COMPLETE")
    print("=" * 70)
    print(f"Files moved: {files_moved}")
    print(f"Songs updated in DB: {songs_updated}")
    print("\nNew folder structure:")
    print("  <song_id>/")
    print("    ├── mscz/          (MuseScore files)")
    print("    ├── sheets/        (Sheet PDFs)")
    print("    ├── mp3s/          (MP3 files)")
    print("    ├── midis/         (MIDI files)")
    print("    ├── *.tex          (TeX files - in root)")
    print("    ├── lyrics.pdf     (Lyrics PDF - in root)")
    print("    └── lyrics_chords.pdf (Lyrics+chords PDF - in root)")
