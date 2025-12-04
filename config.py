"""
Application configuration with S3 toggle
"""
import os
from dotenv import load_dotenv

# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Storage configuration - TOGGLE S3 HERE
USE_S3_STORAGE = os.getenv('USE_S3_STORAGE', 'false').lower() == 'true'

# S3 Configuration (only used if USE_S3_STORAGE is True)
S3_BUCKET = os.getenv("S3_BUCKET")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Local storage paths (used when USE_S3_STORAGE is False, or as fallback)
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')  # Outside static/
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
BACKUP_FOLDER = os.path.join(BASE_DIR, 'instance', 'backups')
JSON_FOLDER = os.path.join(BASE_DIR, 'songs')

# Database
DATABASE_URI = 'sqlite:///songs.db'

# Security
SECRET_KEY = os.getenv('SECRET_KEY', '')
DELETE_SONG_PASSWORD = os.getenv("DELETE_SONG_PASSWORD")
UPDATE_SONG_PASSWORD = os.getenv("UPDATE_SONG_PASSWORD")
EDIT_SONG_PASSWORD = os.getenv("EDIT_SONG_PASSWORD")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "david.stevanak@gmail.com")

# File upload settings
ALLOWED_EXTENSIONS = {'mp3', 'pdf', 'midi', 'mid', 'tex', 'mscz'}
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max upload

# S3 presigned URL expiration (in seconds)
S3_PRESIGNED_URL_EXPIRATION = 3600  # 1 hour
