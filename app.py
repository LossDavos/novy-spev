import os
import json
import shutil
import subprocess
import tempfile
import requests
import io
import html
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, redirect, render_template, url_for, flash, jsonify, send_from_directory, send_file, session
from models import db, Song, Event, EventSection, EventSectionSong, SongReport, SongChangeLog
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
from datetime import datetime
import sqlite3
from markupsafe import Markup, escape
import re
import difflib
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
UPDATE_SONG_PASSWORD = config.UPDATE_SONG_PASSWORD
EDIT_SONG_PASSWORD = config.EDIT_SONG_PASSWORD
ADMIN_EMAIL = config.ADMIN_EMAIL
JSON_FOLDER = config.JSON_FOLDER
BACKUP_FOLDER = config.BACKUP_FOLDER

# SMTP Configuration
SMTP_SERVER = config.SMTP_SERVER
SMTP_PORT = config.SMTP_PORT
SMTP_USERNAME = config.SMTP_USERNAME
SMTP_PASSWORD = config.SMTP_PASSWORD
SMTP_USE_TLS = config.SMTP_USE_TLS
EMAIL_FROM = config.EMAIL_FROM

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = config.DATABASE_URI
app.config['SQLALCHEMY_BINDS'] = {
    'events': config.EVENTS_DATABASE_URI
}
app.secret_key = config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH

# IMPORTANT: Upload folder is now outside static/ 
app.config['UPLOAD_FOLDER'] = config.UPLOAD_FOLDER

db.init_app(app)
app._databases_initialized = False

@app.before_request
def init_databases():
    if not app._databases_initialized:
        db.create_all()
        app._databases_initialized = True

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
        "connect-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
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
# EVENTS MODULE - Helpers
# ============================================================================

DEFAULT_EVENT_SECTIONS = [
    "Uvod",
    "Kyrie",
    "Gloria",
    "Zalm",
    "Aleluja",
    "Kredo",
    "Obetovanie",
    "Svaty",
    "Otce Nas",
    "Baranok",
    "Prijimanie",
    "Podakovanie po prijimani",
    "Zaver",
    "Ine"
]

DEFAULT_EVENT_SECTION_ORDER = {name: index for index, name in enumerate(DEFAULT_EVENT_SECTIONS) if name != "Ine"}


def sort_event_sections(sections):
    """Sort default sections canonically while preserving custom section placement slots."""
    indexed_sections = list(enumerate(sections))
    locked_sections = [
        (original_index, section)
        for original_index, section in indexed_sections
        if (section.get('name') or '').strip() in DEFAULT_EVENT_SECTION_ORDER
    ]
    locked_sections.sort(key=lambda item: (DEFAULT_EVENT_SECTION_ORDER[(item[1].get('name') or '').strip()], item[0]))

    result = []
    locked_index = 0
    for _, section in indexed_sections:
        section_name = (section.get('name') or '').strip()
        if section_name in DEFAULT_EVENT_SECTION_ORDER:
            result.append(locked_sections[locked_index][1])
            locked_index += 1
        else:
            # Custom/flexible sections keep exact user-selected placement.
            result.append(section)
    return result


def resolve_song_identifier(token):
    """Resolve a user-provided token to a Song object."""
    if not token:
        return None

    cleaned = token.strip()
    if not cleaned:
        return None

    if cleaned.isdigit():
        return Song.query.get(int(cleaned))

    match = re.search(r"([A-Za-z]-\d{3})", cleaned)
    if match:
        code = match.group(1).upper()
        return Song.query.filter_by(song_id=code).first()

    return None


def parse_section_songs(raw_text):
    """Parse a section textarea into ordered Song objects."""
    if not raw_text:
        return [], []

    tokens = [t.strip() for t in re.split(r"[\n,]+", raw_text) if t.strip()]
    songs = []
    errors = []
    for token in tokens:
        song = resolve_song_identifier(token)
        if not song:
            errors.append(token)
        else:
            songs.append(song)
    return songs, errors


def parse_section_song_ids(raw_ids):
    if not raw_ids:
        return [], []

    ids = [token.strip() for token in raw_ids.split(',') if token.strip()]
    songs = []
    errors = []
    for token in ids:
        if not token.isdigit():
            errors.append(token)
            continue
        song = Song.query.get(int(token))
        if not song:
            errors.append(token)
        else:
            songs.append(song)
    return songs, errors

def send_edit_notification_email(song, form_data):
    """Send email notification to admin about song edit request"""
    try:
        changes = []
        
        # Define field mappings: form_field -> (db_field, label, processor)
        field_mappings = {
            'title': ('title', 'Názov', lambda x: x.strip()),
            'author': ('author', 'Autor', lambda x: x.strip() if x else None),
            'version_name': ('version_name', 'Verzia', lambda x: x.strip() if x else None),
            'title_original': ('title_original', 'Pôvodný názov', lambda x: x.strip()),
            'author_original': ('author_original', 'Pôvodný autor', lambda x: x.strip()),
            'song_key': ('song_key', 'Tonina', lambda x: x.strip() if x else None),
        }
        
        # Check simple fields
        for form_field, (db_field, label, processor) in field_mappings.items():
            form_value = processor(form_data.get(form_field, ''))
            db_value = getattr(song, db_field) or ''
            if form_value != db_value:
                changes.append(f"{label}: '{db_value or '(prázdne)'}' → '{form_value or '(prázdne)'}'")
        
        # Check categories (special processing)
        new_categories = ';;'.join([cat.strip() for cat in form_data.get('categories', '').split(',') if cat.strip()])
        if new_categories != (song.categories or ''):
            changes.append(f"Kategórie: '{song.categories or '(prázdne)'}' → '{new_categories or '(prázdne)'}'")
        
        # Check alternative titles (special processing)
        new_alt_titles = ';;'.join([t.strip() for t in form_data.getlist('alternative_titles') if t.strip()])
        if new_alt_titles != (song.alternative_titles or ''):
            changes.append(f"Alt. názvy: '{song.alternative_titles or '(prázdne)'}' → '{new_alt_titles or '(prázdne)'}'")
        
        # Check song parts (lyrics/structure)
        parts = []
        idx = 0
        while True:
            part_type = form_data.get(f'part_type_{idx}')
            part_lines = form_data.get(f'part_lines_{idx}')
            if part_type and part_lines:
                parts.append({'type': part_type, 'lines': [l.strip() for l in part_lines.splitlines() if l.strip()]})
                idx += 1
            else:
                break
        
        new_song_parts = json.dumps(parts, ensure_ascii=False)
        lyrics_diff = ""
        if new_song_parts != song.song_parts:
            # Generate detailed diff for song text
            try:
                old_parts = json.loads(song.song_parts)
                
                # Compare parts
                diff_lines = []
                max_parts = max(len(old_parts), len(parts))
                
                for i in range(max_parts):
                    old_part = old_parts[i] if i < len(old_parts) else None
                    new_part = parts[i] if i < len(parts) else None
                    
                    if old_part and not new_part:
                        # Section removed
                        diff_lines.append(f"  ❌ Odstránená sekcia [{old_part['type']}]:")
                        for line in old_part['lines'][:5]:
                            diff_lines.append(f"     - {line}")
                        if len(old_part['lines']) > 5:
                            diff_lines.append(f"     ... ({len(old_part['lines']) - 5} ďalších riadkov)")
                    elif new_part and not old_part:
                        # New section added
                        diff_lines.append(f"  ✅ Nová sekcia [{new_part['type']}]:")
                        for line in new_part['lines'][:5]:
                            diff_lines.append(f"     + {line}")
                        if len(new_part['lines']) > 5:
                            diff_lines.append(f"     ... ({len(new_part['lines']) - 5} ďalších riadkov)")
                    elif old_part['type'] != new_part['type'] or old_part['lines'] != new_part['lines']:
                        # Section changed
                        if old_part['type'] != new_part['type']:
                            diff_lines.append(f"  🔄 Typ sekcie zmenený: [{old_part['type']}] → [{new_part['type']}]")
                        else:
                            diff_lines.append(f"  📝 Sekcia [{new_part['type']}] upravená:")
                        
                        # Show line-by-line changes
                        max_lines = max(len(old_part['lines']), len(new_part['lines']))
                        shown_lines = 0
                        for j in range(max_lines):
                            if shown_lines >= 10:  # Limit to 10 line changes shown
                                diff_lines.append(f"     ... (ďalšie zmeny)")
                                break
                            
                            old_line = old_part['lines'][j] if j < len(old_part['lines']) else None
                            new_line = new_part['lines'][j] if j < len(new_part['lines']) else None
                            
                            if old_line and not new_line:
                                diff_lines.append(f"     - {old_line}")
                                shown_lines += 1
                            elif new_line and not old_line:
                                diff_lines.append(f"     + {new_line}")
                                shown_lines += 1
                            elif old_line != new_line:
                                diff_lines.append(f"     - {old_line}")
                                diff_lines.append(f"     + {new_line}")
                                shown_lines += 2
                
                lyrics_diff = "\n" + "\n".join(diff_lines)
            except:
                lyrics_diff = ""
            
            changes.append(f"Text piesne: ZMENENÝ{lyrics_diff}")
        
        # If no changes, skip email
        if not changes:
            print("No changes detected, skipping email notification")
            return True
        
        # Create email
        subject = f"Žiadosť o úpravu piesne: {song.title}"
        changes_text = "\n".join([f"- {change}" for change in changes])
        
        body = f"""
Nová žiadosť o úpravu piesne v systéme Nový Spev.

Pieseň: {song.title} (ID: {song.id})

Navrhované zmeny:
{changes_text}

Pre schválenie zmien sa prihláste do systému s administrátorským heslom.

Link na pieseň: {request.url_root}song/{song.id}/view

---
Automatický email z aplikácie Nový Spev
        """
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = ADMIN_EMAIL
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # Send email via SMTP
        if SMTP_PASSWORD:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                if SMTP_USE_TLS:
                    server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
            print(f"Email sent successfully: {subject}")
        else:
            # If no SMTP password configured, just log the email
            print(f"EMAIL NOTIFICATION (SMTP not configured): {subject}")
            print(body)
        
        return True
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        # Don't raise - we don't want email failures to break the app
        return False


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


