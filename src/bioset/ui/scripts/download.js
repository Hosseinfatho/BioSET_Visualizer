function downloadBase64File(base64Data, filename, contentType = 'application/pdf') {
    console.log(`[BioSET] downloadBase64File called for ${filename} (${base64Data.length} chars)`);
    try {
        const link = document.createElement('a');
        link.href = `data:${contentType};base64,${base64Data}`;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        console.log(`[BioSET] Download link clicked successfully`);
    } catch (err) {
        console.error(`[BioSET] Failed to trigger download:`, err);
    }
}

// Attach to window so it's accessible from Trame trigger/eval
window.downloadBase64File = downloadBase64File;
console.log("[BioSET] download.js loaded and window.downloadBase64File registered.");
