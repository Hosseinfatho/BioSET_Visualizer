import datetime

from fpdf import FPDF

from bioset.report.content_sections.PDFSection import PDFSection


class BioSETReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'BioSET Analysis Report', 0, 1, 'L')
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')
        self.cell(0, 10, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), 0, 0, 'R')


def generate_report_bytes(pdf_sections: list[PDFSection]):
    """
    Generates PDF bytes based on provided analysis content.
    content_data: dict containing keys like 'title', 'channels', 'params'
    """
    pdf = BioSETReport()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font('Arial', '', 12)

    for section in pdf_sections:
        pdf = section.append_pdf_bytes(pdf)

    return pdf.output(dest='S')
