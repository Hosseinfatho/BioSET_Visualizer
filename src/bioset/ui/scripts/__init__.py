from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
_NOV_DIR = Path(__file__).resolve().parent.parent.parent / "NOV"


def _read_js(filename: str) -> str:
    return (_SCRIPTS_DIR / filename).read_text()

# register js files here
def register_scripts(client):
    client.Script(_read_js("upset.js"))
    client.Script(_read_js("bar.js"))
    client.Script((_NOV_DIR / "nov_popup_drag.js").read_text(encoding="utf-8"))