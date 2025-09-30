# Nový Spev - Song Management System

A comprehensive Flask-based web application for managing choir songs with modern mobile-responsive interface, LaTeX/PDF generation, and advanced file management capabilities.

## ✨ Features

### Core Functionality
- **Song Database Management**: Complete CRUD operations for songs with metadata
- **LaTeX/PDF Generation**: Professional songbook generation with custom templates
- **File Management**: Upload and manage MP3 files, sheet music PDFs, lyrics, and chords  
- **Advanced Search**: Real-time search with category filtering and responsive modal
- **Status Tracking**: Track song approval status, printing history, and completion

### Modern Mobile Interface
- **Responsive Design**: Mobile-first approach with Bootstrap 5
- **Mobile Cards**: Touch-optimized cards with overlapping status badges
- **Desktop Tables**: Comprehensive table view for desktop users
- **Icon-Only Mobile Buttons**: Optimized touch targets for mobile devices
- **Consistent Status Badges**: Bootstrap icons throughout the interface
- **Expandable Sections**: Collapsible "Viac možností" for additional actions

### Technical Features  
- **Docker Support**: Easy containerized deployment
- **Database Migrations**: Automatic schema updates and data migration
- **Security**: Secure file upload handling with validation
- **Performance**: Optimized queries and responsive loading

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

## 📱 Mobile-Optimized Interface

The application features a modern, mobile-first design:

- **Responsive Cards**: Song information displayed in cards with left-aligned status badges
- **Touch-Friendly**: 44px minimum touch targets, optimized spacing
- **Search Modal**: Always-accessible search functionality with responsive positioning  
- **Icon-Only Buttons**: Compact mobile buttons showing only icons for file actions
- **Consistent Styling**: Bootstrap icons and unified color scheme across all views

## 🚀 Usage

The application will be available at `http://localhost:5000`

### Key Pages
- `/` - Main song listing with search and filtering
- `/song/<id>` - Individual song details and editing
- `/songs/<ids>` - View selected songs (shareable links)

### Mobile Features
- Tap status badges to see tooltips
- Use search bar (fixed at bottom on mobile, above table on desktop)
- Expand "Viac možností" sections for additional actions
- Icon-only buttons for file management (download, delete, etc.)

## 📁 Project Structure

```
├── app.py                 # Main Flask application
├── models.py             # SQLAlchemy database models  
├── generate_tex.py       # LaTeX generation utilities
├── stamper.py           # PDF processing utilities
├── templates/           # Jinja2 HTML templates
│   ├── layout.html      # Base template with navigation
│   ├── index.html       # Main song listing (mobile cards + desktop table)
│   ├── songs_view.html  # Selected songs view
│   ├── song_detail.html # Song editing interface
│   └── song_view.html   # Individual song display
├── static/              # Static assets
│   ├── css/            # Custom stylesheets
│   ├── js/             # JavaScript functionality
│   ├── uploads/        # User-uploaded files
│   └── generated/      # Generated PDFs and TeX files
├── instance/           # SQLite database and config
├── docker-compose.yml  # Docker deployment configuration
└── requirements.txt    # Python dependencies
```

## 🤝 Contributing

Recent improvements include:
- ✅ Mobile-responsive search bar positioning (>992px breakpoint)
- ✅ Consistent Bootstrap icon usage across status badges  
- ✅ Left-aligned overlapping badges layout for mobile cards
- ✅ Icon-only mobile buttons for improved UX
- ✅ Enhanced touch targets and accessibility

Contributions welcome! Please maintain the mobile-first approach and Bootstrap design consistency.
