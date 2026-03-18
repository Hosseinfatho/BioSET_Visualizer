// Hover tracker — emits throttled mousemove coords from the VTK canvas
Vue.component('hover-tracker', {
  template: '<span style="display:none"></span>',
  mounted() {
    var self = this;
    var THROTTLE_MS = 80;
    var lastSend = 0;
    var attached = false;

    function tryAttach() {
      if (attached) return true;
      var canvas = document.querySelector('canvas');
      if (!canvas) return false;
      attached = true;

      canvas.addEventListener('mousemove', function (e) {
        var now = Date.now();
        if (now - lastSend < THROTTLE_MS) return;
        lastSend = now;
        self.$emit('hover', [e.offsetX, e.offsetY]);
      });

      canvas.addEventListener('contextmenu', function (e) {
        e.preventDefault();
        self.$emit('rightclick', [e.offsetX, e.offsetY]);
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