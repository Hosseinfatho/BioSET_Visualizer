import datetime

from fpdf import FPDF


class BioSETReport(FPDF):
    def header(self):
        # Arial bold 15
        self.set_font('Arial', 'B', 15)
        # Title
        self.cell(0, 10, 'BioSET Analysis Report', 0, 1, 'C')
        # Line break
        self.ln(10)

    def footer(self):
        # Position at 1.5 cm from bottom
        self.set_y(-15)
        # Arial italic 8
        self.set_font('Arial', 'I', 8)
        # Page number
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')
        # Date on the right
        self.cell(0, 10, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), 0, 0, 'R')


def generate_report_bytes(content_data):
    """
    Generates PDF bytes based on provided analysis content.
    content_data: dict containing keys like 'title', 'channels', 'params'
    """
    pdf = BioSETReport()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font('Arial', '', 12)

    title = content_data.get('title', 'Analysis Summary')
    pdf.set_font('Arial', 'B', 14)
    pdf.cell(0, 10, title, 0, 1)
    pdf.ln(5)

    # Parameters section
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Parameters:', 0, 1)
    pdf.set_font('Arial', '', 11)

    params = content_data.get('params', {})
    for key, value in params.items():
        pdf.cell(0, 8, f"{key}: {value}", 0, 1)

    pdf.ln(5)

    # Channels section
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Active Channels:', 0, 1)
    pdf.set_font('Arial', '', 11)

    channels = content_data.get('channels', [])
    if not channels:
        pdf.cell(0, 8, "No active channels selected.", 0, 1)
    else:
        for ch in channels:
            pdf.cell(0, 8, f"- {ch}", 0, 1)

    # Images section (optional)
    # Note: images should be local paths or we need to handle base64 decoding to temp files
    # images = content_data.get('images', {})
    # for name, img_path in images.items():
    #    pdf.add_page()
    #    pdf.image(img_path, x=10, y=30, w=190)

    return pdf.output(dest='S')
