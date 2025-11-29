import sqlite3
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

# Register fonts
pdfmetrics.registerFont(TTFont('Poppins', 'Poppins-Regular.ttf'))
pdfmetrics.registerFont(TTFont('Poppins-Bold', 'Poppins-Bold.ttf'))

# Load icon (use a PNG or SVG converted to PNG)
ICON_PATH = "sheet_icon.png"  # replace with your chosen icon
sheet_icon = ImageReader(ICON_PATH)

# Label sheet specs
LABEL_WIDTH = 37.8  # mm
LABEL_HEIGHT = 21.25  # mm
COLUMNS = 5
ROWS = 13
PAGE_WIDTH, PAGE_HEIGHT = A4

mm = 2.83465
label_w = LABEL_WIDTH * mm
label_h = LABEL_HEIGHT * mm

# Margins
LEFT_MARGIN = 11*mm + 1# (PAGE_WIDTH - (COLUMNS * label_w)) / 2
TOP_MARGIN = 8.5 *mm #(PAGE_HEIGHT - (ROWS * label_h)) / 2

def fetch_songs(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT song_id, version_name FROM song")
    rows = cursor.fetchall()
    conn.close()
    return rows

def fit_font_size(text, font_name, max_width, max_size):
    """Finds the largest font size that fits within max_width."""
    size = max_size
    while size > 4:
        width = pdfmetrics.stringWidth(text, font_name, size)
        if width <= max_width - 4:  # padding
            return size
        size -= 0.5
    return size

def draw_label(c, x, y, song_id, version_name, sheet=False):
    """
    Draws a styled label. If sheet=True, adds a light blue icon in bottom-right.
    Version name will wrap into multiple lines if too long.
    """
    # Add sheet music icon bottom-right

    # Colors
    border_color = colors.lightblue if sheet else colors.grey
    text_color = colors.darkblue if sheet else colors.black

    # Background box with margin for spacing
    margin = .1  # Small margin to create space between labels
    c.setStrokeColor(border_color)
    c.setLineWidth(3)
    # c.setDash(3, 3)
    c.setFillColor(colors.whitesmoke)
    c.roundRect(x + margin, y - label_h + margin, label_w - 6 - margin, label_h - 6 - margin, 6, stroke=1, fill=1)
    c.setDash()
    if sheet:
        margin = 2  # Same margin as background box
        icon_size = label_h * 0.5
        icon_x = x + label_w - icon_size - 5 - margin
        icon_y = y - label_h + 2 + margin
        # Adjust opacity if version name exists
        if version_name.strip():
            c.saveState()
            c.setFillAlpha(0.45)  # make it lighter
            c.setStrokeAlpha(0.45)
            # Shift icon up a little to avoid overlapping version text
            c.drawImage(sheet_icon, icon_x, icon_y, width=icon_size, height=icon_size, mask='auto')
            c.restoreState()
        else:
            # normal icon for empty version
            c.drawImage(sheet_icon, icon_x, icon_y, width=icon_size, height=icon_size, mask='auto')
            
    # Fit Song ID font size
    song_id_str = str(song_id)
    id_size = fit_font_size(song_id_str, "Poppins-Bold", label_w, 24)

    # Split version name into lines that fit the label
    max_ver_size = 12
    ver_size = max_ver_size
    words = version_name.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = (current_line + " " + word).strip()
        if pdfmetrics.stringWidth(test_line, "Poppins", ver_size) <= label_w - 4:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    # Adjust font size if too many lines
    if len(lines) > 2:
        ver_size = max_ver_size * 2 / len(lines)  # reduce size to fit 2-3 lines

    # Centered text
    center_x = x + label_w / 2
    center_y = y - label_h / 2

    # Draw Song ID
    c.setFont("Poppins-Bold", id_size)
    c.setFillColor(text_color)
    c.drawCentredString(center_x, center_y, song_id_str)

    # Draw Version Name lines
    c.setFont("Poppins", ver_size)
    c.setFillColor(colors.grey)
    line_spacing = ver_size + 1
    start_y = center_y - ver_size *1.4 + (len(lines)-1)*line_spacing/2
    for i, line in enumerate(lines):
        line_y = start_y - i*line_spacing
        c.drawCentredString(center_x, line_y, line)

    


def create_labels(data, output_pdf="labels.pdf"):
    c = canvas.Canvas(output_pdf, pagesize=A4)
    x_positions = [LEFT_MARGIN + col * label_w  for col in range(COLUMNS)]
    y_positions = [PAGE_HEIGHT - TOP_MARGIN - row * label_h for row in range(ROWS)]

    label_index = 0
    for song_id, version_name in data:
        # 4 labels per song: 2 text, 2 sheet-music
        labels = [
            (song_id, version_name, False),
            (song_id, version_name, False),
            (song_id, version_name, True),
            (song_id, version_name, True)
        ]
        for sid, ver, sheet in labels:
            col = label_index % COLUMNS
            row = (label_index // COLUMNS) % ROWS
            x = x_positions[col]
            y = y_positions[row]
            draw_label(c, x, y, sid, ver, sheet)
            label_index += 1
            if label_index % (COLUMNS * ROWS) == 0:
                c.showPage()
    c.save()
    print(f"Labels saved to {output_pdf}")

if __name__ == "__main__":
    songs = fetch_songs("song.db")
    create_labels(songs)
