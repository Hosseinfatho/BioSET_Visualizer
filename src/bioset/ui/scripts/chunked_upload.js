(function () {
  "use strict";

  var CHUNK_SIZE = 2 * 1024 * 1024; // 2 MB per chunk

  window.biosetChunkedUpload = function (file, triggerFn) {
    if (!file) return;

    var name = file.name;
    var total = file.size;
    var offset = 0;

    // Signal start so Python can prepare the temp file
    triggerFn("upload_analysis_start", [{ name: name, total_size: total }]);

    function readNext() {
      if (offset >= total) {
        triggerFn("upload_analysis_complete", [
          { name: name, total_size: total },
        ]);
        return;
      }

      var end = Math.min(offset + CHUNK_SIZE, total);
      var slice = file.slice(offset, end);
      var reader = new FileReader();

      reader.onload = function (e) {
        var arr = new Uint8Array(e.target.result);
        // Convert to base64
        var binary = "";
        for (var i = 0; i < arr.length; i += 8192) {
          binary += String.fromCharCode.apply(
            null,
            arr.subarray(i, Math.min(i + 8192, arr.length))
          );
        }
        var b64 = btoa(binary);

        triggerFn("upload_analysis_chunk", [
          { name: name, data: b64, offset: offset, total_size: total },
        ]);
        offset = end;
        // Small delay to avoid flooding the websocket
        setTimeout(readNext, 10);
      };

      reader.onerror = function () {
        console.error("biosetChunkedUpload: read error at offset", offset);
      };

      reader.readAsArrayBuffer(slice);
    }

    readNext();
  };
})();
