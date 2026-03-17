import datetime

from fpdf import FPDF

from bioset.report.content_sections.PDFSection import PDFSection, TITLE_FONT_SIZE, TITLE_COLOR


def _replace_llm_symbols(txt):
    if not isinstance(txt, str):
        return txt

    replacements = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
    }
    for char, replacement in replacements.items():
        txt = txt.replace(char, replacement)

    return txt


class BioSETReport(FPDF):
    def cell(self, text='', **kwargs):
        text = _replace_llm_symbols(text)
        return super().cell(text=text, **kwargs)

    def multi_cell(self, text='', **kwargs):
        text = _replace_llm_symbols(text)
        return super().multi_cell(text=text, **kwargs)

    def write(self, text='', **kwargs):
        text = _replace_llm_symbols(text)
        return super().write(text=text, **kwargs)

    def header(self):
        icon_w = 20
        self.image("src/bioset/ui/assets/icon.jpg", x=self.l_margin, y=self.t_margin, w=icon_w)
        self.set_font('Arial', 'B', TITLE_FONT_SIZE)
        self.set_text_color(TITLE_COLOR)
        self.set_xy(self.l_margin + icon_w + 5, self.t_margin + (icon_w / 4))
        self.cell(0, 10, 'BioSET Analysis Report', 0, 1, 'L')
        self.set_y(self.t_margin + icon_w + 5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(TITLE_COLOR)
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

    for section in pdf_sections:
        pdf = section.append_pdf_bytes(pdf)
        pdf.ln(2)

    return pdf.output(dest='S')
