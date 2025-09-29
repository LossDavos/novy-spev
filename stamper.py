import os
import tempfile
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register the Poppins font family
def register_poppins_fonts(font_path="/home/Davos/novy_spev/static/fonts/"):
    """
    Register all variants of the Poppins font with ReportLab
    """
    print(f"[FONT DEBUG] register_poppins_fonts called with font_path: {font_path}")
    try:
        print(f"[FONT DEBUG] Absolute font path: {font_path}")


        # Register regular font
        poppins_regular = os.path.join(font_path, "Poppins-Regular.ttf")
        print(f"[FONT DEBUG] Looking for Poppins-Regular.ttf at: {poppins_regular}")
        if os.path.exists(poppins_regular):
            pdfmetrics.registerFont(TTFont("Poppins", poppins_regular))
            print(f"[FONT DEBUG] Registered Poppins-Regular.ttf")
        else:
            print(f"[FONT DEBUG] Poppins-Regular.ttf not found")

        # Register bold font
        poppins_bold = os.path.join(font_path, "Poppins-Bold.ttf")
        print(f"[FONT DEBUG] Looking for Poppins-Bold.ttf at: {poppins_bold}")
        if os.path.exists(poppins_bold):
            pdfmetrics.registerFont(TTFont("Poppins-Bold", poppins_bold))
            print(f"[FONT DEBUG] Registered Poppins-Bold.ttf")
        else:
            print(f"[FONT DEBUG] Poppins-Bold.ttf not found")

        # Register italic font (if available)
        poppins_italic = os.path.join(font_path, "Poppins-Italic.ttf")
        print(f"[FONT DEBUG] Looking for Poppins-Italic.ttf at: {poppins_italic}")
        if os.path.exists(poppins_italic):
            pdfmetrics.registerFont(TTFont("Poppins-Italic", poppins_italic))
            print(f"[FONT DEBUG] Registered Poppins-Italic.ttf")
        else:
            print(f"[FONT DEBUG] Poppins-Italic.ttf not found")

        # Check if at least regular and bold are available
        fonts_available = os.path.exists(poppins_regular) and os.path.exists(poppins_bold)
        print(f"[FONT DEBUG] Fonts available: {fonts_available}")
        return fonts_available
    except Exception as e:
        print(f"[FONT DEBUG] Error registering fonts: {e}")
        import traceback
        traceback.print_exc()
        # Fall back to standard fonts
        return False

