# Handoff: where the project stands

Written 16 November 2026, for continuing the work from another computer. The README explains how each part works; this file says what exists, what has actually been proven, and what to do next.

## Get it running on your laptop

You need [Python](https://www.python.org/downloads/) 3.11 or newer and git.

```bash
git clone https://github.com/WaleedOH/Port-Inspection-System.git
cd Port-Inspection-System
git checkout claude/peaceful-cori-ak5cxx     # all the work is on this branch
python -m venv .venv
source .venv/bin/activate                    # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest                                       # should end with "304 passed, 5 skipped"
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. The first start prints the admin username and password in the terminal. On your laptop everything is saved permanently in the `data/` folder, with no demo mode.

- A fresh clone of this branch was tested on 16 November 2026: the tests pass and the app starts.
- The repository has no `main` branch yet. Everything is on `claude/peaceful-cori-ak5cxx`. Creating `main` from it and making it the default is a sensible first step.
- To continue with Claude Code on the laptop, open it in this folder and point it at this file.

## The live test site

- Address: <https://port-inspection-system-eight.vercel.app>, deployed automatically from the branch on every push.
- Login: username `admin`. The password is the `ADMIN_PASSWORD` environment variable in the Vercel project (Settings → Environment Variables). It is not written in this repository. It was shared in a chat, so change it before anyone else uses the site.
- Vercel variables already set: `ADMIN_PASSWORD`, `SECRET_KEY`, `PRICE_RESEARCH=claude` (inactive, because there is no `ANTHROPIC_API_KEY`).
- **The site runs in demo mode.** Vercel has no permanent disk and no database is connected, so saved checks, photos, accounts, the price book and prices found online are erased regularly. The built-in vehicle database and built-in price list are part of the code and are not affected.

## What is built

| Area | Status |
|---|---|
| One-screen flow: scan or type a VIN, see the vehicle and expected price | Working |
| VIN check: length, I/O/Q, check digit, "did you mean?" fixes | Working. The check digit agreed with about 547,700 real VINs |
| VIN decoding from the app's own vehicle database, no internet | Working. Right make, model and year for 4,992 of 5,000 real VINs |
| VIN barcode scan with the camera, and from a photo | Working with a generated barcode and a simulated camera |
| Built-in price list, about 11,000 prices in 4 datasets | Working. Found a price for 75% of 4,000 real VINs, only 15% for the exact model year |
| Automatic web lookup of missing prices, saved after the first find | Working on the live server for 4 of 6 sample cars |
| Price book that learns typed-in prices, plus CSV import | Working |
| Today's value from a depreciation schedule set in Settings | Working. The schedule holds sample figures |
| Instant verdict, flags, "Why this flag?", print to PDF | Working |
| Inspection checklist and the "vehicle identity" flag | Working |
| Versions of the same model, with the price gap where known | Working. Quality varies by make |
| Repeat seller and buyer warnings, comparison with earlier declarations | Working |
| History with search, totals, CSV export, officer decision | Working |
| Paperwork photo reading (free, on the phone) | Working on a clean printed sample only |
| Accounts, hashed passwords, login lock-out, access control | Working |
| SQLite or PostgreSQL storage, photos kept in the database | Working. All tests pass on both |
| Add to the phone's home screen | Built. Not tried on a real phone |

## Built but never run for real

Be careful with these: the code exists and has tests with stand-ins, but it has not met the real thing.

- **Anything on a real phone.** All screen testing was in a phone-sized desktop browser. The camera scan has never read a real VIN sticker.
- **The live site after login.** From the build environment only the login page and a self-test page could be loaded. Nobody has confirmed the full flow on the Vercel site with the latest version.
- **Paperwork photo reading on real documents.** It read one clean, computer-made invoice. Expect many misreads on real, wrinkled or handwritten paperwork.
- **The Claude photo reader and the Claude price search** (`readers/claude_reader.py`, `research.py`). Never run against the live service; they need a paid API key.
- **A hosted PostgreSQL database.** Tested against a local PostgreSQL server only, never against Supabase or Neon.
- **The Docker image.** The Dockerfile has never been built.
- **Non-US vehicles.** All 40 real test VINs are US-market cars.

## The biggest open problem: price data

The tool is only as good as its prices, and this is where it is weakest.

- **Model years 2018 to 2024 have no stored data.** They depend on the web lookup, or on a price adjusted from 2017 or 2025–2026.
- **The web lookup reads two sites** (MotorTrend, Car and Driver). It gives a range for the model, not the exact trim, and sometimes a single figure of unclear meaning. It found nothing for a 2012 BMW 328i or a Nissan Patrol. The sites could block it or change their pages at any time, and their terms of use have not been checked.
- **The largest dataset's rights are unclear.** It was originally collected from Edmunds. Fine for a prototype; get it checked before selling the product.
- **Today's value is a formula, not market data.** It is the price when new multiplied by an age percentage.
- **Makes with no stored prices** include Jeep, Ram and MINI before 2025.

Ways forward, cheapest first:

1. **Download the "New Cars Price 2019" dataset from Kaggle** (about 32,000 trims with prices, 1990 to 2019, collected from The Car Connection) and load it with `scripts/import_prices.py`. Kaggle was blocked from the build environment. This adds trim names and 2018–2019.
2. **Get a price list or past declarations from the customs authority** and import them. The Prices page takes CSV files.
3. **Buy listing data from Rebrowser** (AutoTrader listings, listed at $2 per 1,000 rows). It has manufacturer prices for new cars and Kelley Blue Book value ranges for used ones, which would replace the depreciation formula. Its free sample is for non-commercial use only, so it is not bundled.
4. **A paid VIN data provider** for the price of the exact car as built. MarketCheck's NeoVIN was the best fit found, at about $0.08 per lookup. None of the providers was confirmed to cover Gulf, European or Japanese market cars.

## Next steps, in order

1. **Try it on a real phone with real cars.** Scan real VIN stickers, check several cars you know the price of, and write down what is wrong. This will change the priorities below.
2. **Give the data a permanent home.** Either run the app on your own machine or server (free, nothing to set up), or connect a PostgreSQL database to the Vercel site by setting `DATABASE_URL`.
3. **Improve price coverage** using the list above, starting with the Kaggle dataset.
4. **Enter the real depreciation schedule and exchange rates** in Settings. Both hold sample figures.
5. **Decide the target market.** The vehicle database and most prices are for US-market cars. If the port mostly sees Gulf, European or Japanese market cars, the data sources need to change, and `HOME_MARKET` in `app/reference_prices.py` should be set.
6. **Test paperwork reading on 30 to 50 real documents** and decide whether the free reader is good enough.
7. **Before any real use:** change the admin password, set up HTTPS hosting on the organisation's own network, add an audit log of who viewed what, password reset, and a retention rule for old records.

## Ideas not started

- Other languages for the screens.
- Reading a stamped VIN with the camera when there is no barcode. The free reader is unreliable on stamped metal.
- Salvage-auction history, odometer consistency and stolen-vehicle screening. See [RESEARCH.md](RESEARCH.md).
- Adjusting FOB and CIF prices for freight and insurance.

## Things that will trip you up

- **Tests never go online and never touch a real database.** `tests/conftest.py` switches the web lookup off and ignores `DATABASE_URL`. To run tests on PostgreSQL, set `TEST_DATABASE_URL` to an empty database whose name contains `test`.
- **Five tests are skipped on purpose.** They call the live NHTSA service; run them with `RUN_LIVE_VPIC=1 pytest`.
- **The web lookup is on by default.** On your laptop the app will read MotorTrend and Car and Driver when a price is missing. Set `WEB_PRICE_LOOKUP=off` to stop that.
- **A failed web lookup is remembered for 14 days** in the `web_prices` table, so a model that returned nothing is not retried until then.
- **The vehicle database unpacks itself** into the data folder (about 37 MB) the first time a VIN is decoded.
- **Rebuilding the data files** needs extra downloads, described at the top of each script in `scripts/`. `scripts/build_price_list.py` also needs `pip install pyreadr`.
- **Phones cache the screens' scripts.** Their addresses carry a version fingerprint, so a normal reload picks up changes. If a phone still shows an old screen, close the tab and reopen it.
- **The `.gitignore` rule for data is `/data/`** with the leading slash. Without it, `tests/data` is ignored too, which once kept the real-VIN test file out of the repository.

## Where things were found

- `WaleedOH/Port-Inspection-System` on GitHub, branch `claude/peaceful-cori-ak5cxx`.
- Vercel project `port-inspection-system`, team "Hunter X Hunter Ship".
- Self-test of the web lookup on the live server: `/healthz/price-sources` (fixed sample cars, no input).
