from abc import abstractmethod, ABC


class PDFSection(ABC):
    def __init__(self, name: str):
        self.name = name

    def get_title(self) -> str:
        return self.name

    @abstractmethod
    def get_content(self):
        pass

    @abstractmethod
    def append_pdf_bytes(self, pdf):
        pass
