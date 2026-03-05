from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent


def _read_js(filename: str) -> str:
    return (_SCRIPTS_DIR / filename).read_text()


# Inline script for NOV lens drag (no separate .js file). Syncs to nov_rect_str via hidden input.
NOV_DRAG_SCRIPT = r"""
(function() {
  "use strict";
  var overlay, lensEl, inputEl, startX, startY, startRX, startRY, side = 0.3;
  var throttleTimer, THROTTLE_MS = 32;

  function getNorm(clientX, clientY) {
    if (!overlay) return { x: 0, y: 0 };
    var r = overlay.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return { x: 0, y: 0 };
    return {
      x: Math.max(0, Math.min(1, (clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, 1 - (clientY - r.top) / r.height))
    };
  }

  function syncToServer(rx, ry, s) {
    var val = rx + "," + ry + "," + s + "," + s;
    var el = document.getElementById("nov-rect-input");
    if (!el) return;
    var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    if (setter) { setter.call(el, val); }
    else { el.value = val; }
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function onMove(e) {
    if (!overlay || !lensEl) return;
    e.preventDefault();
    var n = getNorm(e.clientX, e.clientY);
    var rx = Math.max(0, Math.min(1 - side, startRX + (n.x - startX)));
    var ry = Math.max(0, Math.min(1 - side, startRY + (n.y - startY)));
    if (throttleTimer) return;
    throttleTimer = setTimeout(function() {
      throttleTimer = null;
      syncToServer(rx, ry, side);
    }, THROTTLE_MS);
  }

  function onUp(e) {
    if (e.button !== 0) return;
    if (throttleTimer) clearTimeout(throttleTimer);
    throttleTimer = null;
    if (lensEl) {
      var left = (lensEl.style.left || "35").replace("%", ""), bottom = (lensEl.style.bottom || "35").replace("%", ""), w = (lensEl.style.width || "30").replace("%", "");
      syncToServer(parseFloat(left)/100, parseFloat(bottom)/100, isNaN(parseFloat(w)) ? 0.3 : parseFloat(w)/100);
    }
    document.removeEventListener("mousemove", onMove, true);
    document.removeEventListener("mouseup", onUp, true);
    if (lensEl && lensEl.releasePointerCapture && e.pointerId != null) lensEl.releasePointerCapture(e.pointerId);
  }

  document.addEventListener("mousedown", function(e) {
    if (e.button !== 0 || e.target.closest(".nov-rect-controls")) return;
    var lens = e.target.closest(".nov-rect-lens");
    if (!lens) return;
    overlay = lens.closest(".nov-rect-overlay");
    if (!overlay) return;
    lensEl = lens;
    var left = (lens.style.left || "35").replace("%", ""), bottom = (lens.style.bottom || "35").replace("%", ""), w = (lens.style.width || "30").replace("%", "");
    startRX = parseFloat(left)/100; startRY = parseFloat(bottom)/100; side = parseFloat(w)/100;
    if (isNaN(startRX)) startRX = 0.35; if (isNaN(startRY)) startRY = 0.35; if (isNaN(side)) side = 0.3;
    var n = getNorm(e.clientX, e.clientY);
    startX = n.x; startY = n.y;
    e.preventDefault();
    e.stopPropagation();
    if (lens.setPointerCapture && e.pointerId != null) lens.setPointerCapture(e.pointerId);
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", onUp, true);
  }, true);
})();
"""


def register_scripts(client):
    client.Script(_read_js("upset.js"))
    client.Script(_read_js("bar.js"))
    client.Script(NOV_DRAG_SCRIPT)