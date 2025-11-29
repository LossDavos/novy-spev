"""
PDF Label Generator for Song Stickers
Generates labels in 5x13 grid on A4 paper (37.8x21.25mm labels)
"""

import os
import io
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader


class LabelGenerator:
    """Generates PDF labels for songs"""
    
    # Label sheet specs
    LABEL_WIDTH = 37.8  # mm
    LABEL_HEIGHT = 21.25  # mm
    COLUMNS = 5
    ROWS = 13
    MM = 2.83465  # conversion factor
    
    def __init__(self, base_dir):
        """Initialize the label generator with paths to fonts and icons"""
        self.base_dir = base_dir
        self.label_w = self.LABEL_WIDTH * self.MM
        self.label_h = self.LABEL_HEIGHT * self.MM
        self.left_margin = 11 * self.MM + 1
        self.top_margin = 8.5 * self.MM
        
        # Register fonts
        font_dir = os.path.join(base_dir, 'static', 'fonts')
        pdfmetrics.registerFont(TTFont('Poppins', os.path.join(font_dir, 'Poppins-Regular.ttf')))
        pdfmetrics.registerFont(TTFont('Poppins-Bold', os.path.join(font_dir, 'Poppins-Bold.ttf')))
        
        # Load icon
        icon_path = os.path.join(base_dir, 'static', 'icons', 'sheet_icon.png')
        self.sheet_icon = ImageReader(icon_path)
    
    def fit_font_size(self, text, font_name, max_width, max_size):
        """Find the largest font size that fits within max_width"""
        size = max_size
        while size > 4:
            try:
                width = pdfmetrics.stringWidth(text, font_name, size)
            except:
                width = len(text) * size * 0.6  # Fallback estimation
            if width <= max_width - 4:
                return size
            size -= 0.5
        return size
    
    def draw_label(self, c, x, y, song_id, version_name, sheet=False):
        """Draw a single label with optional sheet music icon"""
        border_color = colors.lightblue if sheet else colors.grey
        text_color = colors.darkblue if sheet else colors.black
        
        # Ensure version_name is a string
        version_name = version_name or ""
        
        # Draw background box
        margin = 0.1
        c.setStrokeColor(border_color)
        c.setLineWidth(3)
        c.setFillColor(colors.whitesmoke)
        c.roundRect(x + margin, y - self.label_h + margin, 
                   self.label_w - 6 - margin, self.label_h - 6 - margin, 
                   6, stroke=1, fill=1)
        
        # Draw sheet music icon if requested
        if sheet:
            margin = 2
            icon_size = self.label_h * 0.5
            icon_x = x + self.label_w - icon_size - 5 - margin
            icon_y = y - self.label_h + 2 + margin
            if version_name.strip():
                c.saveState()
                c.setFillAlpha(0.45)
                c.setStrokeAlpha(0.45)
                c.drawImage(self.sheet_icon, icon_x, icon_y, 
                          width=icon_size, height=icon_size, mask='auto')
                c.restoreState()
            else:
                c.drawImage(self.sheet_icon, icon_x, icon_y, 
                          width=icon_size, height=icon_size, mask='auto')
        
        # Draw song ID
        song_id_str = str(song_id)
        id_size = self.fit_font_size(song_id_str, "Poppins-Bold", self.label_w, 24)
        c.setFont("Poppins-Bold", id_size)
        
        center_x = x + self.label_w / 2
        center_y = y - self.label_h / 2
        
        c.setFillColor(text_color)
        c.drawCentredString(center_x, center_y, song_id_str)
        
        # Draw version name
        if version_name and version_name.strip():
            max_ver_size = 12
            ver_size = max_ver_size
            words = version_name.split()
            lines = []
            current_line = ""
            
            # Word wrap
            for word in words:
                test_line = (current_line + " " + word).strip()
                text_width = pdfmetrics.stringWidth(test_line, "Poppins", ver_size)
                if text_width <= self.label_w - 4:
                    current_line = test_line
                else:
                    if current_line:
                        lines.append(current_line)
                    current_line = word
            if current_line:
                lines.append(current_line)
            
            # Adjust font size if too many lines
            if len(lines) > 2:
                ver_size = max_ver_size * 2 / len(lines)
            
            # Draw version text
            c.setFont("Poppins", ver_size)
            c.setFillColor(colors.grey)
            line_spacing = ver_size + 1
            start_y = center_y - ver_size * 1.4 + (len(lines) - 1) * line_spacing / 2
            for i, line in enumerate(lines):
                line_y = start_y - i * line_spacing
                c.drawCentredString(center_x, line_y, line)
    
    def generate_labels(self, songs, positions=None):
        """
        Generate PDF labels for a list of songs
        
        Args:
            songs: List of song objects with song_id and version_name attributes
            positions: Optional list of grid positions (0-64 for 5x13 grid) to use for labels.
                      If None, labels are placed sequentially from top-left.
            
        Returns:
            BytesIO buffer containing the PDF
        """
        # Create PDF in memory
        pdf_buffer = io.BytesIO()
        c = pdf_canvas.Canvas(pdf_buffer, pagesize=A4)
        PAGE_WIDTH, PAGE_HEIGHT = A4
        
        # Calculate positions
        x_positions = [self.left_margin + col * self.label_w for col in range(self.COLUMNS)]
        y_positions = [PAGE_HEIGHT - self.top_margin - row * self.label_h for row in range(self.ROWS)]
        
        # Prepare labels data (2 per song: 1 text, 1 with sheet icon)
        labels_data = []
        for song in songs:
            version = song.version_name or ""
            labels_data.append((song.song_id, version, False))  # Text only
            labels_data.append((song.song_id, version, True))   # With sheet icon
        
        # If positions are specified, use them; otherwise sequential
        if positions:
            # Group positions by page
            positions_by_page = {}
            for i, pos in enumerate(positions):
                if i >= len(labels_data):
                    break  # No more labels to place
                
                page_num = pos // (self.COLUMNS * self.ROWS)
                if page_num not in positions_by_page:
                    positions_by_page[page_num] = []
                positions_by_page[page_num].append((pos, labels_data[i]))
            
            # Draw labels page by page
            for page_num in sorted(positions_by_page.keys()):
                if page_num > 0:
                    c.showPage()
                
                for pos, (sid, ver, sheet) in positions_by_page[page_num]:
                    local_pos = pos % (self.COLUMNS * self.ROWS)
                    col = local_pos % self.COLUMNS
                    row = local_pos // self.COLUMNS
                    x = x_positions[col]
                    y = y_positions[row]
                    self.draw_label(c, x, y, sid, ver, sheet)
        else:
            # Sequential placement
            label_index = 0
            for sid, ver, sheet in labels_data:
                col = label_index % self.COLUMNS
                row = (label_index // self.COLUMNS) % self.ROWS
                x = x_positions[col]
                y = y_positions[row]
                self.draw_label(c, x, y, sid, ver, sheet)
                label_index += 1
                if label_index % (self.COLUMNS * self.ROWS) == 0:
                    c.showPage()
        
        c.save()
        pdf_buffer.seek(0)
        return pdf_buffer
