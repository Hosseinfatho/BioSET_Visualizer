// NOV popup: drag by top bar to move (event delegation; drag only when clicking bar, not buttons)
(function () {
  var dragging = false;
  var panel = null;
  var startX, startY, startLeft, startBottom;

  function onMouseMove(e) {
    if (!dragging || !panel) return;
    e.preventDefault();
    var dx = e.clientX - startX;
    var dy = e.clientY - startY;
    var newLeft = startLeft + dx;
    var newBottom = startBottom - dy;
    newLeft = Math.max(0, Math.min(newLeft, window.innerWidth - 50));
    newBottom = Math.max(0, Math.min(newBottom, window.innerHeight - 50));
    panel.style.left = newLeft + 'px';
    panel.style.bottom = newBottom + 'px';
    panel.style.transform = 'none';
  }

  function onMouseUp() {
    if (!dragging || !panel) return;
    var input = document.getElementById('nov-popup-pos-input');
    if (input) {
      var rect = panel.getBoundingClientRect();
      var left = Math.round(rect.left);
      var bottom = Math.round(window.innerHeight - rect.bottom);
      input.value = left + ',' + bottom;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    dragging = false;
    panel = null;
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
  }

  document.addEventListener('mousedown', function (e) {
    var header = e.target.closest('[data-nov-drag="header"]') || e.target.closest('.nov-popup-header');
    if (!header) return;
    // Only start drag when clicking the bar itself, not buttons/controls
    if (e.target.closest('button') || e.target.closest('[role="button"]')) return;
    panel = e.target.closest('[data-nov-drag="panel"]') || e.target.closest('.nov-popup-panel');
    if (!panel || !document.body.contains(panel)) return;
    // position:fixed elements often have offsetParent null; check visibility by size
    if (panel.offsetWidth === 0 || panel.offsetHeight === 0) return;
    e.preventDefault();
    e.stopPropagation();
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    var rect = panel.getBoundingClientRect();
    startLeft = rect.left;
    startBottom = window.innerHeight - rect.bottom;
    document.addEventListener('mousemove', onMouseMove, { passive: false });
    document.addEventListener('mouseup', onMouseUp);
  });
})();
