# Import Value Checker

A prototype web app for customs and port inspectors. The inspector photographs a vehicle's import paperwork, and the app compares the declared price with a reference market value.

> ⚠️ A flag from this app is **never proof of fraud**. It is a risk indicator that means "this declaration **needs review** by an officer". A low price can be legitimate (damage, high mileage, trim). Under the WTO Valuation Agreement, customs may not simply replace a declared price with a database value.

## What it does

1. **Photo.** The inspector takes or uploads a photo of the invoice or customs document, and optionally the VIN plate.
2. **Reading.** The app reads the VIN, declared price, currency, make, model, year, seller, buyer, mileage and delivery term from the photo.
   For the VIN, the app first looks for a **barcode** (live camera scan, or in the photo), which is far more reliable than reading printed characters. Without a barcode it reads the VIN plate text.
3. **Correction.** The inspector sees every field and fixes mistakes before continuing.
4. **VIN check.** The app checks the VIN (17 characters, no I/O/Q, check digit) and decodes it with **its own built-in vehicle database**, so it needs no internet connection and no outside service.
   When the check digit fails, it offers "did you mean?" fixes for look-alike characters (S/5, B/8, Z/2, G/6, D/0, and I/O/Q). It never changes the VIN by itself.
5. **Vehicle identity check.** The VIN records how the factory built the car: trim, engine, drive type, body. Badges and trim parts can be swapped, but the VIN cannot, so the app shows these details for the inspector to compare with the actual vehicle, and the inspector records whether it matches. If the trim on the paperwork differs from the trim in the VIN, or the vehicle does not match, the check gets a separate **"Needs review: vehicle identity"** flag, even when the price looks fine.
   It also warns when the decoded make, model or year differs from the paper, and when the same VIN was checked before.
6. **Market value.** It estimates today's value from the manufacturer's price when new (typed in by the inspector, or from NHTSA when available), or the inspector types a market value in with its source.
7. **Result.** It shows declared price, market value, the difference in %, a flag, and a plain-language "Why this flag?" explanation. The page can be printed or saved as PDF.
8. **History.** Every check is saved with date, inspector, photos, all values and the officer's decision. The history page has search by VIN and export to CSV.

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

### Run the tests

```bash
pytest
```

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
| `DATA_DIR` | Folder for the database and photos | `data` |
| `VIN_DECODER` | `offline` (own database), `online` or `both` | `offline` |
| `DOCUMENT_READER` | `browser-ocr` or `claude` | `browser-ocr` |
| `PRICING_PROVIDER` | `mock` | `mock` |

## How it is built

The stack is deliberately small: **Python + FastAPI** for the server, **SQLite** (a single file) for the database, and plain HTML, CSS and JavaScript for the screens. There is no build step.

```
app/
  vin.py          VIN validation and check digit
  offline_vin.py  VIN decoding with the app's own vehicle database
  vehicle_data/   that database (a copy of NHTSA's public data)
  vpic.py         chooses own database or the NHTSA online service
  mismatch.py     paper vs. decoded VIN comparison
  compare.py      declared price vs. market value -> flag + explanation
  pricing/        market value providers  (swappable)
  readers/        document readers        (swappable)
  db.py           database, password hashing
  main.py         pages and API
  templates/, static/   the screens
tests/
```

### The app's own vehicle database

The app is independent: it decodes VINs by itself. `app/vehicle_data/vehicles.db.gz` (about 11 MB) is a copy of NHTSA's public vPIC data, the same data behind the NHTSA online decoder, trimmed to what the app uses. `app/offline_vin.py` reads it.

- On 5,000 real VINs it returned the right make, model and year for 4,992 (99.8%), in under a millisecond each.
- Against live NHTSA answers for the 40 real test VINs, 570 of 600 detail fields were identical. Of the 30 that differed, 27 were trim or series names filed or worded differently (for example "Denali AWD" under series instead of "Denali" under trim; the app compares against both), and 3 were an engine size or drive type missing from the copy.
- The data is a snapshot. To refresh it for new model years, run `python scripts/build_vehicle_db.py path/to/vpic.lite.db` (the script explains where to get the file) and commit the result.
- `VIN_DECODER=offline` is the default. `online` asks the NHTSA service instead; `both` uses the built-in database first and NHTSA only when it has no details.