def update_multi_file_paths(current_paths, new_files, song_id, subfolder='', force_replace=False, replacements_out=None):
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
                        if force_replace and already_existed and replacements_out is not None:
                            replacements_out.append(secure_filename(file.filename))
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


def load_song_tex_content(song):
    if song.tex_path and storage().file_exists(song.tex_path):
        if config.USE_S3_STORAGE:
            try:
                s3_backend = storage()
                response = s3_backend.s3_client.get_object(Bucket=s3_backend.bucket, Key=song.tex_path)
                return response['Body'].read().decode('utf-8')
            except Exception as exc:
                raise RuntimeError(f"Failed to load TeX from S3: {exc}")

        local_storage = storage()
        if hasattr(local_storage, 'get_absolute_path'):
            tex_path = local_storage.get_absolute_path(song.tex_path)
            if tex_path and os.path.exists(tex_path):
                with open(tex_path, 'r', encoding='utf-8') as handle:
                    return handle.read()

    return generate_latex_content(song)


def parse_key_root(key_value):
    if not key_value:
        return None
    match = re.match(r'^([A-Ha-h])([#b]?)(.*)$', key_value.strip())
    if not match:
        return None
    letter, accidental, rest = match.groups()
    return {
        'root': f"{letter.upper()}{accidental}",
        'accidental': accidental,
        'rest': rest
    }


def normalize_enharmonic_preference(value, default='auto'):
    normalized = (value or '').strip().lower()
    if normalized in ('auto', 'sharps', 'flats'):
        return normalized
    return default


def normalize_part_enharmonic_preferences(raw_value):
    if not raw_value:
        return {}
    parsed = raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return {}
    if not isinstance(parsed, dict):
        return {}

    normalized = {}
    for key, value in parsed.items():
        mode = normalize_enharmonic_preference(value, default='')
        if not mode or mode == 'auto':
            continue
        normalized[str(key)] = mode
    return normalized


def calculate_transpose_steps(base_key, target_key):
    note_index = {
        'C': 0, 'C#': 1, 'Db': 1,
        'D': 2, 'D#': 3, 'Eb': 3,
        'E': 4,
        'F': 5, 'F#': 6, 'Gb': 6,
        'G': 7, 'G#': 8, 'Ab': 8,
        'A': 9, 'A#': 10, 'Bb': 10,
        'B': 11, 'H': 11
    }

    base = parse_key_root(base_key)
    target = parse_key_root(target_key)
    if not base or not target:
        return None
    base_index = note_index.get(base['root'])
    target_index = note_index.get(target['root'])
    if base_index is None or target_index is None:
        return None
    return target_index - base_index


def get_prefer_flats_for_target_key(base_key, steps, enharmonic_preference='auto'):
    note_index = {
        'C': 0, 'C#': 1, 'Db': 1,
        'D': 2, 'D#': 3, 'Eb': 3,
        'E': 4,
        'F': 5, 'F#': 6, 'Gb': 6,
        'G': 7, 'G#': 8, 'Ab': 8,
        'A': 9, 'A#': 10, 'Bb': 10,
        'B': 11, 'H': 11
    }
    notes_sharp = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    notes_flat = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
    flat_keys = {'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb'}
    sharp_keys = {'G', 'D', 'A', 'E', 'B', 'F#'}

    mode = normalize_enharmonic_preference(enharmonic_preference, default='auto')
    if mode == 'flats':
        return True
    if mode == 'sharps':
        return False

    parsed = parse_key_root(base_key)
    if not parsed:
        return steps < 0

    idx = note_index.get(parsed['root'])
    if idx is None:
        return steps < 0

    target_idx = (idx + steps + 12) % 12
    target_sharp = notes_sharp[target_idx]
    target_flat = notes_flat[target_idx]

    if target_flat in flat_keys:
        return True
    if target_sharp in sharp_keys:
        return False

    # Neutral/ambiguous fallback: keep source accidental flavor when present,
    # otherwise prefer flats when transposing downward.
    if parsed['accidental']:
        return parsed['accidental'] == 'b'
    return steps < 0


def transpose_chord_value(chord, steps, prefer_flats_global=None):
    if chord is None:
        return chord
    raw = chord.strip()
    if not raw:
        return raw

    optional = raw.startswith('(') and raw.endswith(')') and len(raw) > 2
    inner = raw[1:-1].strip() if optional else raw

    note_index = {
        'C': 0, 'C#': 1, 'Db': 1,
        'D': 2, 'D#': 3, 'Eb': 3,
        'E': 4,
        'F': 5, 'F#': 6, 'Gb': 6,
        'G': 7, 'G#': 8, 'Ab': 8,
        'A': 9, 'A#': 10, 'Bb': 10,
        'B': 11, 'H': 11
    }
    notes_sharp = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    notes_flat = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']

    def normalize_rest(rest):
        rest_value = rest.strip()
        rest_lower = rest_value.lower()
        if rest_lower == 'sus2':
            return '2'
        if rest_lower == 'sus4':
            return '4'
        return rest_value

    def transpose_part(part):
        match = re.match(r'^([A-Ha-h])([#b]?)(.*)$', part)
        if not match:
            return part
        letter, accidental, rest = match.groups()
        rest = normalize_rest(rest)
        rest_lower = rest.lower()
        is_minor = letter.islower()
        if rest_lower.startswith('min'):
            is_minor = True
            rest = rest[3:]
        elif rest_lower.startswith('m') and not rest_lower.startswith('maj'):
            is_minor = True
            rest = rest[1:]

        root_key = f"{letter.upper()}{accidental}"
        idx = note_index.get(root_key)
        if idx is None:
            return part
        next_index = (idx + steps + 12) % 12
        prefer_flats = (accidental == 'b') if prefer_flats_global is None else prefer_flats_global
        next_root = notes_flat[next_index] if prefer_flats else notes_sharp[next_index]
        if is_minor:
            next_root = next_root.lower()
        return f"{next_root}{rest}"

    parts = [p.strip() for p in inner.split('/')]
    transposed_parts = [transpose_part(p) for p in parts if p]
    combined = '/'.join(transposed_parts) if transposed_parts else inner
    return f"({combined})" if optional else combined


def transpose_latex_chords(latex_text, steps, base_key=None, enharmonic_preference='auto'):
    if steps == 0:
        return latex_text

    def resolve_prefer_flats(mode):
        return get_prefer_flats_for_target_key(base_key, steps, enharmonic_preference=mode)

    prefer_flats_global = resolve_prefer_flats(enharmonic_preference)

    def unescape_chord_arg(value):
        return (value
            .replace('\\textbackslash ', '\\')
            .replace('\\textasciitilde{}', '~')
            .replace('\\textasciicircum{}', '^')
            .replace('\\#', '#')
            .replace('\\&', '&')
            .replace('\\%', '%')
            .replace('\\$', '$')
            .replace('\\_', '_')
            .replace('\\{', '{')
            .replace('\\}', '}')
        )
    def escape_chord_arg(value):
        return (value
            .replace('\\', '\\textbackslash ')
            .replace('&', '\\&')
            .replace('%', '\\%')
            .replace('$', '\\$')
            .replace('#', '\\#')
            .replace('_', '\\_')
            .replace('{', '\\{')
            .replace('}', '\\}')
            .replace('~', '\\textasciitilde{}')
            .replace('^', '\\textasciicircum{}')
        )
    def replace_chord(match):
        raw_value = unescape_chord_arg(match.group(1))
        transposed = transpose_chord_value(raw_value, steps, prefer_flats_global=prefer_flats_global)
        return f"\\chord{{{escape_chord_arg(transposed)}}}"

    return re.sub(r'\\chord\{([^}]+)\}', replace_chord, latex_text)


def transpose_latex_chords_with_part_preferences(
    latex_text,
    steps,
    base_key=None,
    enharmonic_preference='auto',
    part_enharmonic_preferences=None,
):
    if steps == 0:
        return latex_text

    part_prefs = normalize_part_enharmonic_preferences(part_enharmonic_preferences)
    if not part_prefs:
        return transpose_latex_chords(
            latex_text,
            steps,
            base_key=base_key,
            enharmonic_preference=enharmonic_preference,
        )

    def unescape_chord_arg(value):
        return (value
            .replace('\\textbackslash ', '\\')
            .replace('\\textasciitilde{}', '~')
            .replace('\\textasciicircum{}', '^')
            .replace('\\#', '#')
            .replace('\\&', '&')
            .replace('\\%', '%')
            .replace('\\$', '$')
            .replace('\\_', '_')
            .replace('\\{', '{')
            .replace('\\}', '}')
        )

    def escape_chord_arg(value):
        return (value
            .replace('\\', '\\textbackslash ')
            .replace('&', '\\&')
            .replace('%', '\\%')
            .replace('$', '\\$')
            .replace('#', '\\#')
            .replace('_', '\\_')
            .replace('{', '\\{')
            .replace('}', '\\}')
            .replace('~', '\\textasciitilde{}')
            .replace('^', '\\textasciicircum{}')
        )

    def transpose_block_content(block_content, prefer_flats):
        def replace_chord(match):
            raw_value = unescape_chord_arg(match.group(1))
            transposed = transpose_chord_value(raw_value, steps, prefer_flats_global=prefer_flats)
            return f"\\chord{{{escape_chord_arg(transposed)}}}"

        return re.sub(r'\\chord\{([^}]+)\}', replace_chord, block_content)

    block_start_pattern = re.compile(r'\\[A-Za-z]+block\{')
    output = []
    cursor = 0
    block_index = 0

    while True:
        match = block_start_pattern.search(latex_text, cursor)
        if not match:
            output.append(latex_text[cursor:])
            break

        output.append(latex_text[cursor:match.end()])
        content_start = match.end()

        depth = 1
        i = content_start
        while i < len(latex_text) and depth > 0:
            if latex_text[i] == '{':
                depth += 1
            elif latex_text[i] == '}':
                depth -= 1
            i += 1

        if depth != 0:
            # Unbalanced braces: fall back to global transposition for safety.
            return transpose_latex_chords(
                latex_text,
                steps,
                base_key=base_key,
                enharmonic_preference=enharmonic_preference,
            )

        content_end = i - 1
        block_content = latex_text[content_start:content_end]

        block_mode = normalize_enharmonic_preference(
            part_prefs.get(str(block_index)),
            default=enharmonic_preference,
        )
        prefer_flats = get_prefer_flats_for_target_key(
            base_key,
            steps,
            enharmonic_preference=block_mode,
        )
        output.append(transpose_block_content(block_content, prefer_flats))
        output.append('}')

        block_index += 1
        cursor = i

    return ''.join(output)