def stamp_pdf(input_pdf_path, output_pdf_path, song_id, version_name=None, font_path=""):
    """
    Stamp a PDF with song ID and optional version name
    
    Args:
        input_pdf_path: Path to the input PDF file
        output_pdf_path: Path where stamped PDF will be saved
        song_id: Song ID to display in the stamp
        version_name: Optional version name to display
        font_path: Path to directory containing Poppins fonts
    
    Returns:
        bool: True if successful, False otherwise
    """
    print(f"[STAMPER DEBUG] stamp_pdf called with:")
    print(f"  input_pdf_path: {input_pdf_path}")
    print(f"  output_pdf_path: {output_pdf_path}")
    print(f"  song_id: {song_id}")
    print(f"  version_name: {version_name}")
    print(f"  font_path: {font_path}")
    
    try:
        # Register custom fonts
        fonts_available = register_poppins_fonts(font_path)
        print(f"[STAMPER DEBUG] Fonts available: {fonts_available}")

        # Read the input PDF first to get page dimensions
        print(f"[STAMPER DEBUG] Reading input PDF to get page dimensions...")
        reader = PdfReader(input_pdf_path)
        print(f"[STAMPER DEBUG] Input PDF has {len(reader.pages)} pages")

        writer = PdfWriter()

        for i, page in enumerate(reader.pages):
            print(f"[STAMPER DEBUG] Processing page {i+1}")

            # Get page dimensions
            media_box = page.mediabox
            page_width = float(media_box.width)
            page_height = float(media_box.height)
            print(f"[STAMPER DEBUG] Page {i+1} dimensions: {page_width} x {page_height}")

            # Determine if page is landscape (width > height)
            is_landscape = page_width > page_height
            print(f"[STAMPER DEBUG] Page {i+1} is landscape: {is_landscape}")

            # Create a temporary file for the stamp for this page
            temp_stamp = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')

            # Create the stamp with ReportLab using the actual page dimensions
            if is_landscape:
                c = canvas.Canvas(temp_stamp.name, pagesize=(page_width, page_height))
            else:
                c = canvas.Canvas(temp_stamp.name, pagesize=(page_width, page_height))

            # Set up coordinates for top-right corner (15pt from right, 20pt from top)
            # Convert points to PDF units (1 point = 1/72 inch)
            x_pos = page_width - 15  # 15pt from right edge
            y_pos = page_height - 20  # 20pt from top edge
            print(f"[STAMPER DEBUG] Stamp position: ({x_pos}, {y_pos})")

            # Draw the box (mimicking your TikZ style)
            c.setStrokeColor(colors.lightblue)  # gray!60
            c.setFillColor(colors.Color(0.68, 0.85, 0.9, alpha=0.5))
            c.setLineWidth(2)  # thick
            c.setDash(6, 3)    # dashed

            # Draw rounded rectangle (approximation)
            box_width = 100  # Approx 35mm in points
            box_height = 50  # Approx 18mm in points
            print(f"[STAMPER DEBUG] Drawing box: {box_width} x {box_height}")

            # Set fill and draw the rounded rectangle
            c.roundRect(x_pos - box_width, y_pos - box_height,
                        box_width, box_height, 8, stroke=1, fill=1)

            # Add song ID text with custom font if available
            if fonts_available:
                c.setFont("Poppins-Bold", 28)
                print(f"[STAMPER DEBUG] Using Poppins-Bold font")
            else:
                c.setFont("Helvetica-Bold", 28)  # Fallback font
                print(f"[STAMPER DEBUG] Using Helvetica-Bold fallback font")

            c.setFillColor(colors.black)  # black
            c.drawCentredString(x_pos - box_width/2, y_pos - box_height/2 - 5, str(song_id))
            print(f"[STAMPER DEBUG] Drew song ID: {song_id}")

            # Add version name if provided
            if version_name:
                if fonts_available:
                    c.setFont("Poppins", 12)
                else:
                    c.setFont("Helvetica", 12)

                c.setFillColorRGB(0.7, 0.7, 0.7)  # gray!70
                c.drawCentredString(x_pos - box_width/2, y_pos - box_height/2 - 20, version_name)
                print(f"[STAMPER DEBUG] Drew version name: {version_name}")

            c.save()

            # Stamp this page
            stamp_reader = PdfReader(temp_stamp.name)
            stamp_page = stamp_reader.pages[0]
            page.merge_page(stamp_page)
            writer.add_page(page)

            # Clean up temp file for this page
            os.unlink(temp_stamp.name)

        # Write output
        print(f"[STAMPER DEBUG] Writing output to: {output_pdf_path}")
        with open(output_pdf_path, "wb") as output_file:
            writer.write(output_file)

        # Verify output file was created
        if os.path.exists(output_pdf_path):
            file_size = os.path.getsize(output_pdf_path)
            print(f"[STAMPER DEBUG] Output file created successfully, size: {file_size} bytes")
            return True
        else:
            print(f"[STAMPER DEBUG] ERROR: Output file was not created")
            return False

    except Exception as e:
        print(f"[STAMPER DEBUG] Exception in stamp_pdf: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# Example usage (only run if script is executed directly)
if __name__ == "__main__":
    stamp_pdf("Crucem Tuam-Tenor.pdf", "stamped_output.pdf", song_id=123, version_name="Version 1")