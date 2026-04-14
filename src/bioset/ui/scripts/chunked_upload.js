(function () {
  "use strict";

  var CHUNK_SIZE = 16 * 1024 * 1024; // 16 MB per chunk
  var PARALLEL = 6; // concurrent readers

  window.biosetChunkedUpload = function (file, triggerFn) {
    if (!file) return;

    var name = file.name;
    var total = file.size;
    var totalChunks = Math.ceil(total / CHUNK_SIZE);
    var nextChunk = 0; // next chunk index to dispatch
    var doneChunks = 0; // chunks fully sent
    var active = 0;

    triggerFn("upload_analysis_start", [{ name: name, total_size: total }]);

    function dispatch() {
      while (active < PARALLEL && nextChunk < totalChunks) {
        launchChunk(nextChunk++);
      }
    }

    function launchChunk(idx) {
      var chunkOffset = idx * CHUNK_SIZE;
      var end = Math.min(chunkOffset + CHUNK_SIZE, total);
      var slice = file.slice(chunkOffset, end);
      var reader = new FileReader();
      active++;

      reader.onload = function (e) {
        var arr = new Uint8Array(e.target.result);
        var binary = "";
        for (var i = 0; i < arr.length; i += 8192) {
          binary += String.fromCharCode.apply(
            null,
            arr.subarray(i, Math.min(i + 8192, arr.length))
          );
        }
        var b64 = btoa(binary);

        triggerFn("upload_analysis_chunk", [
          { name: name, data: b64, offset: chunkOffset, total_size: total },
        ]);

        active--;
        doneChunks++;

        if (doneChunks === totalChunks) {
          triggerFn("upload_analysis_complete", [
            { name: name, total_size: total },
          ]);
        } else {
          dispatch();
        }
      };

      reader.onerror = function () {
        console.error("biosetChunkedUpload: read error at chunk", idx);
        active--;
      };

      reader.readAsArrayBuffer(slice);
    }

    dispatch();
  };
})();