def render_latex_to_pdf(latex_text):
    engine = None
    for candidate in ('xelatex', 'lualatex', 'pdflatex'):
        if shutil.which(candidate):
            engine = candidate
            break
    if engine is None:
        raise RuntimeError('No LaTeX engine found (xelatex/lualatex/pdflatex).')

    with tempfile.TemporaryDirectory() as temp_dir:
        tex_path = os.path.join(temp_dir, 'song.tex')
        with open(tex_path, 'w', encoding='utf-8') as handle:
            handle.write(latex_text)

        preamble_src = os.path.join(BASE_DIR, 'preamble.tex')
        shutil.copy(preamble_src, os.path.join(temp_dir, 'preamble.tex'))

        fonts_src = os.path.join(BASE_DIR, 'static', 'fonts')
        fonts_dest = os.path.join(temp_dir, 'fonts')
        if os.path.isdir(fonts_src):
            shutil.copytree(fonts_src, fonts_dest)

        command = [engine, '-interaction=nonstopmode', '-halt-on-error', 'song.tex']
        for _ in range(2):
            result = subprocess.run(
                command,
                cwd=temp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False
            )
            if result.returncode != 0:
                raise RuntimeError(f"XeLaTeX failed: {result.stdout}")

        pdf_path = os.path.join(temp_dir, 'song.pdf')
        with open(pdf_path, 'rb') as handle:
            return handle.read()


@app.route('/song/<int:song_id>/download_chords_pdf')
def download_chords_pdf(song_id):
    song = Song.query.get_or_404(song_id)
    try:
        steps = int(request.args.get('shift_steps', '0'))
    except ValueError:
        return jsonify({'error': 'Invalid shift_steps'}), 400

    song_pref = normalize_enharmonic_preference(song.enharmonic_preference, default='auto')
    request_pref = normalize_enharmonic_preference(
        request.args.get('enharmonic_preference'),
        default=song_pref
    )
    request_part_prefs = normalize_part_enharmonic_preferences(request.args.get('part_enharmonic_preferences'))

    if not request_part_prefs:
        request_part_prefs = normalize_part_enharmonic_preferences(song.part_enharmonic_preferences)

    ess_id = request.args.get('ess_id')
    if ess_id:
        try:
            ess = EventSectionSong.query.get(int(ess_id))
        except (ValueError, TypeError):
            ess = None
        if ess:
            if request.args.get('enharmonic_preference') in (None, ''):
                request_pref = normalize_enharmonic_preference(
                    ess.enharmonic_preference,
                    default=request_pref,
                )
            if not request_part_prefs:
                request_part_prefs = normalize_part_enharmonic_preferences(ess.part_enharmonic_preferences)

    try:
        latex_text = load_song_tex_content(song)
        transposed_latex = transpose_latex_chords_with_part_preferences(
            latex_text,
            steps,
            base_key=song.song_key,
            enharmonic_preference=request_pref,
            part_enharmonic_preferences=request_part_prefs,
        )
        pdf_data = render_latex_to_pdf(transposed_latex)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500

    base_name = secure_filename(song.song_id or song.title or f"song_{song.id}")
    filename = f"{base_name}_chords.pdf"
    return send_file(
        io.BytesIO(pdf_data),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=filename
    )


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
# EVENTS MODULE - Routes
# ============================================================================

@app.route('/api/song_lookup')
def song_lookup():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'songs': []})

    from unidecode import unidecode
    from sqlalchemy import or_

    query_no_chords = re.sub(r'\[[^\]]*\]', '', query)
    normalized_query = unidecode(query_no_chords.lower()).replace(",", " ").replace(".", " ").replace("-", " ").replace("_", " ").replace(";", " ").strip()
    normalized_query = re.sub(r'\s+', ' ', normalized_query)

    filters = []
    if normalized_query:
        filters.append(Song.search_text.like(f'%{normalized_query}%'))

    code_match = re.search(r"([A-Za-z]-\d{3})", query)
    if code_match:
        code = code_match.group(1).upper()
        filters.append(Song.song_id == code)

    if query.isdigit():
        filters.append(Song.id == int(query))

    if filters:
        songs = Song.query.filter(or_(*filters)).order_by(Song.song_id).limit(25).all()
    else:
        songs = []

    results = []
    for song in songs:
        label = f"{song.song_id} — {song.title}"
        if song.version_name:
            label = f"{label} ({song.version_name})"
        results.append({'id': song.id, 'label': label})

    return jsonify({'songs': results})

@app.route('/events')
def events_list():
    events = Event.query.order_by(Event.event_time.desc()).all()
    return render_template('events_list.html', events=events)


def build_section_form_data(section_names, section_songs, section_names_custom=None, section_song_settings_list=None):
    section_data = []
    section_names_custom = section_names_custom or []
    section_song_settings_list = section_song_settings_list or []
    for index, (name, songs_text) in enumerate(zip(section_names, section_songs)):
        custom_name = section_names_custom[index] if index < len(section_names_custom) else ''
        settings_json = section_song_settings_list[index] if index < len(section_song_settings_list) else '[]'
        try:
            song_settings = json.loads(settings_json or '[]')
        except (json.JSONDecodeError, TypeError):
            song_settings = []
        song_items = []
        songs, _ = parse_section_song_ids(songs_text)
        for song_index, song in enumerate(songs):
            label = f"{song.song_id} — {song.title}"
            if song.version_name:
                label = f"{label} ({song.version_name})"
            s = song_settings[song_index] if song_index < len(song_settings) else {}
            song_items.append({
                'id': song.id,
                'label': label,
                'transpose': int(s.get('transpose', 0) or 0),
                'capo': int(s.get('capo', 0) or 0)
            })
        section_data.append({
            'name': name,
            'custom_name': custom_name,
            'songs': song_items,
            'songs_text': songs_text,
            'settings': [{'transpose': s['transpose'], 'capo': s['capo']} for s in song_items]
        })
    return section_data


def build_section_data_from_event(event):
    section_data = []
    sections = sort_event_sections([
        {'name': section.name, 'section': section} for section in event.sections
    ])
    for section_entry in sections:
        section = section_entry['section']
        songs = EventSectionSong.query.filter_by(section_id=section.id).order_by(EventSectionSong.position).all()
        song_items = []
        for song in songs:
            label = f"{song.song_code or song.song_db_id} - {song.song_title}"
            if song.song_version_name:
                label = f"{label} ({song.song_version_name})"
            song_items.append({
                'id': song.song_db_id,
                'label': label,
                'transpose': song.transpose or 0,
                'capo': song.capo or 0,
                'ess_id': song.id,
                'event_id': event.id
            })
        section_name = section.name
        custom_name = ''
        if section_name not in DEFAULT_EVENT_SECTIONS:
            custom_name = section_name
            section_name = '__custom__'

        section_data.append({
            'name': section_name,
            'custom_name': custom_name,
            'songs': song_items,
            'songs_text': '',
            'settings': [{'transpose': s['transpose'], 'capo': s['capo']} for s in song_items]
        })
    return section_data


