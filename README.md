# Import Value Checker

> **Picking this project up?** Start with [docs/HANDOFF.md](docs/HANDOFF.md): what is built, what is proven, and what to do next. Presenting it to a port? See [docs/PORT-PITCH.md](docs/PORT-PITCH.md).

A prototype web app for customs and port inspectors. The inspector photographs a vehicle's import paperwork, and the app compares the declared price with a reference market value.

> ⚠️ A flag from this app is **never proof of fraud**. It is a risk indicator that means "this declaration **needs review** by an officer". A low price can be legitimate (damage, high mileage, trim). Under the WTO Valuation Agreement, customs may not simply replace a declared price with a database value.

## What it does

The main screen does one thing: **scan or type a VIN, and see the vehicle and its expected price.**

1. **VIN.** Scan the barcode on the VIN sticker with the camera, or type the 17 characters, and tap "Look up".
2. **Vehicle.** The app checks the VIN (length, no I/O/Q, check digit) and shows how the factory built the car: year, make, model, trim, engine, drive type, body. This comes from its own built-in vehicle database. If the check digit fails, it offers "did you mean?" fixes for look-alike characters.
3. **Expected price.** It shows the manufacturer's price when new and an estimate of today's value with a typical range, and says where the figures come from. Prices come from the app's own built-in price list. For a model that is not in it, the app asks for the manufacturer's price once and remembers it.
4. **Compare (optional).** Type the declared price and tap "Compare and save". The result page shows the difference in %, a flag, and a plain-language "Why this flag?" explanation, and can be printed or saved as PDF.

Built around that, without adding steps:

