import os
import json
import shutil
import subprocess
import tempfile
import boto3
from dotenv import load_dotenv

from flask import Flask, request, redirect, render_template, url_for, flash, jsonify
from models import db, Song
from werkzeug.utils import secure_filename
from datetime import datetime
import sqlite3
from markupsafe import Markup
import re
from sqlalchemy import case
from pathlib import Path
from generate_tex import generate_latex_content
from stamper import stamp_pdf

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# S3 config
load_dotenv(os.path.join(BASE_DIR, '.env'))

S3_BUCKET = os.getenv("S3_BUCKET")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
DELETE_SONG_PASSWORD = os.getenv("DELETE_SONG_PASSWORD", "DELETE_SONG_2024")  # Default fallback



# Use session for explicit region and signature
session = boto3.session.Session(
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

s3 = session.client(
    "s3",
    config=boto3.session.Config(
        s3={'addressing_style': 'virtual'},
        signature_version='s3v4'
    )
)


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///songs.db'
app.config['UPLOAD_FOLDER'] = f'{BASE_DIR}/static/uploads'
app.secret_key = 'your-secret-key-here'
ALLOWED_EXTENSIONS = {'mp3', 'pdf', 'midi', 'mid', 'tex', 'mscz'}
JSON_FOLDER = 'songs'
BACKUP_FOLDER = BASE_DIR + '/instance/backups'

db.init_app(app)

# Ensure upload and backup folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)
os.makedirs(JSON_FOLDER, exist_ok=True)

# Helper functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_to_s3(file, folder='mp3s'):
    try:
        filename = secure_filename(file.filename)
        key = f"{folder}/{filename}"
        s3.upload_fileobj(file, S3_BUCKET, key, ExtraArgs={'ContentType': file.content_type})
        return key
    except Exception as e:
        flash(f"S3 upload error: {e}")

        return None

def stamp_uploaded_pdf(pdf_path, song_id, version_name=None):
    """
    Stamp a PDF file with song ID and version name

    Args:
        pdf_path: Path to the PDF file to stamp
        song_id: Song ID to use for stamping
        version_name: Optional version name

    Returns:
        tuple: (success: bool, path: str, error_message: str)
               - success: True if stamping succeeded, False otherwise
               - path: Path to the stamped PDF if successful, original path if failed
               - error_message: Error message if stamping failed, empty string if successful
    """
    print(f"[DEBUG] stamp_uploaded_pdf called with:")
    print(f"  pdf_path: {pdf_path}")
    print(f"  song_id: {song_id}")
    print(f"  version_name: {version_name}")

    try:
        if not pdf_path:
            error_msg = "No PDF path provided"
            print(f"[DEBUG] {error_msg}")
            return False, pdf_path, error_msg

        if not os.path.exists(pdf_path):
            error_msg = f"PDF file does not exist: {pdf_path}"
            print(f"[DEBUG] {error_msg}")
            return False, pdf_path, error_msg

        print(f"[DEBUG] PDF file exists, proceeding with stamping")

        # Create stamped filename
        base_dir = os.path.dirname(pdf_path)
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        stamped_path = os.path.join(base_dir, f"{base_name}_stamped.pdf")

        print(f"[DEBUG] Stamped path will be: {stamped_path}")


        # Check if required dependencies are available
        try:
            from PyPDF2 import PdfReader, PdfWriter
            from reportlab.pdfgen import canvas
        except ImportError as ie:
            error_msg = f"Required PDF libraries not installed: {str(ie)}"
            print(f"[DEBUG] {error_msg}")
            return False, pdf_path, error_msg

        # Stamp the PDF
        print(f"[DEBUG] Calling stamp_pdf function...")
        stamp_result = stamp_pdf(pdf_path, stamped_path, song_id, version_name)
        print(f"[DEBUG] stamp_pdf returned: {stamp_result}")

        if stamp_result:
            print(f"[DEBUG] Stamping successful, replacing original file")
            # Check if stamped file was created
            if os.path.exists(stamped_path):
                print(f"[DEBUG] Stamped file exists, replacing original")
                os.remove(pdf_path)
                os.rename(stamped_path, pdf_path)
                print(f"[DEBUG] File replacement completed")
                return True, pdf_path, ""
            else:
                error_msg = f"Stamped file was not created at {stamped_path}"
                print(f"[DEBUG] ERROR: {error_msg}")
                return False, pdf_path, error_msg
        else:
            error_msg = "PDF stamping function returned False - check stamper.py logs for details"
            print(f"[DEBUG] {error_msg}")
            return False, pdf_path, error_msg

    except Exception as e:
        error_msg = f"Exception during PDF stamping: {str(e)}"
        print(f"[DEBUG] {error_msg}")
        import traceback
        traceback.print_exc()
        return False, pdf_path, error_msg

# Routes


# Routes

@app.route('/song/<int:song_id>/stamp_pdf', methods=['POST'])
def stamp_existing_pdf(song_id):
    """
    Manually stamp an existing PDF file for a song
    """
    song = Song.query.get_or_404(song_id)
    pdf_paths = json.loads(song.sheet_pdf_paths or '[]')  # For sheet_pdfs

    try:
        for path in pdf_paths:


            if path and os.path.exists(path):
                stamp_uploaded_pdf(path, song.song_id, song.version_name)
                flash(f"PDF stamped successfully!")
            else:
                flash("PDF file not found!")

    except Exception as e:
        flash(f"Error stamping PDF: {str(e)}")

    return redirect(url_for('song_detail', song_id=song.id))


@app.route('/song/<int:song_id>/download_sheet/<path:sheet_filename>')
def download_original_sheet(song_id, sheet_filename):
    """
    Download original PDF sheet without stamp
    """
    song = Song.query.get_or_404(song_id)
    
    # Check if file exists in current upload structure
    current_file_path = os.path.join(app.config['UPLOAD_FOLDER'], str(song.id), sheet_filename)
    
    if not os.path.exists(current_file_path):
        flash("Sheet PDF not found!", "error")
        return redirect(url_for('song_detail', song_id=song.id))
    
    # Check if we have an original version stored
    base_name = os.path.splitext(current_file_path)[0]
    original_path = base_name + '_original.pdf'
    
    if os.path.exists(original_path):
        # Return the original version
        return redirect(url_for('static', filename=f'uploads/{song.id}/{os.path.basename(original_path)}'))
    else:
        # Return the current file (might already be stamped)
        return redirect(url_for('static', filename=f'uploads/{song.id}/{sheet_filename}'))


@app.route('/song/<int:song_id>/download_stamped_sheet/<path:sheet_filename>')
def download_stamped_sheet(song_id, sheet_filename):
    """
    Download PDF sheet with stamp - generated on-the-fly without storing
    """
    from flask import send_file
    import io
    
    song = Song.query.get_or_404(song_id)
    
    # Check if file exists in current upload structure
    current_file_path = os.path.join(app.config['UPLOAD_FOLDER'], str(song.id), sheet_filename)
    
    if not os.path.exists(current_file_path):
        flash("Sheet PDF not found!", "error")
        return redirect(url_for('song_detail', song_id=song.id))
    
    try:
        # Create stamped version in memory using temporary file
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_stamped:
            font_path = os.path.join(os.path.dirname(__file__), 'static', 'fonts')
            success = stamp_pdf(current_file_path, temp_stamped.name, song.song_id, song.version_name, font_path)
            
            if not success:
                flash("Failed to create stamped version", "error")
                return redirect(url_for('song_detail', song_id=song.id))
            
            # Read the stamped PDF into memory
            with open(temp_stamped.name, 'rb') as f:
                pdf_data = f.read()
            
            # Clean up temp file
            os.unlink(temp_stamped.name)
            
            # Create filename for download
            base_name = os.path.splitext(sheet_filename)[0]
            stamped_filename = f"{base_name}_stamped.pdf"
            
            # Serve from memory
            return send_file(
                io.BytesIO(pdf_data),
                as_attachment=True,
                download_name=stamped_filename,
                mimetype='application/pdf'
            )
        
    except Exception as e:
        flash(f"Error creating stamped version: {str(e)}", "error")
        return redirect(url_for('song_detail', song_id=song.id))


@app.route('/song/<int:song_id>/download_blank_stamped')
def download_blank_stamped(song_id):
    """
    Download blank page with stamp only - generated on-the-fly without storing
    """
    from flask import send_file
    import io
    
    song = Song.query.get_or_404(song_id)
    
    try:
        # Path to blank PDF in the project directory
        blank_pdf_path = os.path.join(os.path.dirname(__file__), 'blank.pdf')
        
        if not os.path.exists(blank_pdf_path):
            flash("Blank PDF template not found!", "error")
            return redirect(url_for('song_detail', song_id=song.id))
        
        # Create stamped blank version in memory using temporary file
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_stamped:
            font_path = os.path.join(os.path.dirname(__file__), 'static', 'fonts')
            success = stamp_pdf(blank_pdf_path, temp_stamped.name, song.song_id, song.version_name, font_path)
            
            if not success:
                flash("Failed to create blank stamped version", "error")
                return redirect(url_for('song_detail', song_id=song.id))
            
            # Read the stamped PDF into memory
            with open(temp_stamped.name, 'rb') as f:
                pdf_data = f.read()
            
            # Clean up temp file
            os.unlink(temp_stamped.name)
            
            # Create filename for download
            blank_filename = f"{song.song_id}_blank_stamped.pdf"
            
            # Serve from memory
            return send_file(
                io.BytesIO(pdf_data),
                as_attachment=True,
                download_name=blank_filename,
                mimetype='application/pdf'
            )
        
    except Exception as e:
        flash(f"Error creating blank stamped version: {str(e)}", "error")
        return redirect(url_for('song_detail', song_id=song.id))


@app.template_filter('presigned_url')
def presigned_url_filter(key, expires_in=3600):
    """
    Usage in Jinja template:
        <a href="{{ 'uploads/44/adeste_hlasy.mid' | presigned_url }}">Download</a>
    """

    try:
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key},
            ExpiresIn=expires_in
        )
        return Markup(url)
    except Exception as e:
        # Optional: return empty string or a placeholder URL if key not found
        print(f"Error generating presigned URL for {key}: {e}")
        return ""