def save_event_from_form(event=None):
    title = request.form.get('title', '').strip()
    event_time_str = request.form.get('event_time', '').strip()
    place = request.form.get('place', '').strip()
    notes = request.form.get('notes', '').strip()

    section_names = request.form.getlist('section_name')
    section_names_custom = request.form.getlist('section_name_custom')
    section_songs = request.form.getlist('section_song_ids')
    section_song_settings_list = request.form.getlist('section_song_settings')

    if not title or not event_time_str or not place:
        flash('Vyplňte názov, dátum/čas a miesto.', 'error')
        return None, build_section_form_data(section_names, section_songs, section_names_custom, section_song_settings_list)

    try:
        event_time = datetime.fromisoformat(event_time_str)
    except ValueError:
        flash('Neplatný dátum alebo čas.', 'error')
        return None, build_section_form_data(section_names, section_songs, section_names_custom, section_song_settings_list)

    invalid_tokens = []
    sections_payload = []
    for index, (name, songs_text) in enumerate(zip(section_names, section_songs)):
        name_clean = name.strip()
        custom_name = section_names_custom[index].strip() if index < len(section_names_custom) else ''
        if name_clean == '__custom__':
            name_clean = custom_name
        songs, errors = parse_section_song_ids(songs_text)
        if errors:
            invalid_tokens.extend(errors)
        settings_json = section_song_settings_list[index] if index < len(section_song_settings_list) else '[]'
        try:
            song_settings = json.loads(settings_json or '[]')
        except (json.JSONDecodeError, TypeError):
            song_settings = []
        if name_clean or songs:
            # Deduplicate songs within the same section (keep first occurrence)
            seen_ids = set()
            deduped = []
            deduped_settings = []
            for i, s in enumerate(songs):
                if s.id not in seen_ids:
                    seen_ids.add(s.id)
                    deduped.append(s)
                    deduped_settings.append(song_settings[i] if i < len(song_settings) else {})
            if len(deduped) < len(songs):
                flash(f'Duplicitné piesne boli odstránené zo sekcie „{name_clean or "Sekcia"}".', 'warning')
            sections_payload.append({
                'name': name_clean or 'Sekcia',
                'songs': deduped,
                'settings': deduped_settings,
                'raw_text': songs_text
            })

    if invalid_tokens:
        flash(f"Neznáme piesne: {', '.join(invalid_tokens)}", 'error')
        return None, build_section_form_data(section_names, section_songs, section_names_custom, section_song_settings_list)

    sections_payload = sort_event_sections(sections_payload)

    if event is None:
        event = Event(title=title, event_time=event_time, place=place, notes=notes)
        db.session.add(event)
    else:
        event.title = title
        event.event_time = event_time
        event.place = place
        event.notes = notes

    # Smart update: preserve existing EventSectionSong IDs so URLs stay stable.
    # Build lookup of existing sections by position, and existing songs by (section_pos, song_pos).
    existing_sections = {s.position: s for s in event.sections}
    existing_songs = {}
    for sec in event.sections:
        for ess in EventSectionSong.query.filter_by(section_id=sec.id).all():
            existing_songs[(sec.position, ess.position)] = ess

    needed_section_positions = set(range(len(sections_payload)))
    # Delete sections (and their songs via cascade) that are no longer needed
    for pos, sec in list(existing_sections.items()):
        if pos not in needed_section_positions:
            db.session.delete(sec)

    db.session.flush()

    for section_index, payload in enumerate(sections_payload):
        sec = existing_sections.get(section_index)
        if sec:
            sec.name = payload['name']
            sec.position = section_index
        else:
            sec = EventSection(event=event, name=payload['name'], position=section_index)
            db.session.add(sec)
            db.session.flush()  # get sec.id

        needed_song_positions = set(range(len(payload['songs'])))
        # Delete songs at positions no longer needed
        for (sp, ep), ess in list(existing_songs.items()):
            if sp == section_index and ep not in needed_song_positions:
                db.session.delete(ess)

        for song_index, song in enumerate(payload['songs']):
            s_settings = payload.get('settings', [])
            s = s_settings[song_index] if song_index < len(s_settings) else {}
            t = int(s.get('transpose', 0) or 0)
            c = int(s.get('capo', 0) or 0)
            ess = existing_songs.get((section_index, song_index))
            if ess:
                ess.section_id = sec.id
                ess.position = song_index
                ess.song_db_id = song.id
                ess.song_code = song.song_id
                ess.song_title = song.title
                ess.song_version_name = song.version_name
                ess.transpose = t
                ess.capo = c
            else:
                db.session.add(EventSectionSong(
                    section=sec,
                    position=song_index,
                    song_db_id=song.id,
                    song_code=song.song_id,
                    song_title=song.title,
                    song_version_name=song.version_name,
                    transpose=t,
                    capo=c
                ))

    db.session.commit()
    return event, None


@app.route('/events/new', methods=['GET', 'POST'])
def event_create():
    if request.method == 'POST':
        event, section_data = save_event_from_form()
        if event:
            flash('Udalosť bola uložená.', 'success')
            return redirect(url_for('event_edit', event_id=event.id))

        return render_template('event_form.html', event=None, section_data=section_data, section_options=DEFAULT_EVENT_SECTIONS)

    section_data = [{'name': name, 'songs_text': '', 'songs': [], 'custom_name': ''} for name in DEFAULT_EVENT_SECTIONS]
    return render_template('event_form.html', event=None, section_data=section_data, section_options=DEFAULT_EVENT_SECTIONS)


@app.route('/events/<int:event_id>', methods=['GET', 'POST'])
def event_edit(event_id):
    event = Event.query.get_or_404(event_id)
    if request.method == 'POST':
        updated_event, section_data = save_event_from_form(event)
        if updated_event:
            flash('Udalosť bola aktualizovaná.', 'success')
            return redirect(url_for('event_edit', event_id=event.id))
        return render_template('event_form.html', event=event, section_data=section_data, section_options=DEFAULT_EVENT_SECTIONS)

    section_data = build_section_data_from_event(event)
    if not section_data:
        section_data = [{'name': name, 'songs_text': '', 'songs': [], 'custom_name': ''} for name in DEFAULT_EVENT_SECTIONS]
    return render_template('event_form.html', event=event, section_data=section_data, section_options=DEFAULT_EVENT_SECTIONS)


@app.route('/events/<int:event_id>/view')
def event_view(event_id):
    event = Event.query.get_or_404(event_id)

    song_ids = []
    sections = sort_event_sections([
        {'name': section.name, 'section': section} for section in event.sections
    ])
    for section_entry in sections:
        section = section_entry['section']
        for esong in section.songs:
            song_ids.append(esong.song_db_id)

    songs_by_id = {}
    if song_ids:
        songs = Song.query.filter(Song.id.in_(song_ids)).all()
        songs_by_id = {song.id: song for song in songs}

    from urllib.parse import quote
    sections_data = []
    for section_entry in sections:
        section = section_entry['section']
        section_songs = []
        for esong in section.songs:
            song = songs_by_id.get(esong.song_db_id)
            if song:
                try:
                    mp3_paths = json.loads(song.mp3_paths or '[]')
                except (json.JSONDecodeError, TypeError):
                    mp3_paths = []
                try:
                    sheet_pdf_paths = json.loads(song.sheet_pdf_paths or '[]')
                except (json.JSONDecodeError, TypeError):
                    sheet_pdf_paths = []

                section_songs.append({
                    'id': song.id,
                    'song_id': song.song_id,
                    'title': song.title,
                    'author': song.author,
                    'version_name': song.version_name,
                    'printed': song.printed,
                    'admin_checked': song.admin_checked,
                    'pdf_lyrics_path': song.pdf_lyrics_path,
                    'pdf_chords_path': song.pdf_chords_path,
                    'mp3_paths': mp3_paths,
                    'sheet_pdf_paths': sheet_pdf_paths,
                    'mp3_paths_encoded': quote(json.dumps(mp3_paths)),
                    'sheet_pdf_paths_encoded': quote(json.dumps(sheet_pdf_paths)),
                    'transpose': esong.transpose or 0,
                    'capo': esong.capo or 0,
                    'ess_id': esong.id,
                    'event_id': event_id
                })
            else:
                section_songs.append({
                    'id': esong.song_db_id,
                    'song_id': esong.song_code,
                    'title': esong.song_title,
                    'author': None,
                    'version_name': esong.song_version_name,
                    'printed': False,
                    'admin_checked': False,
                    'pdf_lyrics_path': None,
                    'pdf_chords_path': None,
                    'mp3_paths': [],
                    'sheet_pdf_paths': [],
                    'mp3_paths_encoded': quote(json.dumps([])),
                    'sheet_pdf_paths_encoded': quote(json.dumps([])),
                    'transpose': esong.transpose or 0,
                    'capo': esong.capo or 0,
                    'ess_id': esong.id,
                    'event_id': event_id
                })

        sections_data.append({
            'name': section.name,
            'songs': section_songs
        })

    return render_template('event_detail.html', event=event, sections_data=sections_data)


