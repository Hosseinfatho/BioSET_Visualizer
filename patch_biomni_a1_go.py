"""Patch Arcade bioset_biomni/server.py so A1.go() drops image=."""
from pathlib import Path

p = Path("/data/hossein/Biomni/bioset_biomni/server.py")
text = p.read_text(encoding="utf-8")
bak = Path("/data/hossein/Biomni/bioset_biomni/server.py.go_bak")
if not bak.exists():
    bak.write_text(text, encoding="utf-8")
    print("backup written")

print("A1 / go lines:")
for i, line in enumerate(text.splitlines(), 1):
    if "A1" in line or ".go(" in line:
        print(f"{i}: {line}")

MARKER = "_bioset_image_compat"
if MARKER in text:
    print("already patched")
    raise SystemExit(0)

shim = """

# --- BioSET compat: this Biomni A1.go() has no image= ---
import inspect as _inspect
if not getattr(A1.go, "_bioset_image_compat", False):
    _orig_go = A1.go

    def _go_compat(self, *args, **kwargs):
        kwargs.pop("image", None)
        kwargs.pop("images", None)
        params = _inspect.signature(_orig_go).parameters
        if any(x.kind == _inspect.Parameter.VAR_KEYWORD for x in params.values()):
            return _orig_go(self, *args, **kwargs)
        allowed = {k for k in params if k != "self"}
        return _orig_go(self, *args, **{k: v for k, v in kwargs.items() if k in allowed})

    _go_compat._bioset_image_compat = True
    A1.go = _go_compat
"""

p.write_text(text.rstrip() + shim, encoding="utf-8")
print("patched: A1.go now drops image=")
