from pathlib import Path

_STYLES_DIR = Path(__file__).parent

def _read_css(filename: str) -> str:
    return (_STYLES_DIR / filename).read_text()


def register_styles(client):
    # register CSS files here
    client.Style(_read_css("base.css"))