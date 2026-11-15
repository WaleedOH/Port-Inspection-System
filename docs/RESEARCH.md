# Research notes (November 2026)

Two research passes were run before building. This is the short version. Figures marked "reported" come from single sources or vendor blogs and should be checked before you rely on them.

## 1. Findings that shaped the app

- **Wording matters legally.** Under the WTO Valuation Agreement, the customs value is the price actually paid. Customs can doubt it, but must follow a process, and Article 7 forbids minimum or arbitrary values. Nigeria's and Ghana's VIN-valuation systems drew strikes and criticism for acting as price-setters. So this app only produces a risk indicator with an explanation, and records the officer's decision.
- **VIN check digit.** It is mandatory only for North American vehicles, so a mismatch is a warning. Wrong length or the letters I, O, Q are errors.
- **NHTSA vPIC.** Free, no key, unpublished rate limits (cache results). It reports problems inside the response body. Non-US vehicles often decode to a make only.
- **Free OCR.** Tesseract.js runs on the phone with no account, but is weakest on skewed or glary phone photos. PaddleOCR on a server is generally stronger and is the natural next reader. Barcodes on US VIN labels can be read with `zxing-wasm` and are far more reliable than OCR.
- **Pricing data.** Black Book, J.D. Power and Manheim are enterprise licences through sales. MarketCheck and VinAudit are self-serve. All are mainly US-market, so manual entry with a source stays important.
- **Currency and delivery terms.** Convert at the official rate for the declaration date and store the rate. FOB excludes freight and insurance; CIF includes them.

## 2. Less crowded directions

Ranked by how underserved the problem looks and how buildable it is on top of this app.

1. **Explainable flags and an appeal pack.** Existing government systems are black boxes that importers dispute and inspectors cannot defend. Show the comparable vehicles, the adjustments and a confidence range, in a one-page document both sides can see. The "Why this flag?" section and print view are the first step.
2. **Salvage-auction damage history.** Many exported US cars are reported to be salvage or rebuilt. A real auction invoice for a wreck can be presented for a car that has since been repaired. Looking up the VIN's auction history would let the app compare against both salvage and repaired values.
3. **Odometer and age consistency.** Mileage rollback is reported as common on used exports to East Africa. Compare the mileage and dates across documents and flag implausible distance per year. Mileage also feeds the market value.
4. **Re-export and sanctions diversion.** Cars declared for Central Asia or the Caucasus are reported to move on to Russia. A destination-risk score (luxury model, new consignee, transit country) would be new for individual used cars.
5. **Stolen-vehicle and VIN-clone screening for freight forwarders.** Forwarders carry liability but have no cheap screening tool. The VIN check and paper-versus-VIN comparison here are the core of one.

Earlier-stage ideas: EV battery health misdeclaration (no export standard yet), Korean and Japanese price data as comparison sources, and the mirror-image problem of over-valuation for banks and insurers.

## 3. Crowded areas to avoid

- AI classification of HS tariff codes.
- Generic customs-value checks for e-commerce goods.
- Government-wide valuation systems (ASYCUDA's valuation module, Ghana's ICUMS, Nigeria's VIN portal).
- Consumer VIN history reports in the US and EU.

## 4. Suggested next steps for the app

1. Test the reader on 30–50 real paperwork photos and record where it fails.
2. Test VIN barcode scanning (now built in) on real door-frame and windscreen stickers.
3. Connect one real pricing source.
4. Show comparable vehicles behind each market value (direction 1).
5. Add a salvage-history lookup (direction 2).