Like NHTSA itself, this database covers vehicles built for the US market best.

### The port's own price book

The price is not inside a VIN; manufacturers publish it separately for each model, trim and year. Instead of buying that data, the app keeps its own list on the **Prices** page:

- **It fills itself.** When an inspector types a manufacturer's price during a check, it is saved against the car's *VIN pattern* (characters 1–8 and 10, which identify model, trim, engine and year). Every later car with the same pattern gets that price automatically.
- **Administrators can add prices or import a list** as a CSV file with the columns `make, model, year, msrp` and optionally `trim, vin, source`.
- **Lookup order:** a price typed in for this check, then the price book by VIN pattern, then the price book by make, model and year (preferring the same trim), then NHTSA.
- **Every entry records who added it, when, and from what source,** and administrators can remove wrong ones. An entry typed in by mistake would affect later checks until it is removed.

Each result also shows **earlier declarations at this port** for the same VIN pattern, once at least 3 other cars have been checked.

### Swapping the pricing provider

`app/pricing/base.py` defines the interface: one method, `get_value(vin, make, model, year, mileage)`, which returns a value, a low–high range, a source and a date.

Today there is only `mock.py`. It **estimates** today's value from the price when new, reduced for age and mileage by a simple rule. The price when new is the manufacturer's price (MSRP) the inspector types in, or the base price NHTSA holds for the VIN (it has one for very few vehicles), or else a small table of **sample numbers that are not real data**. No free service was found that returns the manufacturer's price for any VIN, so that needs a paid provider. To plug in Black Book, J.D. Power, auction data or another source:

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

Not yet included, and needed before real use: HTTPS (your hosting provides it), an audit log of who viewed what, password reset, and automatic deletion of old records.

## Deploy it

The app needs one thing from a host: a **persistent disk**, because the database and photos live in the `data` folder.

**Vercel and other serverless hosts have no persistent disk.** The app still starts there, in **demo mode**: it stores data in a temporary folder, shows a warning banner, and loses saved checks, photos and accounts regularly. That is fine for showing the app to someone, and not for real work. On Vercel, set `ADMIN_PASSWORD` and `SECRET_KEY` under Settings → Environment Variables, then redeploy.

**With Docker** (works on a small server or any host that runs containers with a volume, such as Railway or Fly.io):

```bash
docker build -t import-value-checker .
docker run -d -p 8000:8000 -v ivc-data:/data \
  -e ADMIN_PASSWORD='choose-a-long-password' \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  import-value-checker
```

Put it behind HTTPS (hosting platforms do this for you; on your own server use a reverse proxy such as Caddy). The Docker image already sets `COOKIE_SECURE=1`, so login only works over HTTPS; for a local test over plain `http`, add `-e COOKIE_SECURE=0`.

Run a single copy of the app: SQLite and the login lock-out are designed for one process.

For real customs data, your organisation will probably require hosting on its own network. The same Docker image works there.

## Known limits

- Market values are rough estimates, or sample data, until a real provider is connected. The manufacturer's price usually has to be typed in.
- The starting exchange rates are samples. Enter the official customs rates in Settings; the inspector can also set the rate per check.
- The vehicle database (NHTSA data) mainly covers vehicles built for the US market. Others often decode to a make only. The check digit is mandatory only in North America, so a mismatch is shown as a warning.
- Chassis numbers that are not 17 characters (pre-1981, Japanese domestic market) are accepted with a warning and are not decoded.
- FOB and CIF prices are recorded but not adjusted for freight and insurance.
- The identity check depends on what NHTSA knows about the VIN. In the 40 real test VINs, 28 had a trim name; the rest gave engine, body and drive details only. Vehicles built for other markets often have none of these.
- The app cannot tell that a VIN plate itself has been swapped or cloned. The inspector should compare the VIN on the dashboard, the door frame and the chassis stamp.

Research notes and ideas for where to take this next are in [docs/RESEARCH.md](docs/RESEARCH.md).