@app.route('/events/<int:event_id>/delete', methods=['POST'])
def event_delete(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    flash('Udalosť bola odstránená.', 'success')
    return redirect(url_for('events_list'))


@app.route('/api/event-section-song/<int:ess_id>', methods=['PATCH'])
def api_update_event_section_song(ess_id):
    from flask import jsonify
    ess = EventSectionSong.query.get_or_404(ess_id)
    data = request.get_json(silent=True) or {}
    if 'transpose' in data:
        try:
            ess.transpose = int(data['transpose'])
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid transpose'}), 400
    if 'capo' in data:
        try:
            ess.capo = int(data['capo'])
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid capo'}), 400
    if 'enharmonic_preference' in data:
        ess.enharmonic_preference = normalize_enharmonic_preference(data.get('enharmonic_preference'), default='auto')
    if 'part_enharmonic_preferences' in data:
        part_prefs = normalize_part_enharmonic_preferences(data.get('part_enharmonic_preferences'))
        ess.part_enharmonic_preferences = json.dumps(part_prefs, ensure_ascii=False) if part_prefs else None
    db.session.commit()
    return jsonify({
        'ok': True,
        'transpose': ess.transpose,
        'capo': ess.capo,
        'enharmonic_preference': ess.enharmonic_preference or 'auto',
        'part_enharmonic_preferences': normalize_part_enharmonic_preferences(ess.part_enharmonic_preferences)
    })


@app.route('/api/events-for-picker')
def api_events_for_picker():
    """Return upcoming/recent events with their sections (and songs) for the add-to-event picker."""
    events = Event.query.order_by(Event.event_time.desc()).limit(20).all()
    result = []
    for ev in events:
        sections = []
        sorted_sections = sort_event_sections([
            {'name': section.name, 'section': section} for section in ev.sections
        ])
        for section_entry in sorted_sections:
            s = section_entry['section']
            songs = EventSectionSong.query.filter_by(section_id=s.id).order_by(EventSectionSong.position).all()
            sections.append({
                'id': s.id,
                'name': s.name,
                'songs': [{'title': ess.song_title, 'code': ess.song_code} for ess in songs]
            })
        result.append({
            'id': ev.id,
            'title': ev.title,
            'event_time': ev.event_time.strftime('%d.%m.%Y %H:%M'),
            'place': ev.place,
            'sections': sections,
        })
    return jsonify(result)


@app.route('/api/event-section/<int:section_id>/add-song', methods=['POST'])
def api_add_song_to_section(section_id):
    """Insert a song into an event section at a given position."""
    section = EventSection.query.get_or_404(section_id)
    data = request.get_json(silent=True) or {}
    song_id = data.get('song_id')
    if not song_id:
        return jsonify({'error': 'song_id required'}), 400
    song = Song.query.get(int(song_id))
    if not song:
        return jsonify({'error': 'Song not found'}), 404
    # Check if song already in this section
    existing = EventSectionSong.query.filter_by(section_id=section.id, song_db_id=song.id).first()
    if existing:
        overwrite = data.get('overwrite', False)
        if overwrite:
            existing.transpose = int(data.get('transpose', 0) or 0)
            existing.capo = int(data.get('capo', 0) or 0)
            db.session.commit()
            return jsonify({'ok': True, 'ess_id': existing.id, 'already_present': True, 'overwritten': True})
        return jsonify({'ok': True, 'ess_id': existing.id, 'already_present': True, 'overwritten': False,
                        'existing_transpose': existing.transpose, 'existing_capo': existing.capo})
    # Determine insert position
    all_songs = EventSectionSong.query.filter_by(section_id=section.id).order_by(EventSectionSong.position).all()
    insert_at = data.get('insert_at')  # 0-based index; None = end
    if insert_at is None:
        insert_pos = (all_songs[-1].position + 1) if all_songs else 0
    else:
        insert_pos = int(insert_at)
        # Shift existing songs at or after insert_pos
        for ess in all_songs:
            if ess.position >= insert_pos:
                ess.position += 1
    song_part_prefs = normalize_part_enharmonic_preferences(song.part_enharmonic_preferences)
    ess = EventSectionSong(
        section=section,
        position=insert_pos,
        song_db_id=song.id,
        song_code=song.song_id,
        song_title=song.title,
        song_version_name=song.version_name,
        transpose=int(data.get('transpose', 0) or 0),
        capo=int(data.get('capo', 0) or 0),
        enharmonic_preference=normalize_enharmonic_preference(song.enharmonic_preference, default='auto'),
        part_enharmonic_preferences=json.dumps(song_part_prefs, ensure_ascii=False) if song_part_prefs else None,
    )
    db.session.add(ess)
    db.session.commit()
    return jsonify({'ok': True, 'ess_id': ess.id, 'already_present': False})


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

    total_open_reports = SongReport.query.filter_by(resolved=False).count()

    return render_template('index.html',
                         songs=songs_data,
                         total_songs=total_songs,
                         total_admin_checked=total_admin_checked,
                         total_printed=total_printed,
                         category_counts=category_counts,
                         initial_batch_size=initial_batch_size,
                         total_open_reports=total_open_reports)

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
    Display specific songs based on comma-separated database IDs in URL
    Example: /songs/1,5,23
    """
    # Parse the IDs from the URL
    try:
        id_list = [int(sid.strip()) for sid in song_ids.split(',') if sid.strip()]
    except ValueError:
        flash("Invalid song IDs provided", "error")
        return redirect(url_for('index'))

    if not id_list:
        flash("No song IDs provided", "error")
        return redirect(url_for('index'))

    # Query songs based on the provided IDs
    songs = Song.query.filter(Song.id.in_(id_list)).all()

    if not songs:
        flash("No songs found with the provided IDs", "error")
        return redirect(url_for('index'))

    # Sort songs to match the order from the URL
    songs_dict = {song.id: song for song in songs}
    ordered_songs = [songs_dict[sid] for sid in id_list if sid in songs_dict]

    from urllib.parse import quote
    songs_data = []
    for song in ordered_songs:
        try:
            mp3_paths = json.loads(song.mp3_paths or '[]')
        except (json.JSONDecodeError, TypeError):
            mp3_paths = []
        try:
            sheet_pdf_paths = json.loads(song.sheet_pdf_paths or '[]')
        except (json.JSONDecodeError, TypeError):
            sheet_pdf_paths = []
        songs_data.append({
            'id': song.id,
            'song_id': song.song_id,
            'title': song.title,
            'author': song.author,
            'author_original': song.author_original,
            'version_name': song.version_name,
            'alternative_titles': song.alternative_titles,
            'categories': song.categories,
            'printed': song.printed,
            'admin_checked': song.admin_checked,
            'pdf_lyrics_path': song.pdf_lyrics_path,
            'pdf_chords_path': song.pdf_chords_path,
            'tex_path': song.tex_path,
            'mp3_paths': mp3_paths,
            'sheet_pdf_paths': sheet_pdf_paths,
            'mp3_paths_encoded': quote(json.dumps(mp3_paths)),
            'sheet_pdf_paths_encoded': quote(json.dumps(sheet_pdf_paths)),
        })

    return render_template('songs_view.html', songs=songs_data, song_ids=song_ids)

@app.route('/stitkovac')
def songs_by_date():
    """
    Display all songs sorted by created_at or last_modified timestamp
    Parameters:
        - sort_by: 'created' or 'modified' (default: 'modified')
        - order: 'asc' or 'desc' (default: 'desc')
    """
    sort_by = request.args.get('sort_by', 'modified')
    order = request.args.get('order', 'desc')
    
    # Validate parameters
    if sort_by not in ['created', 'modified']:
        sort_by = 'modified'
    if order not in ['asc', 'desc']:
        order = 'desc'
    
    # Build the query
    if sort_by == 'created':
        if order == 'desc':
            songs = Song.query.order_by(Song.created_at.desc()).all()
        else:
            songs = Song.query.order_by(Song.created_at.asc()).all()
    else:  # modified
        if order == 'desc':
            songs = Song.query.order_by(Song.last_modified.desc()).all()
        else:
            songs = Song.query.order_by(Song.last_modified.asc()).all()
    
    return render_template('songs_by_date.html', 
                         songs=songs, 
                         sort_by=sort_by, 
                         order=order)

@app.route('/generate-labels-pdf', methods=['POST'])
def generate_labels_pdf():
    """Generate PDF labels for selected songs"""
    try:
        from label_generator import LabelGenerator
        
        # Get selected song IDs
        song_ids_str = request.form.get('song_ids', '')
        if not song_ids_str:
            flash('Nie sú vybrané žiadne piesne', 'error')
            return redirect(url_for('songs_by_date'))
        
        song_ids = [int(sid.strip()) for sid in song_ids_str.split(',') if sid.strip()]
        
        # Get selected positions (optional)
        positions_str = request.form.get('positions', '')
        positions = None
        if positions_str:
            positions = [int(p.strip()) for p in positions_str.split(',') if p.strip()]
        
        # Fetch songs from database, sorted by song_id
        songs = Song.query.filter(Song.id.in_(song_ids)).order_by(Song.song_id).all()
        
        if not songs:
            flash('Neboli nájdené žiadne piesne', 'error')
            return redirect(url_for('songs_by_date'))
        
        # Generate PDF
        generator = LabelGenerator(BASE_DIR)
        pdf_buffer = generator.generate_labels(songs, positions=positions)
        
        # Send PDF
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'stitky_{len(songs)}_songs.pdf'
        )
        
    except Exception as e:
        flash(f'Chyba pri generovaní PDF: {str(e)}', 'error')
        return redirect(url_for('songs_by_date'))

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
        # Check if this is an email request instead of direct save
        send_email = request.form.get('send_email') == 'true'
        
        if send_email:
            # Send email notification to admin
            try:
                send_edit_notification_email(song, request.form)
                flash("Zmeny boli odoslané adminovi na schválenie.", "success")
                return redirect(url_for('song_view', song_id=song.id if not is_new_song else 'new'))
            except Exception as e:
                flash(f"Chyba pri odosielaní emailu: {str(e)}", "error")
                return redirect(url_for('song_detail', song_id=song.id if not is_new_song else 'new'))
        
        # Check password for direct save
        provided_password = request.form.get('edit_password')
        if not provided_password or provided_password != EDIT_SONG_PASSWORD:
            flash("Nesprávne heslo pre uloženie zmien!", "error")
            return redirect(url_for('song_detail', song_id=song.id if not is_new_song else 'new'))

        # Snapshot old values before any mutation (for changelog)
        _FIELD_LABELS = {
            'title': 'Názov', 'author': 'Autor', 'version_name': 'Verzia',
            'title_original': 'Orig. názov', 'author_original': 'Orig. autor',
            'song_key': 'Tónina', 'enharmonic_preference': 'Preferencia akordov', 'categories': 'Kategórie',
            'part_enharmonic_preferences': 'Preferencia akordov pre časti',
            'alternative_titles': 'Alt. názvy', 'song_parts': 'Text piesne',
        }
        _FILE_LABELS = {
            'pdf_lyrics_path': 'PDF slová', 'pdf_chords_path': 'PDF akordy',
            'tex_path': 'TeX', 'mp3_paths': 'MP3',
            'midi_paths': 'MIDI', 'sheet_pdf_paths': 'Noty PDF',
            'sheet_mscz_paths': 'Noty MSCZ',
        }
        if not is_new_song:
            _snap = {
                'title': song.title or '', 'author': song.author or '',
                'version_name': song.version_name or '',
                'title_original': song.title_original or '',
                'author_original': song.author_original or '',
                'song_key': song.song_key or '',
                'enharmonic_preference': song.enharmonic_preference or 'auto',
                'part_enharmonic_preferences': song.part_enharmonic_preferences or '',
                'categories': song.categories or '',
                'alternative_titles': song.alternative_titles or '',
                'song_parts': song.song_parts or '',
                'pdf_lyrics_path': song.pdf_lyrics_path or '',
                'pdf_chords_path': song.pdf_chords_path or '',
                'tex_path': song.tex_path or '',
                'mp3_paths': song.mp3_paths or '[]',
                'midi_paths': song.midi_paths or '[]',
                'sheet_pdf_paths': song.sheet_pdf_paths or '[]',
                'sheet_mscz_paths': song.sheet_mscz_paths or '[]',
            }
        else:
            _snap = None
        
        try:
            # Update song fields - SANITIZE ALL TEXT INPUTS TO PREVENT XSS
            song.title = sanitize_input(request.form['title'], "title")
            song.author = sanitize_input(request.form['author'], "author") if request.form.get('author') and request.form.get('author').strip() else None
            song.version_name = sanitize_input(request.form.get('version_name', ''), "version_name") if request.form.get('version_name') else None

            song.title_original = sanitize_input(request.form.get('title_original', ''), "original title")
            song.author_original = sanitize_input(request.form.get('author_original', ''), "original author")
            song.song_key = sanitize_input(request.form.get('song_key', ''), "song key") if request.form.get('song_key') and request.form.get('song_key').strip() else None
            song.enharmonic_preference = normalize_enharmonic_preference(request.form.get('enharmonic_preference', 'auto'), default='auto')

            # Reset admin_checked when song is edited (requires re-verification)
            song.admin_checked = False

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

            part_prefs = {}
            for idx, _part in enumerate(parts):
                mode = normalize_enharmonic_preference(
                    request.form.get(f'part_enharmonic_preference_{idx}'),
                    default='auto'
                )
                if mode != 'auto':
                    part_prefs[str(idx)] = mode
            song.part_enharmonic_preferences = json.dumps(part_prefs, ensure_ascii=False) if part_prefs else None
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for('song_detail', song_id=song.id if not is_new_song else 'new'))

        # For new songs, add to session first to get an ID
        if is_new_song:
            db.session.add(song)
            db.session.commit()  # Commit to get song ID

        # Check if user confirmed to replace existing files
        force_replace = request.form.get('force_replace') == 'true'

        # Track same-name file replacements (path unchanged but file content replaced)
        _replaced_same_name = []  # list of {'field': ..., 'filename': ...}

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

                # Track force-replace with same filename (path won't change → invisible to diff)
                if current_path and current_path == new_path and force_replace:
                    _replaced_same_name.append({'field': field_name, 'filename': filename})
                
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
        _mp3_rep, _midi_rep, _sheet_rep, _mscz_rep = [], [], [], []
        song.mp3_paths = update_multi_file_paths(song.mp3_paths, request.files.getlist('mp3s'), song.id, 'mp3s', force_replace=force_replace, replacements_out=_mp3_rep)
        song.midi_paths = update_multi_file_paths(song.midi_paths, request.files.getlist('midis'), song.id, 'midis', force_replace=force_replace, replacements_out=_midi_rep)
        song.sheet_pdf_paths = update_multi_file_paths(song.sheet_pdf_paths, request.files.getlist('sheet_pdfs'), song.id, 'sheets', force_replace=force_replace, replacements_out=_sheet_rep)
        song.sheet_mscz_paths = update_multi_file_paths(song.sheet_mscz_paths, request.files.getlist('sheet_mscz'), song.id, 'mscz', force_replace=force_replace, replacements_out=_mscz_rep)
        for _fn in _mp3_rep:   _replaced_same_name.append({'field': 'mp3_paths',        'filename': _fn})
        for _fn in _midi_rep:  _replaced_same_name.append({'field': 'midi_paths',       'filename': _fn})
        for _fn in _sheet_rep: _replaced_same_name.append({'field': 'sheet_pdf_paths',  'filename': _fn})
        for _fn in _mscz_rep:  _replaced_same_name.append({'field': 'sheet_mscz_paths', 'filename': _fn})

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

        # Build and save changelog entry
        try:
            field_changes = []
            file_changes = []
            if _snap is None:
                # New song — record all non-empty fields as "added"
                for f, lbl in _FIELD_LABELS.items():
                    val = (getattr(song, f) or '')
                    if f == 'song_parts':
                        parts_data = json.loads(val) if val else []
                        if parts_data:
                            field_changes.append({'field': f, 'label': lbl, 'old': '', 'new': f'{len(parts_data)} čast(í)'})
                    elif val:
                        field_changes.append({'field': f, 'label': lbl, 'old': '', 'new': val})
                for f, lbl in _FILE_LABELS.items():
                    val = getattr(song, f) or ''
                    if f.endswith('_paths'):
                        for p in json.loads(val if val != '' else '[]'):
                            file_changes.append({'filename': p.split('/')[-1], 'action': 'added', 'type': lbl})
                    elif val:
                        file_changes.append({'filename': val.split('/')[-1], 'action': 'added', 'type': lbl})
            else:
                # Existing song — diff
                for f, lbl in _FIELD_LABELS.items():
                    old_val = _snap.get(f, '')
                    new_val = getattr(song, f) or ''
                    if f == 'song_parts':
                        if old_val != new_val:
                            def _flatten_parts(parts_json):
                                lines = []
                                try:
                                    for part in json.loads(parts_json or '[]'):
                                        lines.append(f"[{part.get('type','?')}]")
                                        lines.extend(part.get('lines', []))
                                except Exception:
                                    pass
                                return lines
                            old_lines = _flatten_parts(old_val)
                            new_lines = _flatten_parts(new_val)
                            raw = list(difflib.unified_diff(old_lines, new_lines, lineterm='', n=0))
                            diff_entries = []
                            for dl in raw:
                                if dl.startswith('+++') or dl.startswith('---') or dl.startswith('@@'):
                                    continue
                                if dl.startswith('+'):
                                    diff_entries.append({'t': '+', 'text': dl[1:]})
                                elif dl.startswith('-'):
                                    diff_entries.append({'t': '-', 'text': dl[1:]})
                            if diff_entries:
                                field_changes.append({'field': 'song_parts', 'label': lbl,
                                                      'diff': diff_entries[:80]})
                    elif old_val != new_val:
                        field_changes.append({'field': f, 'label': lbl, 'old': old_val, 'new': new_val})
                for f, lbl in _FILE_LABELS.items():
                    if f.endswith('_paths'):
                        old_set = set(json.loads(_snap.get(f, '[]') or '[]'))
                        new_set = set(json.loads(getattr(song, f) or '[]'))
                        for p in new_set - old_set:
                            file_changes.append({'filename': p.split('/')[-1], 'action': 'added', 'type': lbl})
                        for p in old_set - new_set:
                            file_changes.append({'filename': p.split('/')[-1], 'action': 'removed', 'type': lbl})
                    else:
                        old_val = _snap.get(f, '')
                        new_val = getattr(song, f) or ''
                        if old_val != new_val:
                            fname_old = old_val.split('/')[-1] if old_val else ''
                            fname_new = new_val.split('/')[-1] if new_val else ''
                            if fname_new:
                                file_changes.append({'filename': fname_new, 'action': 'added', 'type': lbl})
                            if fname_old:
                                file_changes.append({'filename': fname_old, 'action': 'removed', 'type': lbl})
                # Same-name replacements (path unchanged → invisible to set diff above)
                for rep in _replaced_same_name:
                    lbl = _FILE_LABELS.get(rep['field'], rep['field'])
                    file_changes.append({'filename': rep['filename'], 'action': 'replaced', 'type': lbl})

            if field_changes or file_changes or _snap is None:
                log = SongChangeLog(
                    song_db_id=song.id,
                    changed_at=datetime.utcnow(),
                    is_new_song=(_snap is None),
                    field_changes=json.dumps(field_changes, ensure_ascii=False),
                    file_changes=json.dumps(file_changes, ensure_ascii=False),
                )
                db.session.add(log)
        except Exception:
            pass  # changelog errors must not block the save

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
    song_part_enharmonic_preferences = normalize_part_enharmonic_preferences(song.part_enharmonic_preferences)

    return render_template('song_detail.html',
                         song=song,
                         data=data,
                         song_part_enharmonic_preferences=song_part_enharmonic_preferences,
                         mp3s=mp3s,
                         midis=midis,
                         sheet_pdfs=sheet_pdfs,
                         sheet_mscz=sheet_mscz,
                         is_edit=not is_new_song)

@app.route('/api/song/<int:song_id>/parts', methods=['GET'])
def get_song_parts(song_id):
    """Return song_parts JSON for the admin resolve panel."""
    song = Song.query.get_or_404(song_id)
    parts = json.loads(song.song_parts or '[]')
    return jsonify({'parts': parts})

@app.route('/api/song/<int:song_id>/report', methods=['POST'])
def submit_report(song_id):
    """Submit a report (wrong key / lyrics / chords / author / other)."""
    from datetime import datetime
    song = Song.query.get_or_404(song_id)
    data = request.get_json() or {}
    report_type = data.get('type', 'other').strip()
    if report_type not in ('key', 'lyrics', 'chords', 'author', 'other'):
        return jsonify({'success': False, 'message': 'Neplatný typ hlásenia'}), 400
    message = (data.get('message') or '').strip()[:500]
    reporter_name = (data.get('reporter_name') or '').strip()[:100]
    # For key reports also flag the song column for the widget
    if report_type == 'key':
        song.key_reported = True
    report = SongReport(
        song_db_id=song.id,
        report_type=report_type,
        message=message or None,
        reporter_name=reporter_name or None,
        created_at=datetime.utcnow(),
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/song/<int:song_id>/preview-preferences', methods=['PATCH'])
def update_song_preview_preferences(song_id):
    song = Song.query.get_or_404(song_id)
    data = request.get_json(silent=True) or {}
    song.enharmonic_preference = normalize_enharmonic_preference(
        data.get('enharmonic_preference'),
        default='auto'
    )
    part_prefs = normalize_part_enharmonic_preferences(data.get('part_enharmonic_preferences'))
    song.part_enharmonic_preferences = json.dumps(part_prefs, ensure_ascii=False) if part_prefs else None
    db.session.commit()
    return jsonify({
        'success': True,
        'enharmonic_preference': song.enharmonic_preference,
        'part_enharmonic_preferences': part_prefs
    })


@app.route('/api/report/<int:report_id>/resolve', methods=['POST'])
def resolve_report(report_id):
    """Admin: mark a report as resolved (optionally with a note)."""
    report = SongReport.query.get_or_404(report_id)
    data = request.get_json() or {}
    provided_password = data.get('password', '')
    if not is_admin_authorized(provided_password):
        return jsonify({'success': False, 'message': 'Nesprávne heslo!'}), 403
    report.resolved = True
    report.resolved_note = (data.get('note') or '').strip()[:500] or None
    # If resolving a key report, clear the song flag too
    if report.report_type == 'key':
        open_key_reports = SongReport.query.filter_by(
            song_db_id=report.song_db_id, report_type='key', resolved=False
        ).count()
        if open_key_reports == 0:
            report.song.key_reported = False
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/report/<int:report_id>', methods=['DELETE'])
def delete_report(report_id):
    """Admin: delete a report entirely."""
    report = SongReport.query.get_or_404(report_id)
    data = request.get_json() or {}
    provided_password = data.get('password', '')
    if not is_admin_authorized(provided_password):
        return jsonify({'success': False, 'message': 'Nesprávne heslo!'}), 403
    db.session.delete(report)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/song/<int:song_id>/correct-key', methods=['POST'])
def correct_key(song_id):
    """Admin: update the key and clear key_reported flag + resolve open key reports."""
    from datetime import datetime
    song = Song.query.get_or_404(song_id)
    data = request.get_json() or {}
    provided_password = data.get('password', '')
    if not is_admin_authorized(provided_password):
        return jsonify({'success': False, 'message': 'Nesprávne heslo!'}), 403
    raw_key = data.get('key', '').strip()
    old_key = song.song_key or ''
    song.song_key = sanitize_input(raw_key, "song key") if raw_key else None
    song.key_reported = False
    SongReport.query.filter_by(song_db_id=song.id, report_type='key', resolved=False).update({'resolved': True})
    log = SongChangeLog(
        song_db_id=song.id,
        changed_at=datetime.utcnow(),
        field_changes=json.dumps([{'field': 'song_key', 'label': 'Tónina', 'old': old_key, 'new': song.song_key or ''}]),
    )
    db.session.add(log)
    db.session.commit()
    return jsonify({'success': True, 'key': song.song_key})


@app.route('/api/song/<int:song_id>/correct-lyrics', methods=['POST'])
def correct_lyrics(song_id):
    """Admin: apply word/chord text corrections to song_parts and submit a resolved report."""
    song = Song.query.get_or_404(song_id)
    data = request.get_json() or {}
    provided_password = data.get('password', '')
    if not is_admin_authorized(provided_password):
        return jsonify({'success': False, 'message': 'Nesprávne heslo!'}), 403

    report_type = data.get('report_type', 'lyrics')   # 'lyrics' or 'chords'
    corrections = data.get('corrections', [])          # [{partIdx, lineIdx, original, replacement}, ...]
    reporter_name = data.get('reporter_name', '').strip()
    user_message = data.get('message', '').strip()

    if not corrections:
        return jsonify({'success': False, 'message': 'Žiadne opravy.'}), 400

    parts = json.loads(song.song_parts or '[]')
    # build label→index map for fallback lookup
    label_to_idx = {p.get('type', '').strip().lower(): i for i, p in enumerate(parts)}

    applied = []
    for c in corrections:
        try:
            original = str(c['original'])
            replacement = str(c['replacement'])
            line_idx = int(c['lineIdx'])
        except (KeyError, ValueError, TypeError):
            continue
        # resolve partIdx – prefer explicit, fall back to partLabel search
        if 'partIdx' in c:
            part_idx = int(c['partIdx'])
        else:
            label = str(c.get('partLabel', '')).strip().lower()
            part_idx = label_to_idx.get(label, -1)
        if part_idx < 0 or part_idx >= len(parts):
            continue
        lines = parts[part_idx].get('lines', [])
        if line_idx < 0 or line_idx >= len(lines):
            continue
        if report_type == 'chords':
            # replace the nth occurrence of [original] → [replacement]
            occ_idx = int(c.get('occurrenceIdx', 0))
            old_chord = f'[{original}]'
            new_chord = f'[{replacement}]'
            line = lines[line_idx]
            count = 0
            pos = 0
            segments = []
            while True:
                idx = line.find(old_chord, pos)
                if idx == -1:
                    segments.append(line[pos:])
                    break
                if count == occ_idx:
                    segments.append(line[pos:idx])
                    segments.append(new_chord)
                    segments.append(line[idx + len(old_chord):])
                    break
                segments.append(line[pos:idx + len(old_chord)])
                pos = idx + len(old_chord)
                count += 1
            parts[part_idx]['lines'][line_idx] = ''.join(segments)
        else:
            # word replacement (plain text, case-sensitive, whole-word aware)
            import re as _re
            pattern = _re.compile(_re.escape(original))
            parts[part_idx]['lines'][line_idx] = pattern.sub(replacement, lines[line_idx], count=1)
        applied.append(c)

    if not applied:
        return jsonify({'success': False, 'message': 'Žiadne zhody v texte.'}), 400

    song.song_parts = json.dumps(parts, ensure_ascii=False)
    song.update_search_text()

    # Build message summary for the auto-created report
    corr_lines = []
    for c in applied:
        occ_idx = int(c.get('occurrenceIdx', 0))
        occ_note = f' (#{occ_idx + 1})' if occ_idx > 0 else ''
        corr_lines.append(f'[{c.get("partLabel","?")} r.{int(c["lineIdx"])+1}] "{c["original"]}"{occ_note} → "{c["replacement"]}"')
    full_message = '\n'.join(corr_lines)
    if user_message:
        full_message += '\n' + user_message

    report = SongReport(
        song_db_id=song.id,
        report_type=report_type,
        message=full_message,
        reporter_name=reporter_name or 'admin',
        created_at=datetime.now(),
        resolved=True,
        resolved_note='Opravené priamo adminom.',
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({'success': True, 'applied': len(applied)})


def is_admin_authorized(provided_password=''):
    """Return True if the request is from a logged-in admin session or correct password."""
    return session.get('is_admin') or (provided_password and provided_password == UPDATE_SONG_PASSWORD)


@app.route('/admin')
def admin_index():
    """Admin home page."""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    total_open = SongReport.query.filter_by(resolved=False).count()
    type_counts = {}
    for t in ('key', 'lyrics', 'chords', 'author', 'other'):
        type_counts[t] = SongReport.query.filter_by(report_type=t, resolved=False).count()
    pending_songs = Song.query.filter_by(admin_checked=False).count()
    return render_template('admin_index.html',
                           total_open=total_open,
                           type_counts=type_counts,
                           pending_songs=pending_songs)


@app.route('/admin/songs')
def admin_songs():
    """Admin: list songs needing review (admin_checked=False)."""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    songs = Song.query.filter_by(admin_checked=False)\
                      .order_by(Song.last_modified.desc().nullslast(), Song.id.desc()).all()
    # Attach changelogs since last approval per song
    changelogs = {}
    for song in songs:
        cutoff = song.last_approved_at
        logs = song.changelogs.all()
        if cutoff:
            logs = [l for l in logs if l.changed_at > cutoff]
        changelogs[song.id] = logs  # newest first
    return render_template('admin_songs.html', songs=songs, changelogs=changelogs)


@app.route('/admin/songs/approved')
def admin_songs_approved():
    """Admin: list changelog entries for approved songs, newest first, only if they have logs."""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    # Fetch all changelog entries for approved songs, ordered newest first
    logs = (SongChangeLog.query
            .join(Song, SongChangeLog.song_db_id == Song.id)
            .filter(Song.admin_checked == True)
            .order_by(SongChangeLog.changed_at.desc())
            .all())
    return render_template('admin_songs_approved.html', logs=logs)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page — sets a session flag on correct password."""
    if session.get('is_admin'):
        return redirect(url_for('admin_index'))
    error = None
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw and pw == UPDATE_SONG_PASSWORD:
            session['is_admin'] = True
            session.permanent = True
            return redirect(url_for('admin_index'))
        error = 'Nesprávne heslo.'
    return render_template('admin_login.html', error=error)


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('index'))


