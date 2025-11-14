#!/usr/bin/env python3
"""
Migration script to reorganize uploaded files into proper subfolders:
- MuseScore files (.mscz) -> <song_id>/mscz/
- Sheet PDFs -> <song_id>/sheets/ (if not already there)
- MP3s -> <song_id>/mp3s/ (already correct)
- MIDIs -> <song_id>/midis/ (already correct)
- Keep in root: tex, lyrics.pdf, chords.pdf
"""

import os
import sqlite3
import json
import shutil
from pathlib import Path

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'instance/songs.db')

def migrate_files():
    """Migrate files to new folder structure and update database"""
    
    # Connect to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all songs with file paths
    cursor.execute("""
        SELECT id, song_id, sheet_pdf_paths, sheet_mscz_paths 
        FROM song 
        WHERE sheet_pdf_paths IS NOT NULL OR sheet_mscz_paths IS NOT NULL
    """)
    
    songs = cursor.fetchall()
    
    total_songs = len(songs)
    updated_songs = 0
    moved_files = 0
    
    print(f"Found {total_songs} songs with files to check...")
    print()
    
    for song_id, song_code, sheet_pdf_paths_json, sheet_mscz_paths_json in songs:
        song_updated = False
        
        # Process MuseScore files
        if sheet_mscz_paths_json and sheet_mscz_paths_json != '[]':
            try:
                mscz_paths = json.loads(sheet_mscz_paths_json)
                new_mscz_paths = []
                
                for path in mscz_paths:
                    if not path:
                        continue
                    
                    old_path = os.path.join(UPLOAD_FOLDER, path)
                    
                    # Check if file is in root folder (not in mscz/ subfolder)
                    if '/mscz/' not in path and path.endswith('.mscz'):
                        # Construct new path: <song_id>/mscz/<filename>
                        parts = path.split('/')
                        if len(parts) == 2:  # Currently: <song_id>/file.mscz
                            song_folder = parts[0]
                            filename = parts[1]
                            new_path = f"{song_folder}/mscz/{filename}"
                            new_full_path = os.path.join(UPLOAD_FOLDER, new_path)
                            
                            # Create mscz subfolder if it doesn't exist
                            os.makedirs(os.path.dirname(new_full_path), exist_ok=True)
                            
                            # Move file if it exists
                            if os.path.exists(old_path):
                                print(f"  Moving: {path} -> {new_path}")
                                shutil.move(old_path, new_full_path)
                                new_mscz_paths.append(new_path)
                                moved_files += 1
                                song_updated = True
                            else:
                                print(f"  Warning: File not found: {old_path}")
                                new_mscz_paths.append(path)  # Keep old path
                        else:
                            new_mscz_paths.append(path)  # Already in correct structure
                    else:
                        new_mscz_paths.append(path)  # Already in mscz/ or not a mscz file
                
                # Update database if paths changed
                if song_updated:
                    cursor.execute(
                        "UPDATE song SET sheet_mscz_paths = ? WHERE id = ?",
                        (json.dumps(new_mscz_paths, ensure_ascii=False), song_id)
                    )
            
            except (json.JSONDecodeError, TypeError) as e:
                print(f"  Error processing MuseScore paths for song {song_code}: {e}")
        
        # Process Sheet PDFs (move to sheets/ if in root)
        if sheet_pdf_paths_json and sheet_pdf_paths_json != '[]':
            try:
                sheet_paths = json.loads(sheet_pdf_paths_json)
                new_sheet_paths = []
                
                for path in sheet_paths:
                    if not path:
                        continue
                    
                    old_path = os.path.join(UPLOAD_FOLDER, path)
                    
                    # Check if file is in root folder (not in sheets/ subfolder)
                    if '/sheets/' not in path and path.endswith('.pdf'):
                        # Construct new path: <song_id>/sheets/<filename>
                        parts = path.split('/')
                        if len(parts) == 2:  # Currently: <song_id>/file.pdf
                            song_folder = parts[0]
                            filename = parts[1]
                            
                            # Skip if this is lyrics.pdf or chords.pdf (they stay in root)
                            if filename in ['lyrics.pdf', 'chords.pdf']:
                                new_sheet_paths.append(path)
                                continue
                            
                            new_path = f"{song_folder}/sheets/{filename}"
                            new_full_path = os.path.join(UPLOAD_FOLDER, new_path)
                            
                            # Create sheets subfolder if it doesn't exist
                            os.makedirs(os.path.dirname(new_full_path), exist_ok=True)
                            
                            # Move file if it exists
                            if os.path.exists(old_path):
                                print(f"  Moving: {path} -> {new_path}")
                                shutil.move(old_path, new_full_path)
                                new_sheet_paths.append(new_path)
                                moved_files += 1
                                song_updated = True
                            else:
                                print(f"  Warning: File not found: {old_path}")
                                new_sheet_paths.append(path)  # Keep old path
                        else:
                            new_sheet_paths.append(path)  # Already in correct structure
                    else:
                        new_sheet_paths.append(path)  # Already in sheets/ or not a PDF
                
                # Update database if paths changed
                if song_updated:
                    cursor.execute(
                        "UPDATE song SET sheet_pdf_paths = ? WHERE id = ?",
                        (json.dumps(new_sheet_paths, ensure_ascii=False), song_id)
                    )
            
            except (json.JSONDecodeError, TypeError) as e:
                print(f"  Error processing sheet PDF paths for song {song_code}: {e}")
        
        if song_updated:
            updated_songs += 1
            print(f"  Updated song {song_code} (ID {song_id})")
    
    # Commit changes
    conn.commit()
    conn.close()
    
    print()
    print(f"✓ Migration complete!")
    print(f"  - Songs updated: {updated_songs}/{total_songs}")
    print(f"  - Files moved: {moved_files}")

if __name__ == '__main__':
    print("=" * 60)
    print("File Structure Migration")
    print("=" * 60)
    print()
    
    # Confirm before running
    response = input("This will reorganize files in the uploads folder. Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("Migration cancelled.")
        exit(0)
    
    print()
    migrate_files()
