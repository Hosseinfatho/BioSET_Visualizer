from bioset.report.content_sections.PDFSection import PDFSection


class DatasetContent:
    def __init__(self, name, channels):
        self.name = name
        self.channels = channels


class Dataset(PDFSection):
    def __init__(self, dataset_information: DatasetContent):
        super().__init__("Dataset Information")
        self.dataset_information = dataset_information

    def get_content(self):
        pass

    def append_pdf_bytes(self, pdf):
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(0, 10, self.get_title(), 0, 1)

        pdf.set_font('Arial', 'B', 11)
        name_str = "Dataset Name" + self.dataset_information.name
        pdf.cell(0, 10, name_str, 0, 1)
        channels_str = "Channels" + str(self.dataset_information.channels)
        pdf.cell(0, 10, channels_str, 0, 1)
        return pdf
