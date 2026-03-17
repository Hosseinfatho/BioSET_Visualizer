import datetime

from bioset.report.content_sections.PDFSection import PDFSection, SUBTITLE_FONT_SIZE, FONT_SIZE, COLOR, TITLE_COLOR


class GeneralContent:
    def __init__(self, zarr_url: str, metadata_url: str, timestamp: datetime):
        self.zarr_url = zarr_url
        self.metadata_url = metadata_url
        self.timestamp = timestamp


class General(PDFSection):
    def __init__(self, general: GeneralContent):
        super().__init__("General")
        self.general = general

    def append_pdf_bytes(self, pdf):
        pdf.set_font('Arial', 'B', SUBTITLE_FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.cell(0, 10, self.get_title(), ln=1, align='L')

        pdf.set_font('Arial', 'B', FONT_SIZE)

        pdf.set_text_color(TITLE_COLOR)
        pdf.write(5, 'Zarr URL:')
        pdf.ln(5)
        pdf.set_text_color(COLOR)
        pdf.multi_cell(0, 5, self.general.zarr_url, align='L')
        pdf.ln(2)

        pdf.set_text_color(TITLE_COLOR)
        pdf.write(5, 'Metadata URL:')
        pdf.ln(5)
        pdf.set_text_color(COLOR)
        pdf.multi_cell(0, 5, self.general.metadata_url, align='L')
        pdf.ln(2)

        pdf.set_text_color(TITLE_COLOR)
        pdf.write(5, 'Export Timestamp: ')
        pdf.set_text_color(COLOR)
        pdf.write(5, self.general.timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        pdf.ln(5)

        return pdf
