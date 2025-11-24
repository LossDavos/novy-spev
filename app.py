import os
import json
import shutil
import subprocess
import tempfile
import requests
import io
import html
from flask import Flask, request, redirect, render_template, url_for, flash, jsonify, send_from_directory
from models import db, Song, ConcertSong
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
from datetime import datetime
import sqlite3
from markupsafe import Markup, escape
import re
from sqlalchemy import case
from pathlib import Path
from generate_tex import generate_latex_content
from stamper import stamp_pdf

# Import configuration and storage abstraction
import config
from storage import storage, init_storage

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Legacy compatibility - still needed for some file operations
DELETE_SONG_PASSWORD = config.DELETE_SONG_PASSWORD
JSON_FOLDER = config.JSON_FOLDER
BACKUP_FOLDER = config.BACKUP_FOLDER

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///songs.db'
app.secret_key = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH

# IMPORTANT: Upload folder is now outside static/ 
app.config['UPLOAD_FOLDER'] = config.UPLOAD_FOLDER

db.init_app(app)

# Add security headers to prevent XSS attacks
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    # Content Security Policy - prevent inline scripts
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Enable XSS protection in browsers
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'DENY'
    return response

# Initialize storage backend (local or S3 based on config)
init_storage()
print(f"[Storage] Using {'S3' if config.USE_S3_STORAGE else 'Local'} storage backend")

# Ensure folders exist
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(BACKUP_FOLDER, exist_ok=True)
os.makedirs(JSON_FOLDER, exist_ok=True)

# ============================================================================
# HELPER FUNCTIONS - Security
# ============================================================================

def sanitize_input(text, field_name="input"):
    """
    Sanitize user input to prevent XSS attacks.
    Returns sanitized text or raises ValueError if malicious content detected.
    """
    if not text:
        return text
    
    # Check for script tags and other dangerous patterns
    dangerous_patterns = [
        r'<script[^>]*>',
        r'javascript:',
        r'onerror\s*=',
        r'onload\s*=',
        r'onclick\s*=',
        r'eval\s*\(',
        r'window\.location',
        r'document\.cookie',
    ]
    
    text_lower = text.lower()
    for pattern in dangerous_patterns:
        if re.search(pattern, text_lower, re.IGNORECASE):
            raise ValueError(f"Malicious content detected in {field_name}. Script tags and JavaScript are not allowed.")
    
    # HTML escape the input
    return html.escape(text.strip())


# ============================================================================
# HELPER FUNCTIONS - File Handling
# ============================================================================

def allowed_file(filename, category=None):
    """Check if file extension is allowed"""
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in config.ALLOWED_EXTENSIONS


def save_uploaded_file(file, song_id, subfolder='', force_replace=False):
    """
    Save an uploaded file using storage abstraction.
    Checks if file already exists and can skip or replace based on force_replace flag.
    
    Args:
        file: FileStorage object from request.files
        song_id: Song ID (for folder organization)
        subfolder: Optional subfolder (e.g., 'mp3s', 'midis', 'sheets')
        force_replace: If True, replaces existing file. If False, skips if exists.
    
    Returns:
        tuple: (path, already_existed) - path is the file path, already_existed is True if file was there
    """
    filename = secure_filename(file.filename)
    folder = str(song_id)
    if subfolder:
        folder = f"{song_id}/{subfolder}"
    
    # Construct the full path to check if file already exists
    file_path = f"{folder}/{filename}"
    
    # Check if file already exists
    if storage().file_exists(file_path):
        if force_replace:
            print(f"[Upload] Replacing existing file: {file_path}")
            # Delete old file first
            delete_uploaded_file(file_path)
            # Save new file
            return (storage().save_file(file, folder, filename), True)
        else:
            print(f"[Upload] File already exists, skipping: {file_path}")
            return (file_path, True)  # Return existing path with flag
    
    # File doesn't exist, save it
    return (storage().save_file(file, folder, filename), False)


def delete_uploaded_file(path):
    """
    Delete a file using storage abstraction
    
    Args:
        path: Relative path/key of file to delete
    
    Returns:
        bool: True if successful
    """
    return storage().delete_file(path)


