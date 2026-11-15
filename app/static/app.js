// The "Check a vehicle" screen: scan or type a VIN, see the vehicle and its
// expected price. Plain JavaScript, no framework.
(() => {
  const config = JSON.parse(document.getElementById("config").textContent);
  const $ = (id) => document.getElementById(id);
  const photos = { doc: null, vin: null }; // resized photos, ready to upload
  let lookedUpVin = "";                    // the VIN the result on screen belongs to
  const money = (n) => Math.round(n).toLocaleString("en-US");

  // ---------- small helpers ----------
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
      const detail = Array.isArray(data.detail) ? "Please check what you entered." : data.detail;
      throw new Error(detail || "Something went wrong. Please try again.");
    }
    return data;
  }

  // ---------- the lookup ----------
  async function lookup() {
    showError("");
    if (!$("vin").value.trim()) { showError("Scan or type a VIN first."); return; }
    const button = $("lookupBtn");
    button.disabled = true;
    button.textContent = "Looking up";
    try {
      const r = await api("/api/lookup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vin: $("vin").value, make: $("make").value, model: $("model").value, trim: $("trim").value,
          year: $("year").value, mileage: $("mileage").value, new_price: $("newPrice").value }) });
      render(r);
    } catch (error) {
      showError(error.message);
    }
    button.disabled = false;
    button.textContent = "Look up";
  }

  function render(r) {
    const v = r.validation;
    const d = r.decoded;
    $("vin").value = v.vin;
    lookedUpVin = v.ok ? v.vin : "";
    $("result").hidden = false;

    // Messages about the VIN itself.
    const items = [];
    v.errors.forEach((e) => items.push(note("bad", e)));
    v.warnings.forEach((w) => items.push(note("warn", w)));
    (v.suggestions || []).forEach((s) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = `Did you mean ${s}? (its check digit is correct)`;
      button.addEventListener("click", () => { $("vin").value = s; lookup(); });
      items.push(button);
    });
    r.mismatches.forEach((m) => items.push(note("warn", "Mismatch: " + m)));
    if (r.previous.length) {
      items.push(note("warn", `This VIN has been checked ${r.previous.length} time(s) before (latest: check #${r.previous[0].id}).`));
    }
    if (d && !d.make) {
      items.push(note("warn", d.available === false ? d.message
        : "This VIN is not in the vehicle database. That is common for vehicles not built for the US market. You can still add the details by hand under More details."));
    }
    $("vinNotes").replaceChildren(...items);

    // The vehicle.
    const known = d && d.make;
    $("vehicleCard").hidden = !known;
    if (known) {
      $("vehicleTitle").textContent = [d.year, d.make, d.model, d.trim || d.series].filter(Boolean).join(" ");
      const rows = Object.entries(d.specs || {}).flatMap(([label, value]) => {
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = label;
        dd.textContent = value;
        return [dt, dd];
      });
      $("specList").replaceChildren(...rows);
      $("vehicleSource").textContent = d.source ? "Source: " + d.source + "." : "";
    }

    // The expected price.
    $("priceCard").hidden = !v.ok;
    const value = r.value;
    $("priceFound").hidden = !value;
    $("priceMissing").hidden = !!value;
    if (value) {
      $("priceToday").textContent = `About ${money(value.value)} ${value.currency}`;
      $("priceRange").textContent = `${money(value.low)} to ${money(value.high)} ${value.currency}`;
      $("priceNewLine").hidden = !value.new_price;
      if (value.new_price) $("priceNew").textContent = `${money(value.new_price)} ${value.currency}`;
      $("priceSource").textContent = value.source + ".";
    } else if (v.ok) {
      $("priceMissingNote").textContent = (r.note || "No price is stored for this vehicle yet"
        + (r.searches_online ? "." : ", and online search is switched off."))
        + " Enter the manufacturer's price when new once and it is remembered for this model.";
    }
  }

  $("lookupBtn").addEventListener("click", lookup);
  $("relookBtn").addEventListener("click", lookup);
  $("usePriceBtn").addEventListener("click", lookup);
  $("vin").addEventListener("keydown", (event) => { if (event.key === "Enter") lookup(); });
  $("vin").addEventListener("input", () => {
    // A different VIN: the result on screen no longer applies.
    if ($("vin").value.trim().toUpperCase() !== lookedUpVin) { $("result").hidden = true; $("newPrice").value = ""; }
  });

  // ---------- VIN barcode scanning ----------
  function closeScanner() {
    VinBarcode.stopLive();
    $("scanner").hidden = true;
  }
  $("scanBtn").addEventListener("click", async () => {
    showError("");
    $("scanner").hidden = false;
    try {
      await VinBarcode.startLive($("scanVideo"), (vin) => {
        closeScanner();
        $("vin").value = vin;
        $("newPrice").value = "";
        lookup();
      });
    } catch {
      closeScanner();
      showError("The camera could not be opened. Allow camera access, or type the VIN.");
    }
  });
  $("scanCancel").addEventListener("click", closeScanner);

  // ---------- declared price and saving ----------
  function updateRate() {
    const currency = $("currency").value;
    const foreign = currency !== config.baseCurrency;
    $("rateRow").hidden = !foreign;
    $("rateFrom").textContent = currency;
    if (foreign && !$("rate").dataset.touched) $("rate").value = config.rates[currency] || "";
  }
  $("currency").addEventListener("change", () => { delete $("rate").dataset.touched; updateRate(); });
  $("rate").addEventListener("input", () => { $("rate").dataset.touched = "1"; });

  $("saveBtn").addEventListener("click", async () => {
    showError("");
    if (!lookedUpVin) { showError("Look up a VIN first."); return; }
    const price = parseFloat($("price").value.replace(/,/g, ""));
    if (!(price >= 0)) { showError("Enter the declared price as a number."); return; }
    const button = $("saveBtn");
    button.disabled = true;
    const form = new FormData();
    const fields = { vin: "vin", make: "make", model: "model", trim: "trim", year: "year",
      seller: "seller", buyer: "buyer", mileage: "mileage", incoterm: "incoterm",
      declared_price: "price", currency: "currency", exchange_rate: "rate", market_value: "market",
      market_source: "source", notes: "notes", new_price: "newPrice", physical_check: "physical" };
    Object.entries(fields).forEach(([name, id]) => form.append(name, $(id).value));
    // Unless the inspector typed their own reference value, the server works it out.
    form.append("use_provider", $("market").value.trim() ? "" : "1");
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

  // ---------- optional: photos ----------
  // Shrink a photo before upload: faster on mobile data, and re-saving it
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
      if (key === "doc") $("readBtn").hidden = true;
      if (!file) return;
      try {
        photos[key] = await resizePhoto(file);
        $(previewId).src = URL.createObjectURL(photos[key]);
        $(previewId).hidden = false;
        if (key === "doc") $("readBtn").hidden = false;
        showError("");
      } catch {
        showError("That file could not be opened as a photo. Please try another one.");
      }
    });
  }
  wirePhoto("docPhoto", "docPreview", "doc");
  wirePhoto("vinPhoto", "vinPreview", "vin");

  async function ocr(blob) {
    const worker = await Tesseract.createWorker("eng", 1, {
      workerPath: "/static/vendor/tesseract/worker.min.js",
      corePath: "/static/vendor/tesseract",
      langPath: "/static/vendor/tesseract",
      workerBlobURL: false,
      logger: (m) => {
        if (m.status === "recognizing text") $("progress").textContent = `Reading the photo: ${Math.round(m.progress * 100)}%`;
      },
    });
    try {
      return (await worker.recognize(blob)).data.text;
    } finally {
      await worker.terminate();
    }
  }

  // Read the paperwork photo and fill in the "what the paperwork says" fields.
  $("readBtn").addEventListener("click", async () => {
    const button = $("readBtn");
    button.disabled = true;
    $("progress").hidden = false;
    $("progress").textContent = "Preparing the reader (the first time takes a little longer)";
    try {
      const form = new FormData();
      if (config.needsBrowserOcr) form.append("document_text", await ocr(photos.doc));
      else form.append("document", photos.doc, "document.jpg");
      const result = await api("/api/read-document", { method: "POST", body: form });
      const f = result.fields || {};
      const set = (id, value) => { if (value !== null && value !== undefined && value !== "") $(id).value = value; };
      set("make", f.make); set("model", f.model); set("trim", f.trim); set("year", f.year);
      set("price", f.declared_price); set("seller", f.seller); set("buyer", f.buyer);
      set("mileage", f.mileage); set("incoterm", f.incoterm);
      if (f.currency) {
        const select = $("currency");
        if (![...select.options].some((o) => o.value === f.currency)) select.add(new Option(f.currency));
        select.value = f.currency;
        updateRate();
      }
      $("progress").textContent = result.error
        || "Details read from the photo. Reading is never perfect: compare each field with the paper.";
      if (lookedUpVin) lookup(); // re-check the paper details against the VIN
    } catch {
      $("progress").textContent = "The photo could not be read automatically. Please type the details in.";
    }
    button.disabled = false;
  });
})();
