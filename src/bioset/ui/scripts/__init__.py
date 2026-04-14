from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent


def _read_js(filename: str) -> str:
    return (_SCRIPTS_DIR / filename).read_text()

# Keep overlay panels aligned with the real drawer width (Vuetify may override configured width)
DRAWER_WIDTH_SCRIPT = r"""
(function() {
  "use strict";
  function byId(id) { return document.getElementById(id); }
  function setInput(id, value) {
    var inp = byId(id);
    if (inp) { inp.value = String(value); inp.dispatchEvent(new Event("input", { bubbles: true })); }
  }
  function getLeftDrawerEl() {
    // Prefer the non-right navigation drawer (left side)
    var all = document.querySelectorAll(".v-navigation-drawer");
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.classList && el.classList.contains("v-navigation-drawer--right")) continue;
      return el;
    }
    return null;
  }
  var last = -1;
  function update() {
    var el = getLeftDrawerEl();
    if (!el || !el.getBoundingClientRect) return;
    var r = el.getBoundingClientRect();
    var w = Math.round(r.width || 0);
    if (!w || w < 40) return;
    if (w !== last) {
      last = w;
      setInput("bioset-left-drawer-width", w);
    }
  }
  // Initial + keep in sync with mini toggle/animations
  window.addEventListener("resize", update, { passive: true });
  document.addEventListener("transitionend", update, true);
  setTimeout(update, 0);
  setTimeout(update, 250);
  setInterval(update, 750);
})();
"""

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

# Main viewer scale-bar drag zoom: horizontal drag synthesizes wheel events on VTK canvas.
MAIN_SCALE_BAR_ZOOM_SCRIPT = r"""
(function() {
  "use strict";
  window.mainScaleBarZoomStart = function(e) {
    if (!e || e.button !== 0) return;

    var doc = (e.currentTarget && e.currentTarget.ownerDocument) || document;
    var canvas = doc.querySelector("canvas");
    if (!canvas) return;

    e.preventDefault();
    e.stopPropagation();

    var lastX = e.clientX;
    var carry = 0;
    var pxPerStep = 4;

    var prevUserSelect = doc.body ? doc.body.style.userSelect : "";
    var prevCursor = doc.body ? doc.body.style.cursor : "";
    if (doc.body) {
      doc.body.style.userSelect = "none";
      doc.body.style.cursor = "ew-resize";
    }

    function emitWheel(stepSign, ev) {
      // Negative deltaY zooms in for VTK trackball style.
      var wheel = new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        clientX: ev.clientX,
        clientY: ev.clientY,
        deltaY: stepSign > 0 ? -120 : 120,
        deltaMode: 0,
      });
      canvas.dispatchEvent(wheel);
    }

    function onMove(ev) {
      ev.preventDefault();
      var dx = ev.clientX - lastX;
      lastX = ev.clientX;
      carry += dx;

      while (Math.abs(carry) >= pxPerStep) {
        var sign = carry > 0 ? 1 : -1;
        emitWheel(sign, ev);
        carry -= sign * pxPerStep;
      }
    }

    function onUp(ev) {
      if (ev.button !== 0) return;
      doc.removeEventListener("mousemove", onMove, true);
      doc.removeEventListener("mouseup", onUp, true);
      if (doc.body) {
        doc.body.style.userSelect = prevUserSelect;
        doc.body.style.cursor = prevCursor;
      }
    }

    doc.addEventListener("mousemove", onMove, true);
    doc.addEventListener("mouseup", onUp, true);
  };
})();
"""

# register js files here
def register_scripts(client):
    client.Script(DRAWER_WIDTH_SCRIPT)
    client.Script(NOV_DRAG_SCRIPT)  # Load first so window.novStartDrag exists when lens is clicked
    client.Script(MAIN_SCALE_BAR_ZOOM_SCRIPT)
    client.Script(_read_js("upset.js"))
    client.Script(_read_js("bar.js"))
    client.Script(_read_js("linechart.js"))
    client.Script(_read_js("mousemove.js"))
    client.Script(_read_js("histogram.js"))
    client.Script(_read_js("chunked_upload.js"))
