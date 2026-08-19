// Canvas mouse tracking.
//
// The hover-highlight and right-click drill-down were removed: every hover
// emit cost a server round-trip plus a full render/JPEG-encode/push cycle at
// 12.5 fps just for moving the mouse, and surface visibility is no longer tied
// to picking a tile. This component now only suppresses the browser context
// menu over the canvas so right-drag camera moves are not interrupted.
Vue.component('hover-tracker', {
  template: '<span style="display:none"></span>',
  mounted() {
    var attached = false;

    function tryAttach() {
      if (attached) return true;
      var canvas = document.querySelector('canvas');
      if (!canvas) return false;
      attached = true;
      canvas.addEventListener('contextmenu', function (e) {
        e.preventDefault();
      });
      return true;
    }

    if (!tryAttach()) {
      var timer = setInterval(function () {
        if (tryAttach()) clearInterval(timer);
      }, 200);
    }
  }
});