@app.route('/admin/reports')
def admin_reports():
    """Admin page: table of all song reports."""
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    type_filter = request.args.get('type', '')
    status_filter = request.args.get('status', 'open')
    query = SongReport.query.join(Song, SongReport.song_db_id == Song.id)
    if type_filter:
        query = query.filter(SongReport.report_type == type_filter)
    if status_filter == 'open':
        query = query.filter(SongReport.resolved == False)
    elif status_filter == 'resolved':
        query = query.filter(SongReport.resolved == True)
    reports = query.order_by(SongReport.created_at.desc()).all()
    type_counts = {}
    for t in ('key', 'lyrics', 'chords', 'author', 'other'):
        type_counts[t] = SongReport.query.filter_by(report_type=t, resolved=False).count()
    total_open = SongReport.query.filter_by(resolved=False).count()
    return render_template('admin_reports.html',
                           reports=reports,
                           type_filter=type_filter,
                           status_filter=status_filter,
                           type_counts=type_counts,
                           total_open=total_open,
                           session_admin=True)


@app.route('/song/<int:song_id>/toggle-admin-check', methods=['POST'])
def toggle_admin_check(song_id):
    """Toggle admin_checked status with password protection"""
    song = Song.query.get_or_404(song_id)
    
    # Get the desired state and password
    data = request.get_json()
    new_state = data.get('checked', False)
    provided_password = data.get('password', '')
    
    # If trying to check (not uncheck) and it wasn't checked before, require password
    if new_state and not song.admin_checked:
        if not is_admin_authorized(provided_password):
            return jsonify({'success': False, 'message': 'Nesprávne heslo!'}), 403
    
    # Update the state
    song.admin_checked = new_state
    if new_state:
        song.last_approved_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True, 
        'checked': song.admin_checked,
        'message': 'Pieseň označená ako skontrolovaná' if new_state else 'Označenie kontroly zrušené'
    })