def get_file_url(path, expires_in=3600):
    """
    Get URL for a file (local route or S3 presigned URL)
    
    Args:
        path: Relative path/key of file
        expires_in: Expiration time for S3 URLs (ignored for local)
    
    Returns:
        str: URL to access the file
    """
    return storage().get_url(path, expires_in)

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

    # Find the full path from the database
    sheet_pdfs = json.loads(song.sheet_pdf_paths or '[]')
    
    # Find the path that ends with the requested filename
    relative_path = None
    for path in sheet_pdfs:
        if path.endswith(sheet_filename):
            relative_path = path
            break
    
    if not relative_path:
        flash("Sheet PDF not found in database!", "error")
        return redirect(url_for('song_detail', song_id=song.id))
    
    # Check if file exists using storage abstraction
    if not storage().file_exists(relative_path):
        flash("Sheet PDF file not found!", "error")
        return redirect(url_for('song_detail', song_id=song.id))

    # Check if we have an original version stored (same directory, _original suffix)
    dir_path = os.path.dirname(relative_path)
    base_name = os.path.splitext(sheet_filename)[0]
    original_filename = base_name + '_original.pdf'
    original_relative_path = f'{dir_path}/{original_filename}' if dir_path else original_filename

    if storage().file_exists(original_relative_path):
        # Return the original version
        return redirect(get_file_url(original_relative_path))
    else:
        # Return the current file (might already be stamped)
        return redirect(get_file_url(relative_path))


