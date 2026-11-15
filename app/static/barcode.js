// Reads the VIN from a barcode, using the open-source ZXing library that is
// bundled with the app (nothing is sent anywhere).
window.VinBarcode = (() => {
  ZXingWASM.prepareZXingModule({
    overrides: {
      locateFile: (path, prefix) =>
        path.endsWith(".wasm") ? "/static/vendor/zxing/zxing_reader.wasm" : prefix + path,
    },
  });
  // Code 39 and Data Matrix are what VIN labels use; the others cover paperwork.
  const OPTIONS = { formats: ["Code39", "DataMatrix", "QRCode", "Code128", "PDF417"], tryHarder: true };
  let stream = null;
  let timer = null;

  // Pick a VIN out of whatever the barcode contains.
  function vinFromText(text) {
    for (let token of (text || "").toUpperCase().split(/[^A-Z0-9]+/)) {
      // Code 39 VIN labels often start with an extra "I" (an import marker).
      if (token.length === 18 && token[0] === "I") token = token.slice(1);
      if (token.length === 17 && !/[IOQ]/.test(token) && /[A-Z]/.test(token) && /\d/.test(token)) return token;
    }
    return "";
  }

  async function read(source) {
    try {
      for (const result of await ZXingWASM.readBarcodes(source, OPTIONS)) {
        const vin = vinFromText(result.text);
        if (vin) return vin;
      }
    } catch { /* an unreadable image simply means "no barcode found" */ }
    return "";
  }

  function stopLive() {
    clearTimeout(timer);
    timer = null;
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }

  // Show the back camera in <video> and call onFound(vin) once a VIN barcode is seen.
  async function startLive(video, onFound) {
    stopLive();
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false });
    video.srcObject = stream;
    await video.play();
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const tick = async () => {
      if (!stream) return;
      if (video.videoWidth) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        context.drawImage(video, 0, 0);
        const vin = await read(context.getImageData(0, 0, canvas.width, canvas.height));
        if (vin && stream) { onFound(vin); return; }
      }
      timer = setTimeout(tick, 250);
    };
    tick();
  }

  return { fromImage: read, startLive, stopLive, vinFromText };
})();
