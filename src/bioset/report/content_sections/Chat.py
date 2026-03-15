from bioset.report.content_sections.PDFSection import PDFSection


class ChatContent:
    def __init__(self, content: str, sent_by_user: bool):
        self.content = content
        self.sent_by_user = sent_by_user


class Chat(PDFSection):
    def __init__(self, chat_content: list[ChatContent]):
        super().__init__("LLM Section")
        self.chat_content = chat_content

    def get_content(self):
        pass

    def append_pdf_bytes(self, pdf):
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(0, 10, self.get_title(), 0, 1)

        pdf.set_font('Arial', 'B', 11)
        for chat_bubble in self.chat_content:
            pdf.cell(0, 10, chat_bubble.content, 0, 1)
        return pdf
