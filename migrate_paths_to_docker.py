#!/usr/bin/env python3
"""
Database Path Migration Script for Environment Independence
=========================================================

This script migrates all file paths in the database from absolute paths
to RELATIVE paths, making them work in any environment (Docker, PythonAnywhere, local).

Changes:
- /home/Davos/novy_spev/static/uploads/123/file.tex -> static/uploads/123/file.tex
- /home/david/Desktop/Zbor/novy_spev/static/uploads/456/file.pdf -> static/uploads/456/file.pdf
- /app/static/uploads/789/file.tex -> static/uploads/789/file.tex

This way paths work in:
- Docker container (/app/ + relative path)
- PythonAnywhere (/home/username/mysite/ + relative path) 
- Local development (/path/to/project/ + relative path)

Affected fields:
- tex_path
- pdf_lyrics_path  
- pdf_chords_path
"""

import sqlite3
import os
import json
from datetime import datetime

# Database configuration
DB_PATH = 'instance/songs.db'
BACKUP_DIR = 'instance/backups'

# Path prefixes to remove (convert absolute paths to relative)
ABSOLUTE_PATH_PREFIXES = [
    '/home/Davos/novy_spev/',
    '/home/david/Desktop/Zbor/novy_spev/',
    '/app/',
    # Add more absolute prefixes as needed
]

def create_backup():
    """Create a backup of the database before migration"""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f'songs_backup_before_path_migration_{timestamp}.db')
    
    # Copy database file
    import shutil
    shutil.copy2(DB_PATH, backup_path)
    
    print(f"✅ Database backup created: {backup_path}")
    return backup_path

def migrate_path(original_path):
    """Convert absolute path to relative path"""
    if not original_path:
        return original_path
    
    # Check if path is already relative
    if not original_path.startswith('/'):
        return original_path
    
    # Remove known absolute prefixes to make path relative
    for prefix in ABSOLUTE_PATH_PREFIXES:
        if original_path.startswith(prefix):
            relative_path = original_path[len(prefix):]
            # Ensure it starts with static/ or another expected relative path
            if relative_path.startswith('static/') or relative_path.startswith('uploads/'):
                return relative_path
            # If it's just the filename, prepend static/uploads/
            elif '/' not in relative_path:
                return f'static/uploads/{relative_path}'
            else:
                return relative_path
    
    # If no known prefix found, try to extract relative part
    # Look for patterns like /*/static/uploads/ or /*/uploads/
    if '/static/uploads/' in original_path:
        return 'static/uploads/' + original_path.split('/static/uploads/', 1)[1]
    elif '/uploads/' in original_path:
        return 'static/uploads/' + original_path.split('/uploads/', 1)[1]
    
    # If all else fails, return original path (might need manual review)
    return original_path

def analyze_paths(cursor):
    """Analyze current paths in the database"""
    print("\n📊 Analyzing current paths in database...")
    
    # Check tex_path
    cursor.execute("SELECT COUNT(*) FROM song WHERE tex_path IS NOT NULL AND tex_path != ''")
    tex_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM song WHERE pdf_lyrics_path IS NOT NULL AND pdf_lyrics_path != ''")
    pdf_lyrics_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM song WHERE pdf_chords_path IS NOT NULL AND pdf_chords_path != ''")
    pdf_chords_count = cursor.fetchone()[0]
    
    print(f"   TeX files: {tex_count} records")
    print(f"   PDF lyrics files: {pdf_lyrics_count} records") 
    print(f"   PDF chords files: {pdf_chords_count} records")
    
    # Sample paths for each type
    print("\n📁 Sample current paths:")
    
    cursor.execute("SELECT tex_path FROM song WHERE tex_path IS NOT NULL AND tex_path != '' LIMIT 3")
    tex_samples = cursor.fetchall()
    if tex_samples:
        print("   TeX paths:")
        for (path,) in tex_samples:
            print(f"     {path}")
    
    cursor.execute("SELECT pdf_lyrics_path FROM song WHERE pdf_lyrics_path IS NOT NULL AND pdf_lyrics_path != '' LIMIT 3") 
    pdf_samples = cursor.fetchall()
    if pdf_samples:
        print("   PDF paths:")
        for (path,) in pdf_samples:
            print(f"     {path}")

