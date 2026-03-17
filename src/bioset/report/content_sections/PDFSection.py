from abc import abstractmethod, ABC

TITLE_FONT_SIZE = 16
SUBTITLE_FONT_SIZE = 14
CATEGORY_FONT_SIZE = 12
FONT_SIZE = 10

TITLE_COLOR = '#000000'
SUBTITLE_COLOR = '#444444'
COLOR = '#666666'

BACKGROUND_COLOR = '#F5F5F5'

class PDFSection(ABC):
    def __init__(self, name: str):
        self.name = name

    def get_title(self) -> str:
        return self.name

    @abstractmethod
    def append_pdf_bytes(self, pdf):
        pass
