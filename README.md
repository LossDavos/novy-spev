# Nový Spev - Song Management System

A Flask-based web application for managing church songs and generating LaTeX/PDF songbooks.

## Features

- Song database management
- LaTeX songbook generation
- PDF processing and stamping
- AWS S3 integration for file storage
- Web interface for song management

## Requirements

- Python 3.11+
- LaTeX (for PDF generation)
- SQLite database
- AWS credentials (for S3 integration)

## Setup

### Local Development

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create a `.env` file with your AWS credentials:
   ```
   S3_BUCKET=your-bucket-name
   AWS_ACCESS_KEY_ID=your-access-key
   AWS_SECRET_ACCESS_KEY=your-secret-key
   AWS_REGION=your-region
   ```
5. Run the application:
   ```bash
   python app.py
   ```

### Docker Deployment

1. Build and run with Docker Compose:
   ```bash
   docker-compose up --build
   ```

2. Or build and run with Docker directly:
   ```bash
   docker build -t novy_spev .
   docker run -p 5000:5000 -v $(pwd)/instance:/app/instance novy_spev
   ```

## Usage

The application will be available at `http://localhost:5000`

## Project Structure

- `app.py` - Main Flask application
- `models.py` - Database models
- `generate_tex.py` - LaTeX generation utilities
- `stamper.py` - PDF processing utilities
- `templates/` - HTML templates
- `static/` - Static files (CSS, JS, images)
- `songs/` - Song files storage
- `instance/` - SQLite database location