def preview_migration(cursor):
    """Preview what changes will be made"""
    print("\n🔍 Migration preview:")
    
    changes = []
    
    # Check all path fields
    for field in ['tex_path', 'pdf_lyrics_path', 'pdf_chords_path']:
        cursor.execute(f"SELECT id, song_id, {field} FROM song WHERE {field} IS NOT NULL AND {field} != ''")
        records = cursor.fetchall()
        
        for song_id_pk, song_id, original_path in records:
            new_path = migrate_path(original_path)
            if new_path != original_path:
                changes.append({
                    'song_id_pk': song_id_pk,
                    'song_id': song_id, 
                    'field': field,
                    'old_path': original_path,
                    'new_path': new_path
                })
    
    if changes:
        print(f"   📝 {len(changes)} paths will be updated:")
        for change in changes[:10]:  # Show first 10 changes
            print(f"     {change['song_id']} ({change['field']}): ")
            print(f"       FROM: {change['old_path']}")
            print(f"       TO:   {change['new_path']}")
        
        if len(changes) > 10:
            print(f"     ... and {len(changes) - 10} more changes")
    else:
        print("   ✅ No paths need to be updated")
    
    return changes

def perform_migration(cursor, changes):
    """Perform the actual migration"""
    print(f"\n🔄 Starting migration of {len(changes)} paths...")
    
    updated_count = 0
    
    for change in changes:
        try:
            query = f"UPDATE song SET {change['field']} = ? WHERE id = ?"
            cursor.execute(query, (change['new_path'], change['song_id_pk']))
            updated_count += 1
            
            if updated_count % 10 == 0:
                print(f"   ✅ Updated {updated_count}/{len(changes)} paths...")
                
        except Exception as e:
            print(f"   ❌ Error updating song {change['song_id']} {change['field']}: {e}")
    
    print(f"✅ Migration completed! Updated {updated_count} paths")
    return updated_count

def verify_migration(cursor):
    """Verify the migration was successful"""
    print("\n🔍 Verifying migration...")
    
    # Check for any remaining old paths
    old_path_found = False
    
    for prefix in ABSOLUTE_PATH_PREFIXES:
        for field in ['tex_path', 'pdf_lyrics_path', 'pdf_chords_path']:
            cursor.execute(f"SELECT COUNT(*) FROM song WHERE {field} LIKE ?", (f'{prefix}%',))
            count = cursor.fetchone()[0]
            
            if count > 0:
                print(f"   ⚠️  Found {count} records with absolute path in {field}: {prefix}")
                old_path_found = True
    
    if not old_path_found:
        print("   ✅ All paths successfully migrated to relative format")
    
    # Show sample of new paths
    print("\n📁 Sample migrated paths:")
    cursor.execute("SELECT tex_path FROM song WHERE tex_path IS NOT NULL AND tex_path NOT LIKE '/%' LIMIT 3")
    new_samples = cursor.fetchall()
    if new_samples:
        for (path,) in new_samples:
            print(f"     {path}")

def main():
    """Main migration function"""
    print("🌍 Universal Path Migration Script")
    print("=" * 50)
    
    # Check if database exists
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found: {DB_PATH}")
        print("   Make sure you're running this from the novy_spev directory")
        return
    
    # Create backup
    backup_path = create_backup()
    
    try:
        # Connect to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Analyze current state
        analyze_paths(cursor)
        
        # Preview changes
        changes = preview_migration(cursor)
        
        if not changes:
            print("\n✅ No migration needed - all paths are already relative")
            return
        
        # Confirm migration
        print(f"\n⚠️  This will update {len(changes)} file paths in the database")
        print("   A backup has been created at:", backup_path)
        
        response = input("\nProceed with migration? (y/N): ").strip().lower()
        
        if response == 'y':
            # Perform migration
            updated_count = perform_migration(cursor, changes)
            
            # Commit changes
            conn.commit()
            print("💾 Changes committed to database")
            
            # Verify migration
            verify_migration(cursor)
            
            print(f"\n🎉 Migration completed successfully!")
            print(f"   Updated {updated_count} file paths")
            print(f"   Backup available at: {backup_path}")
            
        else:
            print("❌ Migration cancelled")
    
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        print(f"   Database backup is available at: {backup_path}")
        
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    main()