@app.route('/api/presigned_url')
def get_presigned_url():
    """API endpoint to get presigned URL for S3 files"""
    try:
        key = request.args.get('key')
        if not key:
            return jsonify({'error': 'Missing key parameter'}), 400
        
        expires_in = int(request.args.get('expires_in', 3600))
        
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': S3_BUCKET, 'Key': key},
            ExpiresIn=expires_in
        )
        
        return redirect(url)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def delete_from_s3(s3_key):
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        return True
    except Exception as e:
        flash(f"S3 delete error: {e}, {AWS_SECRET_ACCESS_KEY}")
        return False

def update_multi_files_s3(current_paths, new_files, folder='mp3s'):
    paths = json.loads(current_paths or '[]')

    # Upload new files to S3
    for file in new_files:
        if file and allowed_file(file.filename):
            key = upload_to_s3(file, folder=folder)
            if key:
                paths.append(key)
            else:
                flash(f"S3 upload error: {file}")


    return json.dumps(paths, ensure_ascii=False)


def get_song_upload_folder(song_id):
    """Create song-specific upload folder path"""
    song_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(song_id))
    os.makedirs(song_folder, exist_ok=True)
    return song_folder

def backup_db(src_path, backup_folder):
    """Create database backup"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_folder, f'backup_{timestamp}.db')

    src = sqlite3.connect(src_path)
    dest = sqlite3.connect(backup_path)
    with dest:
        src.backup(dest)
    dest.close()
    src.close()
    return backup_path

def delete_song_files(song_id):
    """Delete all files associated with a song - both local and S3 files"""
    # Get song data first to access S3 file paths
    song = Song.query.get(song_id)
    
    if song:
        # Delete S3 files (MP3s and MIDIs)
        try:
            # Delete MP3 files from S3
            if song.mp3_paths:
                mp3_paths = json.loads(song.mp3_paths)
                for s3_key in mp3_paths:
                    if s3_key:  # Make sure the key is not empty
                        print(f"Deleting S3 MP3 file: {s3_key}")
                        delete_from_s3(s3_key)
            
            # Delete MIDI files from S3
            if song.midi_paths:
                midi_paths = json.loads(song.midi_paths)
                for s3_key in midi_paths:
                    if s3_key:  # Make sure the key is not empty
                        print(f"Deleting S3 MIDI file: {s3_key}")
                        delete_from_s3(s3_key)
                        
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Error parsing S3 file paths for song {song_id}: {e}")
        except Exception as e:
            print(f"Error deleting S3 files for song {song_id}: {e}")
    
    # Delete local files (sheet PDFs, MuseScore files, TeX, generated PDFs, etc.)
    song_folder = os.path.join(app.config['UPLOAD_FOLDER'], str(song_id))
    if os.path.exists(song_folder):
        print(f"Deleting local song folder: {song_folder}")
        shutil.rmtree(song_folder)

# Routes

@app.route('/song/<int:song_id>/generate_tex', methods=['POST'])
def generate_tex(song_id):
    song = Song.query.get_or_404(song_id)

    # Get save folder
    folder = get_song_upload_folder(song.id)
    os.makedirs(folder, exist_ok=True)

    # Determine filename
    tex_filename = f"{secure_filename(song.song_id or song.title)}.tex"
    tex_path = os.path.join(folder, tex_filename)

    # Prepare LaTeX content
    latex = generate_latex_content(song)

    # Write to .tex file
    with open(tex_path, 'w', encoding='utf-8') as f:
        f.write(latex)

    # Save path in DB (convert to relative path for cross-environment compatibility)
    song.tex_path = os.path.relpath(tex_path, BASE_DIR)
    db.session.commit()

    flash("TeX file generated successfully!", "success")
    return redirect(url_for('song_view', song_id=song_id))


@app.route('/')
def index():
    # Load only first batch of songs for initial page load
    initial_batch_size = 50
    songs_query = Song.query.order_by(Song.song_id).limit(initial_batch_size).all()
    total_songs = Song.query.count()
    
    # Calculate full database statistics
    total_admin_checked = Song.query.filter(Song.admin_checked == True).count()
    total_printed = Song.query.filter(Song.printed == True).count()
    
    # Calculate category counts for the entire database
    categories = [
        "stále omšové spevy", "úvod", "medzispevy (žalmy; aleluja)", "obetovanie",
        "prijímanie", "poďakovanie po prijímaní", "záver", "adorácia", "advent",
        "vianoce", "pôst", "veľká noc", "cez rok", "k Duchu Svätému", "mariánske",
        "k svätcom", "detské", "iné", "liturgia hodín", "sobášne", "Taizé",
        "krížová cesta", "nevhodné"
    ]
    
    category_counts = {}
    all_songs = Song.query.all()  # For category counting - could be optimized with raw SQL
    for category in categories:
        count = 0
        for song in all_songs:
            if song.categories and category.lower() in song.categories.lower():
                count += 1
        category_counts[category] = count
    
    # Convert Song objects to JSON-serializable dictionaries
    songs_data = []
    for song in songs_query:
        # Parse file paths safely
        mp3_paths = []
        sheet_pdf_paths = []
        try:
            mp3_paths = json.loads(song.mp3_paths or '[]')
            sheet_pdf_paths = json.loads(song.sheet_pdf_paths or '[]')
        except (json.JSONDecodeError, TypeError):
            pass
        
        songs_data.append({
            'id': song.id,
            'song_id': song.song_id,
            'title': song.title,
            'author': song.author,
            'version_name': song.version_name,
            'title_original': song.title_original,
            'author_original': song.author_original,
            'admin_checked': song.admin_checked,
            'printed': song.printed,
            'categories': song.categories or '',
            'alternative_titles': song.alternative_titles or '',
            'mp3_paths': mp3_paths,
            'sheet_pdf_paths': sheet_pdf_paths,
            'pdf_lyrics_path': song.pdf_lyrics_path,
            'pdf_chords_path': song.pdf_chords_path,
            'tex_path': song.tex_path
        })
    
    return render_template('index.html', 
                         songs=songs_data, 
                         total_songs=total_songs,
                         total_admin_checked=total_admin_checked,
                         total_printed=total_printed,
                         category_counts=category_counts,
                         initial_batch_size=initial_batch_size)

@app.route('/api/songs')
def get_songs_paginated():
    """API endpoint for loading more songs with pagination"""
    try:
        offset = int(request.args.get('offset', 0))
        limit = min(int(request.args.get('limit', 25)), 100)  # Max 100 songs per batch
        
        songs = Song.query.order_by(Song.song_id).offset(offset).limit(limit).all()
        total_songs = Song.query.count()
        
        songs_data = []
        for song in songs:
            # Parse file paths safely
            mp3_paths = []
            sheet_pdf_paths = []
            try:
                mp3_paths = json.loads(song.mp3_paths or '[]')
                sheet_pdf_paths = json.loads(song.sheet_pdf_paths or '[]')
            except (json.JSONDecodeError, TypeError):
                pass
            
            songs_data.append({
                'id': song.id,
                'song_id': song.song_id,
                'title': song.title,
                'author': song.author,
                'version_name': song.version_name,
                'title_original': song.title_original,
                'author_original': song.author_original,
                'admin_checked': song.admin_checked,
                'printed': song.printed,
                'categories': song.categories or '',
                'alternative_titles': song.alternative_titles or '',
                'mp3_paths': mp3_paths,
                'sheet_pdf_paths': sheet_pdf_paths,
                'pdf_lyrics_path': song.pdf_lyrics_path,
                'pdf_chords_path': song.pdf_chords_path,
                'tex_path': song.tex_path
            })
        
        return jsonify({
            'songs': songs_data,
            'total_songs': total_songs,
            'offset': offset,
            'limit': limit,
            'has_more': (offset + len(songs_data)) < total_songs
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/songs/<song_ids>')
def songs_view(song_ids):
    """
    Display specific songs based on comma-separated song IDs in URL
    Example: /songs/A-001,B-002,C-003
    """
    # Parse the song IDs from the URL
    song_id_list = [sid.strip() for sid in song_ids.split(',') if sid.strip()]
    
    if not song_id_list:
        flash("No song IDs provided", "error")
        return redirect(url_for('index'))
    
    # Query songs based on the provided IDs
    songs = Song.query.filter(Song.song_id.in_(song_id_list)).all()
    
    # Check for missing songs
    found_ids = [song.song_id for song in songs]
    missing_ids = [sid for sid in song_id_list if sid not in found_ids]
    
    if missing_ids:
        flash(f"Songs not found: {', '.join(missing_ids)}", "warning")
    
    if not songs:
        flash("No songs found with the provided IDs", "error")
        return redirect(url_for('index'))
    
    # Sort songs to match the order from the URL
    songs_dict = {song.song_id: song for song in songs}
    ordered_songs = [songs_dict[sid] for sid in song_id_list if sid in songs_dict]
    
    return render_template('songs_view.html', songs=ordered_songs, song_ids=song_ids)

@app.route('/load_songs')
def load_songs():
    for fname in os.listdir(JSON_FOLDER):
        if fname.endswith(".json"):
            with open(os.path.join(JSON_FOLDER, fname), 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not Song.query.filter_by(title=data['title']).first():
                    song = Song(
                        title=data.get('title'),
                        author=data.get('author') if data.get('author') is not None and len(data.get('author')) > 1 else None,
                        categories=",".join(data.get('categories', [])),
                        song_parts=json.dumps(data["song_parts"], ensure_ascii=False),
                        # checked=False,
                        admin_checked=False
                    )
                    db.session.add(song)
    db.session.commit()
    flash("Songs loaded.")
    return redirect(url_for('index'))

@app.route('/backup')
def backup():
    backup_path = backup_db(os.path.abspath(BASE_DIR + '/instance/songs.db'), BACKUP_FOLDER)
    flash(f"Backup created: {backup_path}")
    return redirect(url_for('index'))

@app.route('/delete_file/<int:song_id>/<file_type>', methods=['POST'])
def delete_file(song_id, file_type):
    print(f"DEBUG: delete_file called with song_id={song_id}, file_type='{file_type}'", flush=True)
    song = Song.query.get_or_404(song_id)

    if file_type == 'tex':
        print(f"DEBUG: Deleting TeX file. song.tex_path = {song.tex_path}", flush=True)
        if song.tex_path:
            # Convert relative path to absolute path
            actual_path = os.path.join(BASE_DIR, song.tex_path)
            
            print(f"DEBUG: Checking if file exists: {os.path.exists(actual_path)}", flush=True)
            if os.path.exists(actual_path):
                print(f"DEBUG: Removing TeX file: {actual_path}", flush=True)
                os.remove(actual_path)
                song.tex_path = None
                print("DEBUG: TeX file removed and path cleared", flush=True)

                # Also remove generated PDFs when TeX is deleted
                if song.pdf_lyrics_path:
                    pdf_path = os.path.join(BASE_DIR, song.pdf_lyrics_path)
                    if os.path.exists(pdf_path):
                        print(f"DEBUG: Removing lyrics PDF: {pdf_path}", flush=True)
                        os.remove(pdf_path)
                    song.pdf_lyrics_path = None

                if song.pdf_chords_path:
                    pdf_path = os.path.join(BASE_DIR, song.pdf_chords_path)
                    if os.path.exists(pdf_path):
                        print(f"DEBUG: Removing chords PDF: {pdf_path}", flush=True)
                        os.remove(pdf_path)
                    song.pdf_chords_path = None
            else:
                print(f"DEBUG: TeX file not found at path: {actual_path}", flush=True)
                flash(f"TeX file not found", "error")
        else:
            print("DEBUG: No TeX path set for this song", flush=True)
            flash("No TeX file to delete", "error")

    elif file_type == 'pdf_lyrics':
        if song.pdf_lyrics_path:
            actual_path = os.path.join(BASE_DIR, song.pdf_lyrics_path)
            if os.path.exists(actual_path):
                os.remove(actual_path)
        song.pdf_lyrics_path = None
        
    elif file_type == 'pdf_chords':
        if song.pdf_chords_path:
            actual_path = os.path.join(BASE_DIR, song.pdf_chords_path)
            if os.path.exists(actual_path):
                os.remove(actual_path)
        song.pdf_chords_path = None
    elif file_type in ['mp3', 'midi', 'sheet_pdfs', 'sheet_mscz']:
        path_to_delete = request.form.get('path')

        # Determine which attribute to update based on file type
        attr_mapping = {
            'mp3': 'mp3_paths',
            'midi': 'midi_paths',
            'sheet_pdfs': 'sheet_pdf_paths',  # Assuming you have this attribute
            'sheet_mscz': 'sheet_mscz_paths'              # Assuming you have this attribute
        }

        attr = attr_mapping[file_type]
        paths = json.loads(getattr(song, attr) or '[]')

        if path_to_delete in paths:
            if file_type in ['mp3', 'midi'] and delete_from_s3(path_to_delete):
                paths.remove(path_to_delete)
            elif file_type in ['sheet_pdfs', 'sheet_mscz'] and os.path.exists(path_to_delete):
                os.remove(path_to_delete)
                paths.remove(path_to_delete)
            else:
                flash(f"{file_type.upper()} file couldnt be deleted.")

            setattr(song, attr, json.dumps(paths, ensure_ascii=False))

    db.session.commit()
    flash(f"{file_type.upper()} file deleted.")
    return redirect(url_for('song_view', song_id=song.id))

@app.route('/song/add')
def add_song():
    return redirect(url_for('song_detail', song_id='new'))

@app.route('/song/<song_id>', methods=['GET', 'POST'])
def song_detail(song_id):
    # Handle both new song creation and existing song editing
    is_new_song = song_id == 'new'
    
    if is_new_song:
        song = Song(title="")
    else:
        try:
            song_id = int(song_id)
            song = Song.query.get_or_404(song_id)
        except ValueError:
            return redirect(url_for('index'))

    if request.method == 'POST':
        # Update song fields
        song.title = request.form['title']
        song.author = request.form['author'].strip() if request.form['author'] and request.form['author'].strip() else None
        song.version_name = request.form['version_name']

        song.title_original = request.form.get('title_original', '')
        song.author_original = request.form.get('author_original', '')
        song.admin_checked = 'admin_checked' in request.form
        song.printed = 'printed' in request.form

        song.categories = ';;'.join(request.form.get('categories', '').split(','))
        song.alternative_titles = ';;'.join(request.form.getlist('alternative_titles'))

        # Handle song parts
        parts = []
        idx = 0
        while True:
            part_type = request.form.get(f'part_type_{idx}')
            part_lines = request.form.get(f'part_lines_{idx}')
            if part_type and part_lines:
                parts.append({
                    'type': part_type,
                    'lines': [line.strip() for line in part_lines.splitlines() if line.strip()]
                })
                idx += 1
            else:
                break
        song.song_parts = json.dumps(parts, ensure_ascii=False)

        # For new songs, add to session first to get an ID
        if is_new_song:
            db.session.add(song)
            db.session.commit()  # Commit to get song ID

        song_folder = get_song_upload_folder(song.id)

        # Handle file uploads (works for both new and existing songs)
        def handle_file_update(current_path, file, field_name):
            if file and allowed_file(file.filename):
                # Delete old file if exists (only for existing songs)
                if not is_new_song and current_path and os.path.exists(current_path):
                    os.remove(current_path)
                # Save new file
                filename = secure_filename(file.filename)
                path = os.path.join(song_folder, filename)
                file.save(path)
                return path
            return current_path

        # Update single files
        song.tex_path = handle_file_update(song.tex_path, request.files.get('tex'), 'tex')
        song.pdf_lyrics_path = handle_file_update(song.pdf_lyrics_path, request.files.get('pdf_lyrics'), 'pdf_lyrics')
        song.pdf_chords_path = handle_file_update(song.pdf_chords_path, request.files.get('pdf_chords'), 'pdf_chords')

        # Handle multiple files (works for both new and existing songs)
        def update_multi_files(current_paths, new_files, field_name):
            paths = json.loads(current_paths or '[]')

            # Handle deletions (only for existing songs)
            if not is_new_song:
                paths = [p for p in paths if os.path.exists(p)]  # Remove any deleted files

            # Add new files
            for file in new_files:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    path = os.path.join(song_folder, filename)
                    file.save(path)
                    paths.append(path)

            return json.dumps(paths, ensure_ascii=False)

        song.mp3_paths = update_multi_files_s3(song.mp3_paths, request.files.getlist('mp3s'), folder=f'mp3s/{song.id}')
        song.midi_paths = update_multi_files_s3(song.midi_paths, request.files.getlist('midis'), folder=f'midis/{song.id}')
        song.sheet_pdf_paths = update_multi_files(song.sheet_pdf_paths, request.files.getlist('sheet_pdfs'), 'sheet_pdfs')
        song.sheet_mscz_paths = update_multi_files(song.sheet_mscz_paths, request.files.getlist('sheet_mscz'), 'sheet_mscz')

        if 'associated_song_id' in request.form:
            associated_song_id = request.form['associated_song_id']
            associated_song = Song.query.filter_by(song_id=associated_song_id).first()

            if associated_song:
                try:
                    # Store original titles
                    associated_original_title = associated_song.title

                    # Get the NEW title from the form submission
                    new_title = request.form['title']

                    # Update BOTH songs
                    song.title = associated_original_title                   # Set common title
                    song.version_name = song.version_name  # Preserve original as version

                    # associated_song.title = new_title           # Set common title
                    # associated_song.version_name = associated_original_title  # Preserve original
                    print(song.song_id)
                    song.song_id = associated_song.song_id  # Associate IDs
                    print(song.song_id)
                    db.session.commit()
                    flash(f"Songs successfully associated with common title: {new_title}", 'success')
                    return redirect(url_for('song_view', song_id=song.id))


                except Exception as e:
                    db.session.rollback()
                    flash(f"Error during association: {str(e)}", 'error')
                    if is_new_song:
                        return redirect(url_for('song_detail', song_id='new'))
                    else:
                        return redirect(url_for('song_detail', song_id=song.id))

            flash("Associated song not found", 'error')
            return redirect(url_for('song_view', song_id=song.id))

        db.session.commit()
        if is_new_song:
            flash("Song created successfully!", "success")
        else:
            flash("Song updated successfully!", "success")
        return redirect(url_for('song_view', song_id=song.id))

    # Prepare data for template
    song.alternative_titles = song.alternative_titles.split(';;') if song.alternative_titles else []
    data = json.loads(song.song_parts) if song.song_parts else []
    mp3s = json.loads(song.mp3_paths or '[]')
    midis = json.loads(song.midi_paths or '[]')
    sheet_pdfs = json.loads(song.sheet_pdf_paths or '[]')
    sheet_mscz = json.loads(song.sheet_mscz_paths or '[]')

    return render_template('song_detail.html', 
                         song=song, 
                         data=data, 
                         mp3s=mp3s, 
                         midis=midis, 
                         sheet_pdfs=sheet_pdfs, 
                         sheet_mscz=sheet_mscz, 
                         is_edit=not is_new_song)

@app.route('/song/<int:song_id>/view')
def song_view(song_id):
    """Read-only detailed view of a song - no editing capabilities"""
    song = Song.query.get_or_404(song_id)
    
    # Parse song parts data
    try:
        data = json.loads(song.song_parts or '[]')
    except (json.JSONDecodeError, TypeError):
        data = []
    
    # Get file paths
    mp3s = json.loads(song.mp3_paths or '[]')
    midis = json.loads(song.midi_paths or '[]')
    sheet_pdfs = json.loads(song.sheet_pdf_paths or '[]')
    sheet_mscz = json.loads(song.sheet_mscz_paths or '[]')

    return render_template('song_view.html', song=song, data=data, mp3s=mp3s, midis=midis, sheet_pdfs=sheet_pdfs, sheet_mscz=sheet_mscz)

@app.route('/song/delete/<int:song_id>', methods=['POST'])
def delete_song(song_id):
    # Check if password is provided and correct
    provided_password = request.form.get('password')
    if not provided_password or provided_password != DELETE_SONG_PASSWORD:
        flash("Nesprávne heslo pre vymazanie piesne!", "error")
        return redirect(url_for('song_view', song_id=song_id))
    
    song = Song.query.get_or_404(song_id)
    song_title = song.title  # Store for flash message
    delete_song_files(song_id)
    db.session.delete(song)
    db.session.commit()
    flash(f"Pieseň '{song_title}' bola úspešne vymazaná!", "success")
    return redirect(url_for('index'))

# Template filter for chord rendering
@app.template_filter('replace_chords')
def replace_chords_filter(text):
    return Markup(re.sub(r"\[([^\]]+)\]", r"<sup style='color:orange; font-size:1.1em'><strong>\1</strong></sup>", text))

# Template filter for JSON parsing
@app.template_filter('parse_json')
def parse_json_filter(text):
    if not text:
        return []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []

@app.route('/api/check-delete-password', methods=['POST'])
def check_delete_password():
    """API endpoint to validate delete password"""
    try:
        data = request.get_json()
        provided_password = data.get('password', '')
        
        if provided_password == DELETE_SONG_PASSWORD:
            return jsonify({'valid': True})
        else:
            return jsonify({'valid': False, 'message': 'Nesprávne heslo'})
    
    except Exception as e:
        return jsonify({'valid': False, 'message': 'Chyba servera'}), 500

@app.route('/api/songs-for-association')
def get_songs():
    prefix = request.args.get('prefix', '').upper()
    exclude_id = request.args.get('exclude_id')  # song_id to exclude
    print(exclude_id)
    # Base query parts
    matching = (
        db.session.query(Song.song_id, Song.title)
        .filter(Song.song_id.startswith(prefix))
    )

    others = (
        db.session.query(Song.song_id, Song.title)
        .filter(~Song.song_id.startswith(prefix))
    )

    if exclude_id:
        matching = matching.filter(Song.song_id != exclude_id)
        others = others.filter(Song.song_id != exclude_id)

    combined_query = matching.union(others)

    combined = combined_query.order_by(
        case(
            (Song.song_id.startswith(prefix), 0),
            else_=1
        ),
        Song.song_id,
        Song.title
    ).all()

    return jsonify([{'song_id': sid, 'title': title} for sid, title in combined])

@app.route('/api/search')
def search_songs():
    """Fast server-side search endpoint with pagination"""
    from unidecode import unidecode
    import re
    
    # Get search parameters
    query = request.args.get('q', '').strip()
    printed_filter = request.args.get('printed')  # 'true' or None
    unchecked_filter = request.args.get('unchecked')  # 'true' or None  
    categories_filter = request.args.get('categories')  # comma-separated
    limit = min(int(request.args.get('limit', 50)), 100)  # Max 100 results per page
    offset = max(int(request.args.get('offset', 0)), 0)  # Start from this position
    
    # Start with base query
    query_obj = Song.query
    
    # Apply text search if provided
    if query:
        # Normalize the search query the same way we normalize stored text
        # First remove any chord brackets from the query (in case user searches for "[C] hello")
        query_no_chords = re.sub(r'\[[^\]]*\]', '', query)
        normalized_query = unidecode(query_no_chords.lower()).replace(",", " ").replace(".", " ").replace("-", " ").replace("_", " ").replace(";", " ").strip()
        normalized_query = re.sub(r'\s+', ' ', normalized_query)
        
        # Use LIKE for fast substring search on pre-normalized text
        query_obj = query_obj.filter(Song.search_text.like(f'%{normalized_query}%'))
    
    # Apply filters
    if printed_filter == 'true':
        query_obj = query_obj.filter(Song.printed == True)
    elif printed_filter == 'false':
        query_obj = query_obj.filter(Song.printed == False)
    
    if unchecked_filter == 'true':
        query_obj = query_obj.filter(Song.admin_checked == False)
    
    # Apply category filters (intersection - must have ALL selected categories)
    category_list = []
    if categories_filter:
        category_list = [cat.strip().lower() for cat in categories_filter.split(',') if cat.strip()]
        for category in category_list:
            query_obj = query_obj.filter(Song.categories.ilike(f'%{category}%'))
    
    # Get total count before applying pagination
    total_count = query_obj.count()
    
    # Apply pagination and execute query
    songs = query_obj.order_by(Song.song_id).offset(offset).limit(limit).all()
    
    # Return JSON response with song data and pagination info
    results = []
    for song in songs:
        # Extract first 5 words from verse1 and chorus
        verse1_preview = ""
        chorus_preview = ""
        
        if song.song_parts:
            try:
                song_data = json.loads(song.song_parts)
                for part in song_data:
                    if isinstance(part, dict):
                        part_type = part.get('type', '').lower()
                        lines = part.get('lines', [])
                        
                        if part_type in ['sloka', 'verse', 'verse1', 'verš'] and not verse1_preview and lines:
                            # Get first line and extract first 9 words
                            first_line = lines[0] if lines else ""
                            # Remove chord brackets [C], [Am], etc.
                            clean_line = re.sub(r'\[[^\]]*\]', '', first_line)
                            words = clean_line.split()[:9]
                            verse1_preview = ' '.join(words)
                        
                        elif part_type in ['refren', 'chorus', 'refrén'] and not chorus_preview and lines:
                            # Get first line and extract first 9 words
                            first_line = lines[0] if lines else ""
                            # Remove chord brackets [C], [Am], etc.
                            clean_line = re.sub(r'\[[^\]]*\]', '', first_line)
                            words = clean_line.split()[:9]
                            chorus_preview = ' '.join(words)
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Parse file paths safely
        mp3_paths = []
        sheet_pdf_paths = []
        try:
            mp3_paths = json.loads(song.mp3_paths or '[]')
            sheet_pdf_paths = json.loads(song.sheet_pdf_paths or '[]')
        except (json.JSONDecodeError, TypeError):
            pass
        
        results.append({
            'id': song.id,
            'song_id': song.song_id,
            'title': song.title,
            'author': song.author,
            'author_original': song.author_original,
            'version_name': song.version_name,
            'title_original': song.title_original,
            'admin_checked': song.admin_checked,
            'printed': song.printed,
            'categories': song.categories or '',
            'alternative_titles': song.alternative_titles or '',
            'verse1_preview': verse1_preview,
            'chorus_preview': chorus_preview,
            'mp3_paths': mp3_paths,
            'sheet_pdf_paths': sheet_pdf_paths,
            'pdf_lyrics_path': song.pdf_lyrics_path,
            'pdf_chords_path': song.pdf_chords_path,
            'tex_path': song.tex_path
        })
    
    return jsonify({
        'results': results,
        'total_found': total_count,
        'returned_count': len(results),
        'offset': offset,
        'limit': limit,
        'has_more': (offset + len(results)) < total_count,
        'query': query,
        'filters_applied': {
            'printed': printed_filter == 'true',
            'unchecked': unchecked_filter == 'true', 
            'categories': category_list
        }
    })


@app.route('/generate_pdfs/<int:song_id>')
def generate_pdfs(song_id):
    song = Song.query.get_or_404(song_id)

    if not song.tex_path:
        flash("TeX file not found for this song.", "error")
        return redirect(url_for('song_view', song_id=song_id))
    
    # Convert relative path to absolute for file operations
    tex_file_absolute = os.path.join(BASE_DIR, song.tex_path)
    if not os.path.exists(tex_file_absolute):
        flash("TeX file not found for this song.", "error")
        return redirect(url_for('song_view', song_id=song_id))

    song_folder = get_song_upload_folder(song.id)
    tex_file = tex_file_absolute
    basename = os.path.splitext(os.path.basename(tex_file))[0]

    pdf_lyrics_path = os.path.join(song_folder, 'lyrics.pdf')
    pdf_chords_path = os.path.join(song_folder, 'lyrics_chords.pdf')

    def run_latex(tex_path, set_chords_bool, output_filename):
        with open(tex_path, 'r', encoding='utf-8') as f:
            tex_content = f.read()

        # Replace \setboolean{showchords}
        replacement = r'\\setboolean{showchords}{' + ('True' if set_chords_bool else 'False') + '}'
        tex_content = re.sub(r'\\setboolean\{showchords\}\{.*?\}', replacement, tex_content)

        # Create absolute path to fonts
        fonts_src = os.path.join(BASE_DIR, 'static/fonts')

        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy preamble
            shutil.copy(os.path.join(BASE_DIR, "preamble.tex"), tmpdir)

            # Create fonts directory structure in temp dir
            fonts_dest = os.path.join(tmpdir, 'fonts')
            os.makedirs(fonts_dest, exist_ok=True)

            # Copy all font files
            for font_file in os.listdir(fonts_src):
                if font_file.endswith(('.ttf', '.otf')):
                    shutil.copy(os.path.join(fonts_src, font_file), fonts_dest)

            # Update font path in tex content to use absolute path
            tex_content = tex_content.replace(
                'Path=./fonts/',
                f'Path={fonts_dest}/'
            )

            tmp_tex_path = os.path.join(tmpdir, "song.tex")
            # flash(f"Fonts directory contents: { os.listdir(fonts_dest)}")
            # flash(f"Modified tex_content snippet: {tex_content[:500]}")
            with open(tmp_tex_path, "w", encoding='utf-8') as f:
                f.write(tex_content)

            try:
                required_fonts = ['Poppins-Regular.ttf', 'Poppins-Bold.ttf', 'Poppins-Italic.ttf']
                for font in required_fonts:
                    if not os.path.exists(os.path.join(fonts_dest, font)):
                        raise RuntimeError(f"Missing font file: {font}")
                for _ in range(2):
                    result = subprocess.run(
                        ["lualatex", "-interaction=nonstopmode", "song.tex"],
                        cwd=tmpdir,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True  # This means output is already strings
                    )
                print("LaTeX Output:\n", result.stdout)  # No .decode() needed
                print("LaTeX Errors:\n", result.stderr)   # No .decode() needed
            except subprocess.CalledProcessError as e:
                flash(f"STDOUT: {str(e.stdout)}")  # Remove .decode()
                flash(f"STDERR:\n { str(e.stderr)}")  # Remove .decode()
                raise RuntimeError(f"LaTeX compilation failed {e.stdout} {e.stderr}")

            # Copy result to final path
            generated_pdf = os.path.join(tmpdir, "song.pdf")
            os.makedirs(os.path.dirname(output_filename), exist_ok=True)
            shutil.copyfile(generated_pdf, output_filename)

    run_latex(tex_file, set_chords_bool=False, output_filename=pdf_lyrics_path)
    run_latex(tex_file, set_chords_bool=True, output_filename=pdf_chords_path)

    # Update DB (convert to relative paths for cross-environment compatibility)
    song.pdf_lyrics_path = os.path.relpath(pdf_lyrics_path, BASE_DIR)
    song.pdf_chords_path = os.path.relpath(pdf_chords_path, BASE_DIR)
    db.session.commit()

    flash("PDFs generated successfully!", "success")
    return redirect(url_for('song_view', song_id=song_id))

@app.route('/api/category_counts')
def get_category_counts():
    """API endpoint to get category counts with optional filtering"""
    from unidecode import unidecode
    import re
    
    # Get filter parameters (same as search API)
    query = request.args.get('q', '').strip()
    printed_filter = request.args.get('printed')
    unchecked_filter = request.args.get('unchecked')
    active_categories = request.args.get('active_categories')  # comma-separated active filters
    
    # List of all categories
    categories = [
        "stále omšové spevy", "úvod", "medzispevy (žalmy; aleluja)", "obetovanie",
        "prijímanie", "poďakovanie po prijímaní", "záver", "adorácia", "advent",
        "vianoce", "pôst", "veľká noc", "cez rok", "k Duchu Svätému", "mariánske",
        "k svätcom", "detské", "iné", "liturgia hodín", "sobášne", "Taizé",
        "krížová cesta", "nevhodné"
    ]
    
    # Build base query with filters (excluding category filters for now)
    query_obj = Song.query
    
    # Apply text search if provided
    if query:
        query_no_chords = re.sub(r'\[[^\]]*\]', '', query)
        normalized_query = unidecode(query_no_chords.lower()).replace(",", " ").replace(".", " ").replace("-", " ").replace("_", " ").replace(";", " ").strip()
        normalized_query = re.sub(r'\s+', ' ', normalized_query)
        query_obj = query_obj.filter(Song.search_text.like(f'%{normalized_query}%'))
    
    # Apply other filters
    if printed_filter == 'true':
        query_obj = query_obj.filter(Song.printed == True)
    if unchecked_filter == 'true':
        query_obj = query_obj.filter(Song.admin_checked == False)
    
    # Get active categories list for intersection logic
    active_categories_list = []
    if active_categories:
        active_categories_list = [cat.strip().lower() for cat in active_categories.split(',') if cat.strip()]
    
    # Calculate counts for each category
    category_counts = {}
    
    for category in categories:
        # For each category, create a query that includes this category + all active categories
        category_query = query_obj
        
        # Add the current category we're counting
        category_query = category_query.filter(Song.categories.ilike(f'%{category.lower()}%'))
        
        # Add all active category filters (intersection logic)
        for active_cat in active_categories_list:
            if active_cat != category.lower():  # Don't double-filter the same category
                category_query = category_query.filter(Song.categories.ilike(f'%{active_cat}%'))
        
        # Count songs for this category
        count = category_query.count()
        # Use lowercase version as key to match what frontend JavaScript expects
        # (frontend does btn.dataset.category.toLowerCase())  
        category_counts[category.lower()] = count
        
        # Also add the original case version for debugging/fallback
        category_counts[category] = count
    
    # Ensure JSON response has proper UTF-8 encoding
    return jsonify(category_counts)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)