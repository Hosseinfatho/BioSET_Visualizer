from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent


def _read_js(filename: str) -> str:
    return (_SCRIPTS_DIR / filename).read_text()


# Lens drag: update position on every mousemove so the lens follows the cursor 1:1 (smooth).
NOV_DRAG_SCRIPT = r"""
(function() {
  "use strict";
  function byId(id) { return document.getElementById(id); }
  function setInput(id, value) {
    var inp = byId(id);
    if (inp) { inp.value = value; inp.dispatchEvent(new Event("input", { bubbles: true })); }
  }
  window.novStartDrag = function(e) {
    if (!e || e.button !== 0) return;
    if (e.target && e.target.closest && e.target.closest(".nov-rect-controls")) return;
    var lens = e.currentTarget || e.target;
    if (!lens || !lens.parentElement) return;
    var overlay = lens.parentElement;
    e.preventDefault();
    e.stopPropagation();
    overlay.style.pointerEvents = "auto";
    var r = overlay.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    var startX = (e.clientX - r.left) / r.width;
    var startY = 1 - (e.clientY - r.top) / r.height;
    var leftPct = parseFloat(String(lens.style.left || "35").replace("%", "")) / 100;
    var bottomPct = parseFloat(String(lens.style.bottom || "35").replace("%", "")) / 100;
    var sidePct = parseFloat(String(lens.style.width || "30").replace("%", "")) / 100;
    if (isNaN(leftPct)) leftPct = 0.35; if (isNaN(bottomPct)) bottomPct = 0.35; if (isNaN(sidePct)) sidePct = 0.3;
    function onMove(ev) {
      ev.preventDefault();
      var nx = (ev.clientX - r.left) / r.width;
      var ny = 1 - (ev.clientY - r.top) / r.height;
      leftPct = Math.max(0, Math.min(1 - sidePct, leftPct + (nx - startX)));
      bottomPct = Math.max(0, Math.min(1 - sidePct, bottomPct + (ny - startY)));
      startX = nx;
      startY = ny;
      lens.style.left = (leftPct * 100) + "%";
      lens.style.bottom = (bottomPct * 100) + "%";
    }
    function onUp(ev) {
      if (ev.button !== 0) return;
      overlay.style.pointerEvents = "none";
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("mouseup", onUp, true);
      var leftPct = parseFloat(String(lens.style.left || "35").replace("%", "")) / 100;
      var bottomPct = parseFloat(String(lens.style.bottom || "35").replace("%", "")) / 100;
      var sidePct = parseFloat(String(lens.style.width || "30").replace("%", "")) / 100;
      if (isNaN(leftPct)) leftPct = 0.35; if (isNaN(bottomPct)) bottomPct = 0.35; if (isNaN(sidePct)) sidePct = 0.3;
      setInput("nov-rect-drag-end", leftPct + "," + bottomPct + "," + sidePct + "," + sidePct);
    }
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", onUp, true);
  };

  /** NOV panel drag: mousedown on header band moves panel by updating its style. */
  window.novDragStart = function(e) {
    if (!e || e.button !== 0) return;
    if (e.target && (e.target.closest("button") || e.target.closest(".v-btn") || e.target.closest("a"))) return;
    var panel = e.currentTarget;
    if (!panel || !panel.getBoundingClientRect) return;
    if (panel.closest && !panel.querySelector("[data-nov-pos-input='1']") && !panel.querySelector("#nov-popup-pos-input")) {
      var p2 = panel.closest(".nov-popup-panel");
      if (p2) panel = p2;
    }
    var doc = panel.ownerDocument || document;
    var win = doc.defaultView || window;
    var r = panel.getBoundingClientRect();
    var startX = e.clientX, startY = e.clientY;
    var startLeft = r.left, startBottom = win.innerHeight - r.bottom;
    function setPanelPos(leftPx, bottomPx) {
      panel.style.left = Math.round(leftPx) + "px";
      panel.style.bottom = Math.round(bottomPx) + "px";
      panel.style.transform = "none";
    }
    setPanelPos(startLeft, startBottom);
    function onMove(ev) {
      ev.preventDefault();
      var left = startLeft + (ev.clientX - startX);
      var bottom = startBottom - (ev.clientY - startY);
      left = Math.max(0, Math.min(win.innerWidth - panel.offsetWidth, left));
      bottom = Math.max(0, Math.min(win.innerHeight - 40, bottom));
      setPanelPos(left, bottom);
    }
    function onUp(ev) {
      if (ev.button !== 0) return;
      doc.removeEventListener("mousemove", onMove, true);
      doc.removeEventListener("mouseup", onUp, true);
      var r2 = panel.getBoundingClientRect();
      setPanelPos(Math.round(r2.left), Math.round(win.innerHeight - r2.bottom));
    }
    doc.addEventListener("mousemove", onMove, true);
    doc.addEventListener("mouseup", onUp, true);
  };
})();
"""


def register_scripts(client):
    client.Script(NOV_DRAG_SCRIPT)  # Load first so window.novStartDrag exists when lens is clicked
    client.Script(_read_js("upset.js"))
    client.Script(_read_js("bar.js"))
    client.Script(_read_js("mousemove.js"))