from bioset.report.content_sections.PDFSection import PDFSection, SUBTITLE_FONT_SIZE, FONT_SIZE, TITLE_COLOR, COLOR, \
    BACKGROUND_COLOR


class AnalysisDatasetContent:
    def __init__(self, name, channels):
        self.name = name
        self.channels = channels


class AnalysisDataset(PDFSection):
    def __init__(self, dataset_information: AnalysisDatasetContent):
        super().__init__("Dataset Information")
        self.dataset_information = dataset_information

    def append_pdf_bytes(self, pdf):
        pdf.set_font('Arial', 'B', SUBTITLE_FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.cell(0, 10, self.get_title(), ln=1, align='L')

        pdf.set_font('Arial', 'B', FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.write(5, 'Dataset Name: ')
        pdf.set_text_color(COLOR)
        pdf.write(5, self.dataset_information.name)
        pdf.ln(7)

        pdf.set_text_color(TITLE_COLOR)
        pdf.cell(0, 7, "Channels", 0, 1)
        pdf.ln(2)

        column_count = 4
        effective_width = pdf.w - pdf.l_margin - pdf.r_margin

        padding = 3
        inner_width = effective_width - (2 * padding)
        col_width = inner_width / column_count

        rows = [self.dataset_information.channels[i:i + column_count] for i in
                range(0, len(self.dataset_information.channels), column_count)]
        row_heights = []
        for row in rows:
            max_h = 0
            for channel in row:
                lines = pdf.multi_cell(col_width, 5, channel, split_only=True)
                h = len(lines) * 5
                if h > max_h:
                    max_h = h
            row_heights.append(max_h)

        total_table_height = sum(row_heights) + max(0, (len(row_heights) - 1) * 2) + (2 * padding)

        start_y = pdf.get_y()
        pdf.set_fill_color(BACKGROUND_COLOR)
        pdf.rect(pdf.l_margin, start_y, effective_width, total_table_height, style='F', round_corners=True,
                 corner_radius=3)

        pdf.set_y(start_y + padding)

        for r_idx, row in enumerate(rows):
            y_row_start = pdf.get_y()
            for c_idx, channel in enumerate(row):
                pdf.set_xy(pdf.l_margin + padding + (c_idx * col_width), y_row_start)
                pdf.multi_cell(col_width, 5, channel, border=0)

            pdf.set_y(y_row_start + row_heights[r_idx] + 2)

        pdf.set_y(start_y + total_table_height + 5)

        return pdf