@app.route('/song/<int:song_id>/download_stamped_sheet/<path:sheet_filename>')
def download_stamped_sheet(song_id, sheet_filename):
    """
    Download PDF sheet with stamp - generated on-the-fly without storing
    """
    from flask import send_file
    import io

    song = Song.query.get_or_404(song_id)

    # Find the full path from the database
    sheet_pdfs = json.loads(song.sheet_pdf_paths or '[]')
    
    # Find the path that ends with the requested filename
    relative_path = None
    for path in sheet_pdfs:
        if path.endswith(sheet_filename):
            relative_path = path
            break
    
    if not relative_path:
        flash("Sheet PDF not found in database!", "error")
        return redirect(url_for('song_detail', song_id=song.id))
    
    # Check if file exists using storage abstraction
    if not storage().file_exists(relative_path):
        flash("Sheet PDF file not found!", "error")
        return redirect(url_for('song_detail', song_id=song.id))

    try:
        # For local storage, get absolute path directly
        if not config.USE_S3_STORAGE:
            current_file_path = os.path.join(config.UPLOAD_FOLDER, relative_path)
        else:
            # For S3, download file to temp location first
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_input:
                # Get presigned URL and download
                s3_url = storage().get_url(relative_path)
                import requests
                response = requests.get(s3_url)
                temp_input.write(response.content)
                current_file_path = temp_input.name
        
        # Create stamped version in memory using temporary file
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_stamped:
            font_path = os.path.join(os.path.dirname(__file__), 'static', 'fonts')
            success = stamp_pdf(current_file_path, temp_stamped.name, song.song_id, song.version_name, font_path)

            if not success:
                # Clean up temp files
                if config.USE_S3_STORAGE and os.path.exists(current_file_path):
                    os.unlink(current_file_path)
                flash("Failed to create stamped version", "error")
                return redirect(url_for('song_detail', song_id=song.id))

            # Read the stamped PDF into memory
            with open(temp_stamped.name, 'rb') as f:
                pdf_data = f.read()

            # Clean up temp files
            os.unlink(temp_stamped.name)
            if config.USE_S3_STORAGE and os.path.exists(current_file_path):
                os.unlink(current_file_path)

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
    Jinja template filter to get file URL (works with both local and S3 storage)
    Usage in template:
        <a href="{{ 'uploads/44/adeste_hlasy.mid' | presigned_url }}">Download</a>
    """
    try:
        return Markup(get_file_url(key, expires_in))
    except Exception as e:
        print(f"Error generating URL for {key}: {e}")
        return ""


def update_multi_file_paths(current_paths, new_files, song_id, subfolder='', force_replace=False):
    """
    Upload multiple files and update path list
    
    Args:
        current_paths: JSON string of existing paths
        new_files: List of FileStorage objects
        song_id: Song ID for folder organization
        subfolder: Optional subfolder (e.g., 'mp3s', 'midis')
        force_replace: If True, replaces existing files
    
    Returns:
        str: JSON string of updated paths
    """
    paths = json.loads(current_paths or '[]')
    skipped_files = []
    replaced_files = []
    
    for file in new_files:
        if file and allowed_file(file.filename):
            try:
                path, already_existed = save_uploaded_file(file, song_id, subfolder, force_replace=force_replace)
                
                if path:
                    # Check if path is not already in the list to avoid duplicates
                    if path not in paths:
                        paths.append(path)
                        if already_existed:
                            if force_replace:
                                replaced_files.append(file.filename)
                            else:
                                skipped_files.append(file.filename)
                    else:
                        print(f"[Upload] Path already in list: {path}")
                        if not force_replace:
                            skipped_files.append(file.filename)
                else:
                    flash(f"Failed to upload {file.filename}", "error")
            except Exception as e:
                flash(f"Upload error for {file.filename}: {e}", "error")
    
    # Show appropriate messages
    if replaced_files:
        flash(f"Files replaced: {', '.join(replaced_files)}", "success")
    if skipped_files:
        flash(f"Files already exist (skipped): {', '.join(skipped_files)}. Use 'Replace Existing Files' option to override.", "warning")
    
    return json.dumps(paths, ensure_ascii=False)


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
    """Delete all files associated with a song"""
    song = Song.query.get(song_id)

    if song:
        # Delete all file types (MP3s, MIDIs, PDFs, etc.)
        try:
            # Delete MP3 files
            if song.mp3_paths:
                mp3_paths = json.loads(song.mp3_paths)
                for path in mp3_paths:
                    if path:
                        print(f"Deleting MP3 file: {path}")
                        delete_uploaded_file(path)

            # Delete MIDI files
            if song.midi_paths:
                midi_paths = json.loads(song.midi_paths)
                for path in midi_paths:
                    if path:
                        print(f"Deleting MIDI file: {path}")
                        delete_uploaded_file(path)
            
            # Delete sheet PDFs
            if song.sheet_pdf_paths:
                sheet_paths = json.loads(song.sheet_pdf_paths)
                for path in sheet_paths:
                    if path:
                        print(f"Deleting sheet PDF: {path}")
                        delete_uploaded_file(path)
            
            # Delete other files (lyrics PDF, chords PDF, TeX, MuseScore, etc.)
            for attr in ['pdf_lyrics_path', 'pdf_chords_path', 'tex_path', 'musescore_path']:
                path = getattr(song, attr, None)
                if path:
                    print(f"Deleting {attr}: {path}")
                    delete_uploaded_file(path)

        except (json.JSONDecodeError, TypeError) as e:
            print(f"Error parsing file paths for song {song_id}: {e}")
        except Exception as e:
            print(f"Error deleting files for song {song_id}: {e}")

    # Delete local folder (if using local storage, this cleans up the directory)
    song_folder = os.path.join(config.UPLOAD_FOLDER, str(song_id))
    if os.path.exists(song_folder):
        print(f"Deleting local song folder: {song_folder}")
        try:
            shutil.rmtree(song_folder)
        except Exception as e:
            print(f"Error deleting folder {song_folder}: {e}")

# Routes

@app.route('/song/<int:song_id>/generate_tex', methods=['POST'])
def generate_tex(song_id):
    song = Song.query.get_or_404(song_id)

    # Determine filename and relative path
    tex_filename = f"{secure_filename(song.song_id or song.title)}.tex"
    relative_path = f"{song.id}/{tex_filename}"
    
    # For local storage, write directly to file
    if not config.USE_S3_STORAGE:
        # Get absolute path for local storage
        folder = os.path.join(config.UPLOAD_FOLDER, str(song.id))
        os.makedirs(folder, exist_ok=True)
        tex_path = os.path.join(folder, tex_filename)
        
        # Prepare LaTeX content
        latex = generate_latex_content(song)
        
        # Write to .tex file
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(latex)
        
        # Save relative path in DB
        song.tex_path = relative_path
    else:
        # For S3 storage, create temp file and upload
        import io
        from werkzeug.datastructures import FileStorage
        
        # Prepare LaTeX content
        latex = generate_latex_content(song)
        
        # Create file-like object
        tex_file = FileStorage(
            stream=io.BytesIO(latex.encode('utf-8')),
            filename=tex_filename,
            content_type='text/plain'
        )
        
        # Upload to S3
        saved_path = storage().save_file(tex_file, str(song.id), tex_filename)
        song.tex_path = saved_path
    
    db.session.commit()

    flash("TeX file generated successfully!", "success")
    return redirect(url_for('song_view', song_id=song_id))


# ============================================================================
# FILE SERVING ROUTES
# ============================================================================

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """
    Serve uploaded files from /uploads/ directory or S3
    
    This route handles all file serving, automatically using:
    - Local filesystem when USE_S3_STORAGE=false
    - S3 presigned URLs when USE_S3_STORAGE=true
    
    Security: Validates path to prevent directory traversal
    """
    if config.USE_S3_STORAGE:
        # For S3: generate presigned URL and redirect
        try:
            url = storage().get_url(filename, expires_in=3600)
            if url:
                return redirect(url)
            else:
                return jsonify({'error': 'File not found in S3'}), 404
        except Exception as e:
            return jsonify({'error': f'S3 error: {str(e)}'}), 500
    else:
        # For local storage: serve directly from uploads folder
        try:
            # Security: send_from_directory prevents path traversal
            return send_from_directory(config.UPLOAD_FOLDER, filename)
        except FileNotFoundError:
            return jsonify({'error': 'File not found'}), 404


# Legacy route for presigned S3 URLs (kept for backward compatibility)
@app.route('/api/presigned_url')
def get_presigned_url():
    """
    Legacy API endpoint to get presigned URL for S3 files
    Use /uploads/<path> route instead for new code
    """
    try:
        key = request.args.get('key')
        if not key:
            return jsonify({'error': 'Missing key parameter'}), 400

        expires_in = int(request.args.get('expires_in', 3600))
        
        if config.USE_S3_STORAGE:
            url = storage().get_url(key, expires_in=expires_in)
            if url:
                return redirect(url)
            else:
                return jsonify({'error': 'File not found'}), 404
        else:
            # For local storage, redirect to /uploads/ route
            return redirect(url_for('serve_upload', filename=key))

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/check_file_exists', methods=['POST'])
def check_file_exists():
    """
    Check if files already exist before upload
    Expects JSON: {song_id: 123, files: [{name: "file.mp3", type: "mp3s"}]}
    Returns: {exists: [{name: "file.mp3", path: "123/mp3s/file.mp3"}]}
    """
    try:
        data = request.get_json()
        song_id = data.get('song_id')
        files_to_check = data.get('files', [])
        
        if not song_id:
            return jsonify({'error': 'Missing song_id'}), 400
        
        existing_files = []
        
        for file_info in files_to_check:
            filename = secure_filename(file_info.get('name', ''))
            file_type = file_info.get('type', '')  # 'mp3s', 'midis', 'sheets', etc.
            
            if filename:
                # Construct the path
                if file_type:
                    file_path = f"{song_id}/{file_type}/{filename}"
                else:
                    file_path = f"{song_id}/{filename}"
                
                # Check if file exists
                if storage().file_exists(file_path):
                    existing_files.append({
                        'name': filename,
                        'path': file_path,
                        'type': file_type
                    })
        
        return jsonify({'exists': existing_files})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# MAIN ROUTES
# ============================================================================

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
            # Use storage abstraction to delete
            if storage().delete_file(song.tex_path):
                song.tex_path = None
                print("DEBUG: TeX file removed and path cleared", flush=True)

                # Also remove generated PDFs when TeX is deleted
                if song.pdf_lyrics_path:
                    if storage().delete_file(song.pdf_lyrics_path):
                        print(f"DEBUG: Removing lyrics PDF: {song.pdf_lyrics_path}", flush=True)
                        song.pdf_lyrics_path = None

                if song.pdf_chords_path:
                    if storage().delete_file(song.pdf_chords_path):
                        print(f"DEBUG: Removing chords PDF: {song.pdf_chords_path}", flush=True)
                        song.pdf_chords_path = None
            else:
                print(f"DEBUG: TeX file not found at path: {song.tex_path}", flush=True)
                flash(f"TeX file not found", "error")
        else:
            print("DEBUG: No TeX path set for this song", flush=True)
            flash("No TeX file to delete", "error")

    elif file_type == 'pdf_lyrics':
        if song.pdf_lyrics_path:
            if storage().delete_file(song.pdf_lyrics_path):
                song.pdf_lyrics_path = None
                flash("PDF lyrics file deleted.", "success")
            else:
                flash("PDF lyrics file not found.", "error")
        else:
            flash("No PDF lyrics file to delete.", "error")

    elif file_type == 'pdf_chords':
        if song.pdf_chords_path:
            if storage().delete_file(song.pdf_chords_path):
                song.pdf_chords_path = None
                flash("PDF chords file deleted.", "success")
            else:
                flash("PDF chords file not found.", "error")
        else:
            flash("No PDF chords file to delete.", "error")
    elif file_type in ['mp3', 'midi', 'sheet_pdfs', 'sheet_mscz']:
        path_to_delete = request.form.get('path')

        # Determine which attribute to update based on file type
        attr_mapping = {
            'mp3': 'mp3_paths',
            'midi': 'midi_paths',
            'sheet_pdfs': 'sheet_pdf_paths',
            'sheet_mscz': 'sheet_mscz_paths'
        }

        attr = attr_mapping[file_type]
        paths = json.loads(getattr(song, attr) or '[]')

        if path_to_delete in paths:
            # Use storage abstraction to delete files
            if delete_uploaded_file(path_to_delete):
                paths.remove(path_to_delete)
                flash(f"{file_type.upper()} file deleted.")
            else:
                flash(f"{file_type.upper()} file couldn't be deleted.")

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
        try:
            # Update song fields - SANITIZE ALL TEXT INPUTS TO PREVENT XSS
            song.title = sanitize_input(request.form['title'], "title")
            song.author = sanitize_input(request.form['author'], "author") if request.form.get('author') and request.form.get('author').strip() else None
            song.version_name = sanitize_input(request.form.get('version_name', ''), "version_name") if request.form.get('version_name') else None

            song.title_original = sanitize_input(request.form.get('title_original', ''), "original title")
            song.author_original = sanitize_input(request.form.get('author_original', ''), "original author")
            song.admin_checked = 'admin_checked' in request.form
            song.printed = 'printed' in request.form

            # Sanitize categories and alternative titles
            categories = request.form.get('categories', '').split(',')
            song.categories = ';;'.join([sanitize_input(cat, "category") for cat in categories if cat.strip()])
            
            alt_titles = request.form.getlist('alternative_titles')
            song.alternative_titles = ';;'.join([sanitize_input(title, "alternative title") for title in alt_titles if title.strip()])
        
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for('song_detail', song_id=song.id if not is_new_song else 'new'))

        # Handle song parts - SANITIZE ALL TEXT TO PREVENT XSS
        try:
            parts = []
            idx = 0
            while True:
                part_type = request.form.get(f'part_type_{idx}')
                part_lines = request.form.get(f'part_lines_{idx}')
                if part_type and part_lines:
                    parts.append({
                        'type': sanitize_input(part_type, f"part type {idx}"),
                        'lines': [sanitize_input(line, f"part line {idx}") for line in part_lines.splitlines() if line.strip()]
                    })
                    idx += 1
                else:
                    break
            song.song_parts = json.dumps(parts, ensure_ascii=False)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for('song_detail', song_id=song.id if not is_new_song else 'new'))

        # For new songs, add to session first to get an ID
        if is_new_song:
            db.session.add(song)
            db.session.commit()  # Commit to get song ID

        # Check if user confirmed to replace existing files
        force_replace = request.form.get('force_replace') == 'true'

        # Handle file uploads (works for both new and existing songs)
        def handle_file_update(current_path, file, field_name):
            """Update single file (PDF, TeX, etc.)"""
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                new_path = f"{song.id}/{filename}"
                
                # If the new file has the same name as current, show warning unless force_replace
                if current_path and current_path == new_path and not force_replace:
                    flash(f"File '{filename}' already exists (skipped). Use 'Replace Existing Files' option to override.", "warning")
                    return current_path
                
                # Delete old file if it exists and is different
                if current_path and current_path != new_path:
                    delete_uploaded_file(current_path)
                
                # Save new file (will replace if force_replace is True)
                path, already_existed = save_uploaded_file(file, song.id, '', force_replace=force_replace)
                if already_existed and not force_replace:
                    flash(f"File '{filename}' already exists (skipped). Use 'Replace Existing Files' option to override.", "warning")
                return path
            return current_path

        # Update single files
        song.tex_path = handle_file_update(song.tex_path, request.files.get('tex'), 'tex')
        song.pdf_lyrics_path = handle_file_update(song.pdf_lyrics_path, request.files.get('pdf_lyrics'), 'pdf_lyrics')
        song.pdf_chords_path = handle_file_update(song.pdf_chords_path, request.files.get('pdf_chords'), 'pdf_chords')

        # Handle multiple files (MP3s, MIDIs, sheet PDFs, MuseScore files)
        song.mp3_paths = update_multi_file_paths(song.mp3_paths, request.files.getlist('mp3s'), song.id, 'mp3s', force_replace=force_replace)
        song.midi_paths = update_multi_file_paths(song.midi_paths, request.files.getlist('midis'), song.id, 'midis', force_replace=force_replace)
        song.sheet_pdf_paths = update_multi_file_paths(song.sheet_pdf_paths, request.files.getlist('sheet_pdfs'), song.id, 'sheets', force_replace=force_replace)
        song.sheet_mscz_paths = update_multi_file_paths(song.sheet_mscz_paths, request.files.getlist('sheet_mscz'), song.id, 'mscz', force_replace=force_replace)

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

    # Get TeX file - works with both local and S3 storage
    if not config.USE_S3_STORAGE:
        # For local storage, use direct path
        tex_file_absolute = os.path.join(config.UPLOAD_FOLDER, song.tex_path)
        if not os.path.exists(tex_file_absolute):
            flash("TeX file not found for this song.", "error")
            return redirect(url_for('song_view', song_id=song_id))
    else:
        # For S3, download to temp file
        if not storage().file_exists(song.tex_path):
            flash("TeX file not found in storage.", "error")
            return redirect(url_for('song_view', song_id=song_id))
        
        # Download from S3 to temp file
        with tempfile.NamedTemporaryFile(suffix='.tex', delete=False) as temp_tex:
            s3_url = storage().get_url(song.tex_path)
            response = requests.get(s3_url)
            temp_tex.write(response.content)
            tex_file_absolute = temp_tex.name

    try:
        # Create output folder for PDFs (local temp location)
        with tempfile.TemporaryDirectory() as output_dir:
            pdf_lyrics_path = os.path.join(output_dir, 'lyrics.pdf')
            pdf_chords_path = os.path.join(output_dir, 'lyrics_chords.pdf')

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
                    with open(tmp_tex_path, "w", encoding='utf-8') as f:
                        f.write(tex_content)

                    try:
                        required_fonts = ['Poppins-Regular.ttf', 'Poppins-Bold.ttf', 'Poppins-Italic.ttf']
                        for font in required_fonts:
                            if not os.path.exists(os.path.join(fonts_dest, font)):
                                raise RuntimeError(f"Missing font file: {font}")
                        for _ in range(2):
                            result = subprocess.run(
                                ["/usr/bin/lualatex", "-interaction=nonstopmode", "song.tex"],
                                cwd=tmpdir,
                                check=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True
                            )
                        print("LaTeX Output:\n", result.stdout)
                        print("LaTeX Errors:\n", result.stderr)
                    except subprocess.CalledProcessError as e:
                        flash(f"STDOUT: {str(e.stdout)}")
                        flash(f"STDERR:\n { str(e.stderr)}")
                        raise RuntimeError(f"LaTeX compilation failed {e.stdout} {e.stderr}")

                    # Copy result to final path
                    generated_pdf = os.path.join(tmpdir, "song.pdf")
                    os.makedirs(os.path.dirname(output_filename), exist_ok=True)
                    shutil.copyfile(generated_pdf, output_filename)

            run_latex(tex_file_absolute, set_chords_bool=False, output_filename=pdf_lyrics_path)
            run_latex(tex_file_absolute, set_chords_bool=True, output_filename=pdf_chords_path)

            # Now save/upload the PDFs using storage abstraction
            # Save lyrics PDF
            with open(pdf_lyrics_path, 'rb') as f:
                pdf_content = f.read()
                lyrics_file = FileStorage(
                    stream=io.BytesIO(pdf_content),
                    filename='lyrics.pdf',
                    content_type='application/pdf'
                )
                lyrics_saved_path = storage().save_file(lyrics_file, str(song.id), 'lyrics.pdf')
                song.pdf_lyrics_path = lyrics_saved_path
            
            # Save chords PDF
            with open(pdf_chords_path, 'rb') as f:
                pdf_content = f.read()
                chords_file = FileStorage(
                    stream=io.BytesIO(pdf_content),
                    filename='lyrics_chords.pdf',
                    content_type='application/pdf'
                )
                chords_saved_path = storage().save_file(chords_file, str(song.id), 'lyrics_chords.pdf')
                song.pdf_chords_path = chords_saved_path

    finally:
        # Clean up temp TeX file if it was downloaded from S3
        if config.USE_S3_STORAGE and os.path.exists(tex_file_absolute):
            os.unlink(tex_file_absolute)

    db.session.commit()

    flash("PDFs generated successfully!", "success")
    return redirect(url_for('song_view', song_id=song_id))

@app.route('/api/concert/add_song', methods=['POST'])
def add_concert_song():
    data = request.get_json()
    song_id = data.get('song_id')
    section = data.get('section')
    
    if not song_id or not section:
        return jsonify({'error': 'Missing song_id or section'}), 400
        
    # Get max order for this section
    max_order = db.session.query(db.func.max(ConcertSong.order)).filter_by(section=section).scalar() or 0
    
    concert_song = ConcertSong(
        song_id=song_id,
        section=section,
        order=max_order + 1
    )
    db.session.add(concert_song)
    db.session.commit()
    
    return jsonify({'success': True, 'id': concert_song.id})

@app.route('/api/concert/remove_song', methods=['POST'])
def remove_concert_song():
    data = request.get_json()
    concert_song_id = data.get('id')
    
    if not concert_song_id:
        return jsonify({'error': 'Missing id'}), 400
        
    concert_song = ConcertSong.query.get(concert_song_id)
    if concert_song:
        db.session.delete(concert_song)
        db.session.commit()
        return jsonify({'success': True})
    
    return jsonify({'error': 'Song not found'}), 404

@app.route('/api/concert/reorder_songs', methods=['POST'])
def reorder_concert_songs():
    data = request.get_json()
    ordered_ids = data.get('ordered_ids', [])
    
    for index, cs_id in enumerate(ordered_ids):
        concert_song = ConcertSong.query.get(cs_id)
        if concert_song:
            concert_song.order = index
            
    db.session.commit()
    return jsonify({'success': True})

@app.route('/koncert')
def koncert():
    """Concert page with falling snowflakes and concert materials"""
    
    # Helper to get songs for a section
    def get_songs_for_section(section_name):
        # Join with Song to get song details, order by ConcertSong.order
        results = db.session.query(Song, ConcertSong).join(ConcertSong).filter(ConcertSong.section == section_name).order_by(ConcertSong.order).all()
        # Return list of songs, but we might need the ConcertSong ID for removal/reordering
        # So let's attach it to the song object temporarily or return a structure
        songs_with_meta = []
        for song, concert_song in results:
            song.concert_song_id = concert_song.id # Attach the ID for the frontend to use
            songs_with_meta.append(song)
        return songs_with_meta

    shared_songs = get_songs_for_section('shared')
    maria_songs = get_songs_for_section('maria')
    tristianus_songs = get_songs_for_section('tristianus')
    bozskeho_srdca_songs = get_songs_for_section('bozskeho_srdca')
    cd_brezovica_songs = get_songs_for_section('cd_brezovica')
    
    return render_template('koncert.html',
                         shared_songs=shared_songs,
                         maria_songs=maria_songs,
                         tristianus_songs=tristianus_songs,
                         bozskeho_srdca_songs=bozskeho_srdca_songs,
                         cd_brezovica_songs=cd_brezovica_songs)


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