- **Instant verdict.** The flag appears as soon as the declared price is typed.
- **Inspection checklist.** For each fact in the VIN (trim, engine, fuel, drive, body, doors, and the VIN's locations on the car) the inspector taps "Matches" or "Differs". Any "Differs" raises a separate **"Needs review: vehicle identity"** flag, even when the price looks fine. This is aimed at a car dressed up or described as a different version.
- **Versions of the same model.** The app lists the versions that model was built in that year (for example Limited, SE, SEL, SHO), marks the one this VIN belongs to, and shows the price gap between versions where prices are known.
- **Repeat sellers and buyers.** The result says when the same seller or buyer appeared in earlier checks and how many of those needed review.
- **Typing help.** The VIN box counts characters, rejects I, O and Q, and looks up by itself at 17 characters.
- **Your own depreciation schedule.** Today's value is the price when new reduced by an age table that an administrator sets in Settings, so it can follow the customs authority's official schedule.
- **For supervisors.** The **Dashboard** shows checks per day, how they were flagged, what officers decided, the sellers and makes that keep needing review, and an estimate of the duty riding on flagged declarations. The History page starts with totals (checks, awaiting a decision, price needs review, identity needs review); tap one to see only those, and search by VIN, seller or buyer.
- **Price bar.** The live verdict and the saved result show where the declared price sits against the expected value and the review thresholds.
- **Audit log.** Administrators see who logged in, saved or opened a check, recorded a decision, changed a price or a setting, or exported data, and can export the log.
- **The port's own name.** Set the organisation's name in Settings; it appears in the header and on the printed record, which has signature lines for the inspector and the reviewing officer.
- **Phone and desk.** One-handed layout with a bottom bar on a phone; a sidebar and two columns on a laptop.
- **On the phone's home screen.** The app can be added to the home screen and opens like an app.

Everything else is optional, under **More details**:

- a photo of the paperwork, which the app can read to fill in what the paper says (make, model, trim, year, price, seller, buyer);
- what the paper says (make, model, trim, year). If it disagrees with the VIN, the check also gets the identity flag;
- mileage, delivery term, a different reference value, and notes.

Every saved check goes into the **History** page, with search by VIN and export to CSV. The app also warns when the same VIN was checked before.

| Flag | Default rule | Label |
|---|---|---|
| 🟢 GREEN | up to 15% below market (or above it) | Within expected range |
| 🟡 YELLOW | 15–30% below market | Needs review |
| 🔴 RED | more than 30% below market | Needs review (priority) |

An administrator can change both thresholds on the Settings page.

## Run it on your computer

You need [Python](https://www.python.org/downloads/) 3.11 or newer. Open a terminal in this folder and run:

```bash
python -m venv .venv                 # a private place for this project's packages
source .venv/bin/activate            # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open <http://localhost:8000>.

The first time it starts, the app creates an administrator account and **prints the username and password in the terminal**. To choose your own instead, set `ADMIN_USERNAME` and `ADMIN_PASSWORD` before the first start (see `.env.example`).

To try it from your phone on the same Wi-Fi, start it with `uvicorn app.main:app --host 0.0.0.0` and open `http://<your computer's IP address>:8000`. Phones only open the camera directly on HTTPS sites, so on plain `http` you may get the photo picker instead. That is one reason to deploy it properly.

### Try it with demonstration data

```bash
python scripts/seed_demo.py            # about 160 made-up checks over the last 60 days
python scripts/seed_demo.py --clear    # remove them again
```

The cars are real VINs from the test data; the declared prices, sellers, buyers and inspectors are invented. Demo checks are saved under inspector names starting with `demo.`. The script refuses to run against a PostgreSQL database unless you add `--force`.

For a public demo site whose storage is erased regularly, the app adds the same demo checks by itself whenever it starts empty. This is on by default on Vercel when no database is connected (`DEMO_DATA=0` switches it off), and elsewhere with `DEMO_DATA=1`. It never runs when `DATABASE_URL` is set.

### Run the tests

```bash
pytest
```

Tests use a throw-away SQLite file and ignore `DATABASE_URL`, so they can never touch real data. To run them on PostgreSQL as well, set `TEST_DATABASE_URL` to an empty database whose name contains `test`.

The tests cover VIN validation, the price comparison logic, the document parser, the mock pricing provider and the whole web flow.

They include **40 real VINs** (34 makes, model years 1993–2015) from the open [vin-test-data](https://github.com/sen4ik/vin-test-data) dataset (MIT licence). Each was decoded once with the live NHTSA service and the answer is saved in `tests/data/real_vins.json`, so the tests run without internet. To call the live NHTSA service as well:

```bash
RUN_LIVE_VPIC=1 pytest
```

The real VINs are all from US-market vehicles. VINs from vehicles built for other markets are not covered by this test data yet.

## Settings (environment variables)

No password or API key is ever written in the code. They are read from environment variables. Copy `.env.example` to `.env`, fill it in, and start with `uvicorn app.main:app --env-file .env`.

| Variable | What it is | Default |
|---|---|---|
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | First administrator account (used once) | `admin` / random, printed |
| `SECRET_KEY` | Signs the login cookies | generated and kept in the data folder |
| `COOKIE_SECURE` | Set to `1` when served over HTTPS | `0` |
| `DATABASE_URL` | PostgreSQL address for permanent storage; empty = SQLite file | empty |
| `DATA_DIR` | Folder for the SQLite file | `data` |
| `VIN_DECODER` | `both` (own database, then NHTSA), `offline` or `online` | `both` |
| `WEB_PRICE_LOOKUP` | `on` to read public car-price pages for missing models, or `off` | `on` |
| `PRICE_RESEARCH` | `off`, or `claude` to search the web for missing prices | `off` |
| `DOCUMENT_READER` | `browser-ocr` or `claude` | `browser-ocr` |
| `PRICING_PROVIDER` | `mock` | `mock` |

## How it is built

The stack is deliberately small: **Python + FastAPI** for the server, **SQLite** (a single file) for the database, and plain HTML, CSS and JavaScript for the screens. There is no build step.

```
app/
  main.py              pages and API, and the price lookup chain
  dashboard.py         the supervisor's figures, worked out from the saved checks
  vin.py               VIN validation, check digit, "did you mean?" fixes
  offline_vin.py       VIN decoding with the app's own vehicle database
  vpic.py              chooses own database or the NHTSA online service
  mismatch.py          paper vs. VIN comparison (make, model, year, trim)
  compare.py           declared price vs. expected value -> flag + explanation
  reference_prices.py  the built-in price list
  web_prices.py        automatic lookup of missing prices on public pages
  trims.py             matches a VIN's trim to the trim names in a price list
  research.py          optional price search with Claude (off by default)
  pricing/             today's value from the price when new (swappable)
  readers/             paperwork photo readers (swappable)
  db.py                all SQL tables, SQLite or PostgreSQL
  config.py            settings read from environment variables
  vehicle_data/        vehicles.db.gz (vehicle database) and prices/ (price datasets)
  templates/, static/  the screens; static/vendor holds the OCR and barcode libraries
scripts/               build the vehicle database, build and import price datasets, seed demo data
tests/                 315 tests, including 40 real VINs in tests/data
docs/                  HANDOFF.md (status and next steps), PORT-PITCH.md, RESEARCH.md
```

### Where the data is stored: your own SQL tables

Everything the app saves lives in SQL tables that the app creates itself on first start (`app/db.py`):

| Table | What it holds |
|---|---|
| `checks` | every check: VIN, paper and decoded details, prices, flags, decision |
| `photos` | the document and VIN photos |
| `price_book` | manufacturer prices, with source and who added them |
| `users` | inspector accounts (passwords stored as hashes) |
| `settings` | thresholds, exchange rates, depreciation schedule, organisation name, duty rate |
| `audit_log` | who did what, and when |
| `web_prices` | prices found on public pages, with each trim's price |
| `vin_cache` | vehicle details fetched online, kept for next time |

The same tables and the same SQL run on two database engines:

- **SQLite** (default): one file in the `data` folder. Nothing to install; ideal on your own computer or a server with a disk.
- **PostgreSQL**: used when `DATABASE_URL` is set. This is what makes data permanent on hosts without a disk, such as Vercel. Any PostgreSQL server works: Supabase, Neon, or your organisation's own. Nothing in the code is tied to one provider, and the data can be exported with standard PostgreSQL tools.

The built-in vehicle database is separate, read-only reference data that ships with the app.

### How a lookup works: own data first, online only when needed

When an inspector scans a VIN, the app answers from **its own stored data**. It goes online only for what is missing, and saves what it finds so the next lookup is local.

| | 1. Own stored data | 2. Online, only if missing | Then |
|---|---|---|---|
| **Vehicle details** | Built-in vehicle database | NHTSA online service | Answer is kept in the app's database |
| **Manufacturer's price** | Price book, vehicle database, built-in price list, prices already found online | Public car-price pages (automatic, see below) | Saved in the price book with its web sources, marked "found online, not yet confirmed" until an administrator confirms it |

**Automatic web lookup** is on by default and needs no key or account. When the stored data has no price for a car's exact model year, the app reads a public car-information page for that make, model and year (MotorTrend, then Car and Driver), takes the manufacturer's price range from it, and saves it in the `web_prices` table. The next car of that model is answered from the database.

- It accepts a price only from a page whose title names that model and year, and only if it is a plausible car price.
- Where the page lists every trim with its price, all of them are saved, and each VIN gets the price of its own trim. Otherwise the app keeps the range for the model.
- It identifies itself honestly, obeys each site's `robots.txt`, and asks about each model and year once. Sites that refuse automated visitors are not used.
- Only make, model and year are sent, never a VIN or a name.
- Found prices are marked "found online, not yet confirmed". Some pages give a single figure without saying which version it is for; the app says so.
- A fixed self-test at `/healthz/price-sources` shows which sources work from the server. In a live run it found prices for 4 of 6 sample cars; it found none for a 2012 BMW 328i or for a Nissan Patrol, which is not sold in the US.
- Set `WEB_PRICE_LOOKUP=off` to keep the app fully offline. Check the sites' terms of use before relying on this in a commercial product.

**Online price search with Claude** is a separate, optional step and is off by default. To switch it on, set `PRICE_RESEARCH=claude` and `ANTHROPIC_API_KEY` (a paid Anthropic account). The app then asks Claude to search the web for the manufacturer's price of the exact year, make, model and trim. It only accepts an answer that is a sensible price **and** cites a page that really came up in the search; otherwise it reports "not found". Only the vehicle description is sent, never the VIN, seller or buyer. This step has been tested with a stand-in for the search service, not yet against the live one.

### The app's own built-in price list

The app ships with about **47,000 manufacturer prices** (price when new) in `app/vehicle_data/prices/`. `app/reference_prices.py` reads them, so a price appears from the VIN alone, with nothing typed and no internet.

| Dataset | Prices | Years | Notes |
|---|---|---|---|
| Trim prices read from public pages (`web_trims_us_<year>`) | 4,068 | 2025–2027 | US. The price of each trim, for about 300 current models. Refreshed with `scripts/pull_new_prices.py` |
| New Cars Price 2019 (from The Car Connection) | 32,200 | 1990–2019, no 2017 | US. Trim names, engine and drive per version. Only models still on sale in 2018–2019, with their earlier years |
| Car Features and MSRP (Edmunds-derived) | 10,086 | 1990–2017 | US. Engine, cylinders and drive per version, no trim names |
| carfinder | 722 | 2025–2027 | US. Cheapest and most expensive version of each model. MIT licence |
| qatarcars | 104 | 2025 | Qatar prices converted from riyals. Covers brands not sold in the US (BYD, Chery, Geely, Jetour). CC BY 4.0, by Paul Musgrave |
| gtcars | 47 | 2014–2017 | High-end cars with real trim names. MIT licence |

How a price is found:

- The app takes the versions of the same make, model and year and narrows them with what the VIN says (trim where the dataset has it, then cylinders, drive, horsepower). It shows the **range** of prices that remain.
- Trim names are matched the way people write them (`app/trims.py`): a VIN's "SXP" finds "SX-Prestige", "EXL" finds "EX-L", and "EX" does not pick "EX-L". A VIN that allows two trims gets the range across both. When the VIN's trim is not in the list, the whole model range is shown.
- When the exact model year is not on file, it uses the nearest year within 5 years, scales the price by 1.5% a year, and says so.
- US prices are preferred. Another market's price is used only when there is no US price, and is labelled as a rough guide.
- A price in the price book (below) always wins over this list.

What it covers, and what it does not:

- On 4,000 real VINs (mostly model years 2003 to 2015) it found a price for 95%, and for 83% it was the exact model year; the rest were adjusted from a nearby year. For 41% the VIN's trim narrowed the price. Before the New Cars Price 2019 dataset was added these figures were 77%, 16% and 0%.
- Models that were dropped before 2018 and are not in the Edmunds-derived dataset still have no price, for example the Jeep Liberty and Patriot, the Chrysler Town and Country and the Ford E-Series vans.
- **Model years 2020 to 2024 have no data of their own.** Cars from those years get a price adjusted from 2019 or 2025–2026, which can be well off when a model was redesigned in between.
- For 2025–2027, about 300 current models have the price of each trim (205 of them for 2027 so far). The other models have only the cheapest and most expensive version, from the carfinder dataset. Models filed under a variant name (many plug-in hybrids, some Mercedes-Benz and electric models) were not found by the pull; the list is printed at the end of each run.

**Adding more price data.** The store is a folder: every `*.csv.gz` file in `app/vehicle_data/prices/` is one dataset, and `SOURCES.json` records where each came from. Any CSV that lists cars with a manufacturer's price can be added with one command, and the app uses it from the next start:

```bash
python scripts/import_prices.py new_cars.csv --name cars_2020_2024 \
    --title "New car prices 2020-2024 (where it came from)" \
    --make Make --model Model --year Year --msrp MSRP --trim Trim
```

When a dataset has trim names, the app uses the VIN's trim to narrow the price first.

**Keeping new model years up to date.** `python scripts/pull_new_prices.py` reads the public page of every current model (MotorTrend, then Car and Driver) and stores the price of each trim, one dataset per model year. It takes about an hour, waits between requests and obeys `robots.txt`. New model years arrive all year round and prices change mid-year, so run it every three months and commit the changed files:

```bash
python scripts/pull_new_prices.py                              # next year and this year
python scripts/pull_new_prices.py --years 2026 --skip-existing   # only fill gaps for one year
```

The prices are the sites' published base prices per trim, without delivery fees or options, so a dealer's window sticker is usually a few thousand dollars higher. Copying a site's whole price list is heavier use than a single lookup: check both sites' terms of use before any commercial use.

**About the largest dataset:** "Car Features and MSRP" was originally collected from Edmunds and published on Kaggle by CooperUnion; this copy was taken from the MIT-licensed `qacBase` R package. The package licence covers the package; the original rights in the price data are less clear, so check this before any commercial use. `scripts/build_price_list.py` rebuilds that file and `scripts/add_open_datasets.py` rebuilds the three small ones.

**About "New Cars Price 2019":** it was collected from The Car Connection and published on Kaggle by Prassanth, where its licence is listed as "Unknown". Treat it like the Edmunds-derived data: fine for a prototype, to be checked before any commercial use. `scripts/add_carconnection.py` rebuilds it.

### The app's own vehicle database

The app is independent: it decodes VINs by itself. `app/vehicle_data/vehicles.db.gz` (about 11 MB) is a copy of NHTSA's public vPIC data, the same data behind the NHTSA online decoder, trimmed to what the app uses. `app/offline_vin.py` reads it.

- On 5,000 real VINs it returned the right make, model and year for 4,992 (99.8%), in under a millisecond each.
- Against live NHTSA answers for the 40 real test VINs, 570 of 600 detail fields were identical. Of the 30 that differed, 27 were trim or series names filed or worded differently (for example "Denali AWD" under series instead of "Denali" under trim; the app compares against both), and 3 were an engine size or drive type missing from the copy.
- The data is a snapshot. To refresh it for new model years, run `python scripts/build_vehicle_db.py path/to/vpic.lite.db` (the script explains where to get the file) and commit the result.
- `VIN_DECODER=both` is the default: the built-in database first, and the NHTSA online service only when it has no details. `offline` never goes online; `online` always asks NHTSA.

Like NHTSA itself, this database covers vehicles built for the US market best.

### The port's own price book

The price is not inside a VIN; manufacturers publish it separately for each model, trim and year. Instead of buying that data, the app keeps its own list on the **Prices** page:

- **It fills itself.** When an inspector types a manufacturer's price during a check, it is saved against the car's *VIN pattern* (characters 1–8 and 10, which identify model, trim, engine and year). Every later car with the same pattern gets that price automatically.
- **Administrators can add prices or import a list** as a CSV file with the columns `make, model, year, msrp` and optionally `trim, vin, source`.
- **Lookup order:** a price typed in for this check, then the price book by VIN pattern, then the price book by make, model and year (preferring the same trim), then the vehicle database, then the built-in price list for the exact model year, then the automatic web lookup, then the built-in list's nearest model year.
- **Every entry records who added it, when, and from what source,** and administrators can remove wrong ones. An entry typed in by mistake would affect later checks until it is removed.

Each result also shows **earlier declarations at this port** for the same VIN pattern, once at least 3 other cars have been checked.

### Swapping the pricing provider

`app/pricing/base.py` defines the interface: one method, `get_value(vin, make, model, year, mileage)`, which returns a value, a low–high range, a source and a date.

Today there is only `mock.py`. It **estimates** today's value from the manufacturer's price when new, reduced for age by the depreciation schedule in Settings. It never invents a price: the price when new comes from the app's own lists or from the inspector. A real market-value provider would replace this estimate.

1. Add a file such as `app/pricing/blackbook.py` with a class that has `get_value()`. Read its API key from an environment variable.
2. Add one line to `PROVIDERS` in `app/pricing/__init__.py`.
3. Set `PRICING_PROVIDER=blackbook`.

### Swapping the document reader

`app/readers/base.py` defines the interface: `read(document, vin_plate, ...)` returns the extracted fields.

- **`browser-ocr`** (default, free, no account). The phone reads the text itself with [Tesseract.js](https://github.com/naptha/tesseract.js), which is included in `app/static/vendor/`, so nothing is downloaded from other sites. Barcodes are read with [zxing-wasm](https://github.com/Sec-ant/zxing-wasm), also included. `readers/parser.py` then finds the fields in the text. It works best on clear, flat, printed documents and will misread poor photos.
- **`claude`** (optional, paid, more accurate). Claude reads the photo directly. Run `pip install anthropic`, then set `DOCUMENT_READER=claude` and `ANTHROPIC_API_KEY`. With this reader the photos are sent to Anthropic's API, so check that this is allowed for your documents. This reader has not been run against the live API yet.

To add another reader, write a class with `read()` and add it to `READERS` in `app/readers/__init__.py`.

## Security

- **Accounts.** Every inspector logs in with their own account. Passwords are stored as salted scrypt hashes. After 5 wrong attempts the login is locked for 5 minutes.
- **Access.** Every page, photo and export requires login. Only administrators can change settings and accounts.
- **Photos.** They are checked to be real images, limited to 8 MB, stored under random names, and resized on the phone before upload, which also removes hidden location data.
- **Web protections.** Database queries use placeholders, pages escape all text, cross-site form posts are blocked, and CSV cells that look like formulas are neutralised.
- **Personal data.** Invoices contain seller and buyer names. Keep the `data/` folder on an encrypted disk, back it up, and agree a retention period with your organisation.

- **Audit log.** Logins (including failed ones), checks saved and opened, decisions, price and settings changes, account changes and exports are recorded with the user and the time. Only administrators can read it.

Not yet included, and needed before real use: HTTPS (your hosting provides it), password reset, and automatic deletion of old records.

## Deploy it

The app needs somewhere permanent for its data: either a **persistent disk** (for the SQLite file) or a **PostgreSQL database**.

**Vercel and other serverless hosts have no persistent disk**, so there the app needs a PostgreSQL database:

1. Create a PostgreSQL database (for example a free Supabase project) and copy its connection string, which looks like `postgresql://user:password@host:5432/dbname`. If the provider offers a "pooler" address, use that one.
2. In Vercel, under Settings → Environment Variables, add it as `DATABASE_URL`, and also set `ADMIN_PASSWORD` and `SECRET_KEY`.
3. Redeploy. The app creates its tables by itself, and the Settings page shows "Saved data: PostgreSQL database (permanent)".

Without `DATABASE_URL`, the app still starts on such hosts in **demo mode**: it shows a warning banner and loses saved data regularly.

**With Docker** (works on a small server or any host that runs containers with a volume, such as Railway or Fly.io):

```bash
docker build -t import-value-checker .
docker run -d -p 8000:8000 -v ivc-data:/data \
  -e ADMIN_PASSWORD='choose-a-long-password' \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  import-value-checker
```

Put it behind HTTPS (hosting platforms do this for you; on your own server use a reverse proxy such as Caddy). The Docker image already sets `COOKIE_SECURE=1`, so login only works over HTTPS; for a local test over plain `http`, add `-e COOKIE_SECURE=0`.

With SQLite, run a single copy of the app. The login lock-out counter is kept per process.

For real customs data, your organisation will probably require hosting on its own network. The same Docker image works there.

## Known limits

- Today's value is an estimate from the price when new and a depreciation schedule, not market data. The built-in price list has no data of its own for model years 2020 to 2024, and many prices are adjusted from a nearby model year.
- The starting exchange rates are samples. Enter the official customs rates in Settings; the inspector can also set the rate per check.
- The vehicle database (NHTSA data) mainly covers vehicles built for the US market. Others often decode to a make only. The check digit is mandatory only in North America, so a mismatch is shown as a warning.
- Chassis numbers that are not 17 characters (pre-1981, Japanese domestic market) are accepted with a warning and are not decoded.
- FOB and CIF prices are recorded but not adjusted for freight and insurance.
- The identity check depends on what NHTSA knows about the VIN. In the 40 real test VINs, 28 had a trim name; the rest gave engine, body and drive details only. Vehicles built for other markets often have none of these.
- The app cannot tell that a VIN plate itself has been swapped or cloned. The inspector should compare the VIN on the dashboard, the door frame and the chassis stamp.

Research notes and ideas for where to take this next are in [docs/RESEARCH.md](docs/RESEARCH.md).