@app.route('/song/<int:song_id>/toggle-printed', methods=['POST'])
def toggle_printed(song_id):
    """Toggle printed status"""
    song = Song.query.get_or_404(song_id)
    
    # Get the desired state
    data = request.get_json()
    new_state = data.get('printed', False)
    
    # Update the state
    song.printed = new_state
    db.session.commit()
    
    return jsonify({
        'success': True, 
        'printed': song.printed,
        'message': 'Pieseň označená ako vytlačená' if new_state else 'Označenie tlače zrušené'
    })

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

    try:
        view_transpose = int(request.args.get('transpose', '0'))
    except ValueError:
        view_transpose = 0
    try:
        view_capo = int(request.args.get('capo', '0'))
    except ValueError:
        view_capo = 0
    return render_template('song_view.html', song=song, data=data, mp3s=mp3s, midis=midis, sheet_pdfs=sheet_pdfs, sheet_mscz=sheet_mscz, view_transpose=view_transpose, view_capo=view_capo)

@app.route('/song/<int:song_id>/preview')
def song_preview(song_id):
    """Minimal, styled preview of lyrics with enlarged chords."""
    song = Song.query.get_or_404(song_id)

    try:
        data = json.loads(song.song_parts or '[]')
    except (json.JSONDecodeError, TypeError):
        data = []

    show_chords = request.args.get('chords', '1') != '0'
    song_enharmonic_preference = normalize_enharmonic_preference(song.enharmonic_preference, default='auto')
    song_part_enharmonic_preferences = normalize_part_enharmonic_preferences(song.part_enharmonic_preferences)
    try:
        ess_id = int(request.args.get('ess_id', '0')) or None
    except ValueError:
        ess_id = None

    initial_enharmonic_preference = song_enharmonic_preference
    initial_part_enharmonic_preferences = song_part_enharmonic_preferences

    if ess_id:
        ess = EventSectionSong.query.get(ess_id)
        if ess:
            initial_transpose = ess.transpose or 0
            initial_capo = ess.capo or 0
            initial_enharmonic_preference = normalize_enharmonic_preference(
                ess.enharmonic_preference or song_enharmonic_preference,
                default=song_enharmonic_preference
            )
            initial_part_enharmonic_preferences = normalize_part_enharmonic_preferences(
                ess.part_enharmonic_preferences or song.part_enharmonic_preferences
            )
        else:
            ess_id = None
            initial_transpose = 0
            initial_capo = 0
    else:
        try:
            initial_transpose = int(request.args.get('transpose', '0'))
        except ValueError:
            initial_transpose = 0
        try:
            initial_capo = int(request.args.get('capo', '0'))
        except ValueError:
            initial_capo = 0
    return render_template(
        'song_preview.html',
        song=song,
        data=data,
        back_url=request.referrer,
        show_chords=show_chords,
        initial_transpose=initial_transpose,
        initial_capo=initial_capo,
        initial_enharmonic_preference=initial_enharmonic_preference,
        initial_part_enharmonic_preferences=initial_part_enharmonic_preferences,
        ess_id=ess_id
    )

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
    def format_chord_display(chord_text):
        raw = chord_text.strip()
        if not raw:
            return raw

        optional = raw.startswith('(') and raw.endswith(')') and len(raw) > 2
        inner = raw[1:-1].strip() if optional else raw
        inner = inner.replace('\\', '/')

        def normalize_part(part):
            match = re.match(r'^([A-Ha-h])([#b]?)(.*)$', part)
            if not match:
                return part
            letter, accidental, rest = match.groups()
            is_lower = letter.islower()
            letter = letter.upper()
            rest = rest.strip()
            rest_lower = rest.lower()

            if rest in ('2', '4'):
                rest = rest
            elif rest_lower == 'sus2':
                rest = '2'
            elif rest_lower == 'sus4':
                rest = '4'

            is_minor = False
            if rest_lower.startswith('min'):
                is_minor = True
                rest = rest[3:]
            elif rest_lower.startswith('m') and not rest_lower.startswith('maj'):
                is_minor = True
                rest = rest[1:]

            if is_minor:
                letter = letter.lower()
            elif is_lower:
                letter = letter.lower()

            root = f"{letter}{accidental}"
            return f"{root}{rest}"

        parts = [p.strip() for p in inner.split('/')]
        normalized_parts = [normalize_part(p) for p in parts if p]
        normalized = '/'.join(normalized_parts) if normalized_parts else inner

        if optional:
            normalized = f"({normalized})"
        return normalized

    def render_chord_html(chord_text):
        formatted = format_chord_display(chord_text)
        optional = formatted.startswith('(') and formatted.endswith(')') and len(formatted) > 2
        inner = formatted[1:-1] if optional else formatted

        def split_root(chord_part):
            match = re.match(r'^([A-Ha-h])([#b]?)(.*)$', chord_part)
            if not match:
                return chord_part, ''
            letter, accidental, rest = match.groups()
            return f"{letter}{accidental}", rest

        parts = [p.strip() for p in inner.split('/')]
        html_parts = []
        for part in parts:
            root, rest = split_root(part)
            root_html = f"<span class='chord-root' style='font-size:1em'>{root}</span>"
            ext_html = ''
            if rest:
                ext_html = (
                    "<span class='chord-ext' "
                    "style='font-size:0.6em; vertical-align:super'>"
                    f"{rest}</span>"
                )
            html_parts.append(f"{root_html}{ext_html}")

        body = '/'.join(html_parts)
        if optional:
            body = f"({body})"

        raw_escaped = chord_text.replace('&', '&amp;').replace('"', '&quot;')
        return f"<sup class='chord' style='color:orange; font-size:1.1em' data-raw=\"{raw_escaped}\"><strong>{body}</strong></sup>"

    return Markup(re.sub(r"\[([^\]]+)\]", lambda m: render_chord_html(m.group(1)), text))

