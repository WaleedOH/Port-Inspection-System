// The 3-step "new check" screen. Plain JavaScript, no framework.
(() => {
  const config = JSON.parse(document.getElementById("config").textContent);
  const $ = (id) => document.getElementById(id);
  const photos = { doc: null, vin: null }; // resized photos, ready to upload
  let scannedVin = "";                     // VIN read from a barcode, if any
  let useProvider = false;                 // true while the looked-up value is untouched

  // ---------- small helpers ----------
  function showStep(n) {
    [1, 2, 3].forEach((i) => { $("step" + i).hidden = i !== n; });
    [...$("steps").children].forEach((li, i) => li.classList.toggle("on", i === n - 1));
    showError("");
    window.scrollTo(0, 0);
  }
  function showError(message) {
    $("error").textContent = message;
    $("error").hidden = !message;
    if (message) $("error").scrollIntoView({ block: "center" });
  }
  function note(kind, text) {
    const p = document.createElement("p");
    p.className = "note " + kind;
    p.textContent = text; // textContent, never innerHTML: document text is untrusted
    return p;
  }
  async function api(url, options) {
    const response = await fetch(url, options);
    if (response.status === 401) { window.location = "/login"; throw new Error("Please log in again."); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? "Please check the form." : data.detail;
      throw new Error(detail || "Something went wrong. Please try again.");
    }
    return data;
  }
  const postJson = (url, body) => api(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // Shrink the photo before upload: faster on mobile data, and re-saving it
  // also removes hidden data such as GPS location.
  async function resizePhoto(file, maxSide = 2000) {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  }

  function wirePhoto(inputId, previewId, key) {
    $(inputId).addEventListener("change", async (event) => {
      const file = event.target.files[0];
      photos[key] = null;
      $(previewId).hidden = true;
      if (!file) return;
      try {
        photos[key] = await resizePhoto(file);
        $(previewId).src = URL.createObjectURL(photos[key]);
        $(previewId).hidden = false;
        showError("");
      } catch {
        showError("That file could not be opened as a photo. Please try another one.");
      }
    });
  }
  wirePhoto("docPhoto", "docPreview", "doc");
  wirePhoto("vinPhoto", "vinPreview", "vin");

  // ---------- step 1: read the photo ----------
  async function ocr(blob, label, vinOnly = false) {
    const worker = await Tesseract.createWorker("eng", 1, {
      workerPath: "/static/vendor/tesseract/worker.min.js",
      corePath: "/static/vendor/tesseract",
      langPath: "/static/vendor/tesseract",
      workerBlobURL: false,
      logger: (m) => {
        if (m.status === "recognizing text") {
          $("progress").textContent = `Reading ${label}: ${Math.round(m.progress * 100)}%`;
        }
      },
    });
    try {
      // For the VIN plate, only allow characters a VIN can contain (no I, O, Q).
      if (vinOnly) await worker.setParameters({ tessedit_char_whitelist: "ABCDEFGHJKLMNPRSTUVWXYZ0123456789" });
      return (await worker.recognize(blob)).data.text;
    } finally {
      await worker.terminate();
    }
  }

  $("readBtn").addEventListener("click", async () => {
    if (!photos.doc && !photos.vin) { showError("Take or choose a photo first."); return; }
    const button = $("readBtn");
    button.disabled = true;
    $("progress").hidden = false;
    $("progress").textContent = "Preparing the reader (the first time takes a little longer)";
    let message = "";
    try {
      const form = new FormData();
      // A barcode is the most reliable source for the VIN, so look for one first.
      if (!scannedVin) {
        $("progress").textContent = "Looking for a VIN barcode";
        for (const photo of [photos.vin, photos.doc]) {
          if (photo && !scannedVin) setScannedVin(await VinBarcode.fromImage(photo));
        }
      }
      if (config.needsBrowserOcr) {
        if (photos.doc) form.append("document_text", await ocr(photos.doc, "the document"));
        if (photos.vin && !scannedVin) form.append("vin_plate_text", await ocr(photos.vin, "the VIN plate", true));
      } else {
        if (photos.doc) form.append("document", photos.doc, "document.jpg");
        if (photos.vin) form.append("vin_plate", photos.vin, "vin.jpg");
      }
      $("progress").textContent = "Finding the fields";
      const result = await api("/api/read-document", { method: "POST", body: form });
      fillForm(result.fields || {});
      if (scannedVin) $("vin").value = scannedVin;
      const found = Object.values(result.fields || {}).filter((v) => v !== null && v !== "").length;
      message = result.error || (found
        ? "These fields were read automatically. Reading is never perfect: compare every field with the paper and correct mistakes."
        : "Nothing could be read from the photo. Please type the details in.");
    } catch (error) {
      message = "The photo could not be read automatically. Please type the details in.";
    }
    button.disabled = false;
    $("progress").hidden = true;
    $("readNote").textContent = message;
    $("readNote").hidden = false;
    showStep(2);
    if ($("vin").value) checkVin();
  });

  // ---------- VIN barcode scanning ----------
  function setScannedVin(vin) {
    if (!vin) return;
    scannedVin = vin;
    $("vin").value = vin;
    $("scanNote").textContent = `VIN read from barcode: ${vin}`;
    $("scanNote").hidden = false;
  }
  function closeScanner() {
    VinBarcode.stopLive();
    $("scanner").hidden = true;
  }
  document.querySelectorAll("[data-scan]").forEach((button) => button.addEventListener("click", async () => {
    $("scanner").hidden = false;
    $("scanStatus").textContent = "Point the camera at the VIN barcode";
    try {
      await VinBarcode.startLive($("scanVideo"), (vin) => {
        closeScanner();
        setScannedVin(vin);
        if (!$("step2").hidden) checkVin();
      });
    } catch {
      closeScanner();
      showError("The camera could not be opened. Allow camera access, or take a photo of the VIN sticker instead.");
    }
  }));
  $("scanCancel").addEventListener("click", closeScanner);
  $("vin").addEventListener("input", () => { scannedVin = ""; });

  $("skipBtn").addEventListener("click", () => { $("readNote").hidden = true; showStep(2); });

  function fillForm(f) {
    const set = (id, value) => { if (value !== null && value !== undefined) $(id).value = value; };
    set("vin", f.vin); set("make", f.make); set("model", f.model); set("year", f.year); set("trim", f.trim);
    set("price", f.declared_price); set("seller", f.seller); set("buyer", f.buyer);
    set("mileage", f.mileage); set("incoterm", f.incoterm);
    if (f.currency) {
      const select = $("currency");
      if (![...select.options].some((o) => o.value === f.currency)) select.add(new Option(f.currency));
      select.value = f.currency;
    }
  }

  // ---------- step 2: VIN check ----------
  async function checkVin() {
    const box = $("vinResult");
    box.replaceChildren(note("", "Checking the VIN"));
    try {
      const r = await postJson("/api/vin-check", {
        vin: $("vin").value, make: $("make").value, model: $("model").value, year: $("year").value,
        trim: $("trim").value });
      const v = r.validation;
      showSpecs((r.decoded && r.decoded.specs) || {});
      $("vin").value = v.vin;
      const items = [];
      v.errors.forEach((e) => items.push(note("bad", e)));
      if (v.is_standard && v.check_digit_ok) items.push(note("good", "VIN format and check digit are correct."));
      v.warnings.forEach((w) => items.push(note("warn", w)));
      (v.suggestions || []).forEach((s) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn";
        button.textContent = `Did you mean ${s}? (its check digit is correct)`;
        button.addEventListener("click", () => { $("vin").value = s; checkVin(); });
        items.push(button);
      });
      const d = r.decoded;
      if (d && d.available && d.make) {
        items.push(note("", `VIN decodes to: ${[d.year, d.make, d.model || "(model not in database)"].filter(Boolean).join(" ")}`));
        // Offer the decoded values when the paper fields are still empty.
        if (!$("make").value) $("make").value = d.make;
        if (!$("model").value && d.model) $("model").value = d.model;
        if (!$("year").value && d.year) $("year").value = d.year;
      } else if (d && d.message && (!d.available || !d.make)) {
        items.push(note("warn", d.available ? "The VIN database has no details for this VIN. That is common for vehicles not built for the US market." : d.message));
      }
      r.mismatches.forEach((m) => items.push(note("warn", "Mismatch: " + m)));
      if (r.previous.length) {
        items.push(note("warn", `This VIN has been checked ${r.previous.length} time(s) before (latest: check #${r.previous[0].id}).`));
      }
      box.replaceChildren(...items);
      return v.ok;
    } catch (error) {
      box.replaceChildren(note("bad", error.message));
      return false;
    }
  }
  $("vinBtn").addEventListener("click", checkVin);

  // The factory build details recorded in the VIN, for comparing with the real car.
  function showSpecs(specs) {
    const rows = Object.entries(specs).flatMap(([label, value]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value;
      return [dt, dd];
    });
    $("specList").replaceChildren(...rows);
    $("specCard").hidden = rows.length === 0;
    if (!rows.length) $("physical").value = "not_done";
  }

  $("toStep3").addEventListener("click", async () => {
    if (!$("vin").value.trim()) { showError("Enter the VIN."); return; }
    const price = parseFloat($("price").value.replace(/,/g, ""));
    if (!(price >= 0)) { showError("Enter the declared price as a number."); return; }
    if (!(await checkVin())) { showError("Fix the VIN before continuing."); return; }
    const currency = $("currency").value;
    const foreign = currency !== config.baseCurrency;
    $("rateCard").hidden = !foreign;
    $("rateFrom").textContent = currency;
    if (foreign && !$("rate").dataset.touched) $("rate").value = config.rates[currency] || "";
    showStep(3);
  });
  $("rate").addEventListener("input", () => { $("rate").dataset.touched = "1"; });
  $("currency").addEventListener("change", () => { delete $("rate").dataset.touched; });

  document.querySelectorAll("[data-back]").forEach((button) =>
    button.addEventListener("click", () => showStep(Number(button.dataset.back))));

  // ---------- step 3: market value ----------
  $("lookupBtn").addEventListener("click", async () => {
    const box = $("lookupResult");
    box.replaceChildren(note("", "Looking up"));
    try {
      const r = await postJson("/api/market-value", {
        vin: $("vin").value, make: $("make").value, model: $("model").value,
        year: $("year").value, mileage: $("mileage").value, new_price: $("newPrice").value });
      if (!r.found) {
        box.replaceChildren(note("warn", $("year").value
          ? "No price is on file for this vehicle. Enter the manufacturer's price when new above and tap again, or type a market value below."
          : "Enter the vehicle's year on the previous step first."));
        return;
      }
      const v = r.value;
      const money = (n) => n.toLocaleString("en-US", { maximumFractionDigits: 0 });
      $("market").value = v.value;
      $("source").value = v.source;
      useProvider = true;
      box.replaceChildren(note("good",
        `About ${money(v.value)} ${v.currency} today (typical range ${money(v.low)} to ${money(v.high)}). ${v.source}.`));
    } catch (error) {
      box.replaceChildren(note("bad", error.message));
    }
  });
  ["market", "source"].forEach((id) => $(id).addEventListener("input", () => { useProvider = false; }));

  $("saveBtn").addEventListener("click", async () => {
    const button = $("saveBtn");
    button.disabled = true;
    const form = new FormData();
    const fields = { vin: "vin", make: "make", model: "model", year: "year", seller: "seller",
      buyer: "buyer", mileage: "mileage", incoterm: "incoterm", declared_price: "price",
      currency: "currency", exchange_rate: "rate", market_value: "market",
      market_source: "source", notes: "notes", new_price: "newPrice", trim: "trim",
      physical_check: "physical" };
    Object.entries(fields).forEach(([name, id]) => form.append(name, $(id).value));
    form.append("use_provider", useProvider ? "1" : "");
    if (photos.doc) form.append("document", photos.doc, "document.jpg");
    if (photos.vin) form.append("vin_plate", photos.vin, "vin.jpg");
    try {
      const result = await api("/api/checks", { method: "POST", body: form });
      window.location = result.url;
    } catch (error) {
      showError(error.message);
      button.disabled = false;
    }
  });
})();
