"""
Storage abstraction layer - handles both local and S3 storage
Toggle between local/S3 using config.USE_S3_STORAGE
"""
import os
import boto3
from werkzeug.utils import secure_filename
from flask import current_app
import config

class StorageBackend:
    """Base class for storage backends"""
    
    def save_file(self, file, folder, filename=None):
        """Save a file and return its path/key"""
        raise NotImplementedError
    
    def delete_file(self, path):
        """Delete a file"""
        raise NotImplementedError
    
    def get_url(self, path, expires_in=3600):
        """Get a URL to access the file"""
        raise NotImplementedError
    
    def file_exists(self, path):
        """Check if file exists"""
        raise NotImplementedError


class LocalStorage(StorageBackend):
    """Local filesystem storage"""
    
    def __init__(self, base_path):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)
    
    def save_file(self, file, folder, filename=None):
        """
        Save file to local filesystem
        Returns: relative path from base (e.g., '123/song.mp3')
        """
        if filename is None:
            filename = secure_filename(file.filename)
        
        # Create folder path relative to base
        folder_path = os.path.join(self.base_path, folder)
        os.makedirs(folder_path, exist_ok=True)
        
        # Full filesystem path
        file_path = os.path.join(folder_path, filename)
        file.save(file_path)
        
        # Return relative path (for database storage)
        return os.path.join(folder, filename)
    
    def delete_file(self, relative_path):
        """Delete file from local filesystem"""
        if not relative_path:
            return False
        
        full_path = os.path.join(self.base_path, relative_path)
        if os.path.exists(full_path):
            os.remove(full_path)
            return True
        return False
    
    def get_url(self, relative_path, expires_in=None):
        """
        Get URL for local file
        Returns: Flask route path (e.g., '/uploads/123/song.mp3')
        """
        if not relative_path:
            return None
        # Return route path that will be served by Flask
        return f'/uploads/{relative_path}'
    
    def file_exists(self, relative_path):
        """Check if file exists locally"""
        if not relative_path:
            return False
        full_path = os.path.join(self.base_path, relative_path)
        return os.path.exists(full_path)
    
    def get_absolute_path(self, relative_path):
        """Get absolute filesystem path (for internal use)"""
        if not relative_path:
            return None
        return os.path.join(self.base_path, relative_path)


class S3Storage(StorageBackend):
    """AWS S3 storage"""
    
    def __init__(self, bucket, access_key, secret_key, region):
        self.bucket = bucket
        
        session = boto3.session.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region
        )
        
        self.s3_client = session.client(
            "s3",
            config=boto3.session.Config(
                s3={'addressing_style': 'virtual'},
                signature_version='s3v4'
            )
        )
    
    def save_file(self, file, folder, filename=None):
        """
        Upload file to S3
        Returns: S3 key (e.g., 'mp3s/123/song.mp3')
        """
        if filename is None:
            filename = secure_filename(file.filename)
        
        # S3 key (path in bucket)
        key = f"{folder}/{filename}"
        
        try:
            # Reset file pointer to beginning
            file.seek(0)
            
            self.s3_client.upload_fileobj(
                file,
                self.bucket,
                key,
                ExtraArgs={'ContentType': file.content_type or 'application/octet-stream'}
            )
            return key
        except Exception as e:
            print(f"S3 upload error: {e}")
            raise
    
    def delete_file(self, s3_key):
        """Delete file from S3"""
        if not s3_key:
            return False
        
        try:
            self.s3_client.delete_object(Bucket=self.bucket, Key=s3_key)
            return True
        except Exception as e:
            print(f"S3 delete error: {e}")
            return False
    
    def get_url(self, s3_key, expires_in=3600):
        """
        Get presigned URL for S3 file
        Returns: Temporary signed URL
        """
        if not s3_key:
            return None
        
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket, 'Key': s3_key},
                ExpiresIn=expires_in
            )
            return url
        except Exception as e:
            print(f"Error generating presigned URL for {s3_key}: {e}")
            return None
    
    def file_exists(self, s3_key):
        """Check if file exists in S3"""
        if not s3_key:
            return False
        
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except:
            return False


# Factory function to get the appropriate storage backend
def get_storage():
    """
    Get storage backend based on config
    Returns: StorageBackend instance (LocalStorage or S3Storage)
    """
    if config.USE_S3_STORAGE:
        # Validate S3 configuration
        if not all([config.S3_BUCKET, config.AWS_ACCESS_KEY_ID, config.AWS_SECRET_ACCESS_KEY]):
            print("WARNING: S3 storage enabled but credentials not configured. Falling back to local storage.")
            return LocalStorage(config.UPLOAD_FOLDER)
        
        return S3Storage(
            bucket=config.S3_BUCKET,
            access_key=config.AWS_ACCESS_KEY_ID,
            secret_key=config.AWS_SECRET_ACCESS_KEY,
            region=config.AWS_REGION
        )
    else:
        return LocalStorage(config.UPLOAD_FOLDER)


# Singleton instance
_storage = None

def init_storage():
    """Initialize storage backend (call once at app startup)"""
    global _storage
    _storage = get_storage()
    print(f"Storage backend initialized: {'S3' if config.USE_S3_STORAGE else 'Local'}")
    return _storage

def storage():
    """Get current storage instance"""
    global _storage
    if _storage is None:
        _storage = init_storage()
    return _storage
