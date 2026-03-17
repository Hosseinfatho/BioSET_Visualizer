from bioset.report.content_sections.PDFSection import PDFSection, SUBTITLE_FONT_SIZE, FONT_SIZE, CATEGORY_FONT_SIZE, \
    TITLE_COLOR, COLOR

CHAT_BUBBLE_PRIMARY = '#E6F2FF'
CHAT_BUBBLE_SECONDARY = '#F5F5F5'

class ChatContent:
    def __init__(self, content: str, sent_by_user: bool):
        self.content = content
        self.sent_by_user = sent_by_user


class LLMSettings:
    def __init__(self, model: str, mode: str):
        self.model = model
        self.mode = mode

class Chat(PDFSection):
    def __init__(self, chat_content: list[ChatContent], llm_settings: LLMSettings):
        super().__init__("Biomni / LLM")
        self.chat_content = chat_content
        self.llm_settings = llm_settings

    def get_content(self):
        pass

    def append_pdf_bytes(self, pdf):
        pdf.set_font('Arial', 'B', SUBTITLE_FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.cell(0, 10, self.get_title(), ln=1, align='L')

        pdf.set_font('Arial', 'B', CATEGORY_FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.cell(0, 10, "Settings", ln=1, align='L')

        pdf.set_font('Arial', 'B', FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.write(5, 'Model: ')
        pdf.set_text_color(COLOR)
        pdf.write(5, self.llm_settings.model)
        pdf.ln(5)

        pdf.set_text_color(TITLE_COLOR)
        pdf.write(5, 'Mode: ')
        pdf.set_text_color(COLOR)
        pdf.write(5, self.llm_settings.mode)
        pdf.ln(8)

        pdf.set_font('Arial', 'B', CATEGORY_FONT_SIZE)
        pdf.set_text_color(TITLE_COLOR)
        pdf.cell(0, 10, "Chat Content", ln=1, align='L')

        pdf.set_font('Arial', 'B', FONT_SIZE)
        pdf.set_text_color(COLOR)

        if not self.chat_content:
            pdf.set_font('Arial', 'B', FONT_SIZE)
            pdf.set_text_color(COLOR)
            pdf.multi_cell(0, 6, "No messages sent.")
            return pdf

        effective_width = pdf.w - pdf.l_margin - pdf.r_margin
        bubble_width = effective_width * 0.9
        padding = 3
        text_width = bubble_width - (2 * padding)

        pdf.set_font('Arial', '', FONT_SIZE)
        
        for chat_bubble in self.chat_content:
            lines = pdf.multi_cell(text_width, 5, chat_bubble.content, split_only=True)
            text_height = len(lines) * 5
            bubble_height = text_height + (2 * padding)

            if pdf.get_y() + bubble_height + 5 > pdf.page_break_trigger:
                pdf.add_page()

            start_y = pdf.get_y()

            if chat_bubble.sent_by_user:
                start_x = pdf.l_margin + (effective_width - bubble_width)
                pdf.set_fill_color(CHAT_BUBBLE_PRIMARY)
            else:
                start_x = pdf.l_margin
                pdf.set_fill_color(CHAT_BUBBLE_SECONDARY)

            pdf.rect(start_x, start_y, bubble_width, bubble_height, style='F', round_corners=True, corner_radius=3)

            pdf.set_xy(start_x + padding, start_y + padding)
            pdf.set_text_color(COLOR)
            pdf.multi_cell(text_width, 5, chat_bubble.content, border=0, align='L')

            pdf.set_y(start_y + bubble_height + 3)
            
        return pdf
