from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent


def _read_js(filename: str) -> str:
    return (_SCRIPTS_DIR / filename).read_text()

# regsiter js files here
def register_scripts(client):
    client.Script(_read_js("upset.js"))
    client.Script(_read_js("bar.js"))
    client.Script(_read_js("mousemove.js"))
    client.Script(_read_js("histogram.js"))