# Template filter for JSON parsing
@app.template_filter('parse_json')
def parse_json_filter(text):
    if not text:
        return []
    if isinstance(text, list):
        return text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []

@app.template_filter('from_json')
def from_json_filter(text):
    if not text:
        return []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []

@app.template_filter('roman')
def roman_numeral_filter(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n <= 0 or n > 39:
        return str(n)
    vals = [(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]
    result = ''
    for v, s in vals:
        while n >= v:
            result += s
            n -= v
    return result

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

@app.route('/api/check-update-password', methods=['POST'])
def check_update_password():
    """API endpoint to validate update/admin check password"""
    try:
        data = request.get_json()
        provided_password = data.get('password', '')

        if provided_password == UPDATE_SONG_PASSWORD:
            return jsonify({'valid': True})
        else:
            return jsonify({'valid': False, 'message': 'Nesprávne heslo'})

    except Exception as e:
        return jsonify({'valid': False, 'message': 'Chyba servera'}), 500

@app.route('/api/check-edit-password', methods=['POST'])
def check_edit_password():
    """API endpoint to validate edit password"""
    try:
        data = request.get_json()
        provided_password = data.get('password', '')

        if provided_password == EDIT_SONG_PASSWORD:
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

@app.route('/api/search_cards')
def search_cards_html():
    """Returns server-rendered HTML cards for search results (uses _song_card.html macro)"""
    from unidecode import unidecode
    from urllib.parse import quote
    import re

    query = request.args.get('q', '').strip()
    printed_filter = request.args.get('printed')
    unchecked_filter = request.args.get('unchecked')
    categories_filter = request.args.get('categories')
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = max(int(request.args.get('offset', 0)), 0)

    query_obj = Song.query

    if query:
        query_no_chords = re.sub(r'\[[^\]]*\]', '', query)
        normalized_query = unidecode(query_no_chords.lower()).replace(",", " ").replace(".", " ").replace("-", " ").replace("_", " ").replace(";", " ").strip()
        normalized_query = re.sub(r'\s+', ' ', normalized_query)
        query_obj = query_obj.filter(Song.search_text.like(f'%{normalized_query}%'))

    if printed_filter == 'true':
        query_obj = query_obj.filter(Song.printed == True)
    elif printed_filter == 'false':
        query_obj = query_obj.filter(Song.printed == False)

    if unchecked_filter == 'true':
        query_obj = query_obj.filter(Song.admin_checked == False)

    if categories_filter:
        for cat in [c.strip().lower() for c in categories_filter.split(',') if c.strip()]:
            query_obj = query_obj.filter(Song.categories.ilike(f'%{cat}%'))

    songs = query_obj.order_by(Song.song_id).offset(offset).limit(limit).all()

    songs_data = []
    for song in songs:
        try:
            mp3_paths = json.loads(song.mp3_paths or '[]')
        except (json.JSONDecodeError, TypeError):
            mp3_paths = []
        try:
            sheet_pdf_paths = json.loads(song.sheet_pdf_paths or '[]')
        except (json.JSONDecodeError, TypeError):
            sheet_pdf_paths = []
        songs_data.append({
            'id': song.id,
            'song_id': song.song_id,
            'title': song.title,
            'author': song.author,
            'version_name': song.version_name,
            'categories': song.categories,
            'printed': song.printed,
            'admin_checked': song.admin_checked,
            'pdf_lyrics_path': song.pdf_lyrics_path,
            'pdf_chords_path': song.pdf_chords_path,
            'mp3_paths': mp3_paths,
            'sheet_pdf_paths': sheet_pdf_paths,
            'mp3_paths_encoded': quote(json.dumps(mp3_paths)),
            'sheet_pdf_paths_encoded': quote(json.dumps(sheet_pdf_paths)),
        })

    html = render_template('_search_cards_fragment.html', songs=songs_data)
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}

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