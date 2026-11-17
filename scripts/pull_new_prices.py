"""Pull this year's per-trim prices for every current model into the built-in store.

Run it a few times a year (new model years arrive all year round):
    python scripts/pull_new_prices.py

For each model the store already knows from the last two model years, it reads
the model's public page for each model year asked for (MotorTrend, then Car
and Driver), takes the price of every trim, and writes one dataset per model year:
app/vehicle_data/prices/web_trims_us_<year>.csv.gz. A later run adds to those
files and replaces the models it reads again; it never drops a model just
because a page could not be read this time.

It uses the app's own web lookup, so the same rules apply: it identifies
itself honestly, obeys robots.txt, sends only make, model and year, and waits
between requests. Check the sites' terms of use before relying on this in a
commercial product.

Options:
    --years 2027 2026    model years to read for each model
                         (default: next year and this year)
    --skip-existing      leave out models the store already has for that year
    --makes Kia Toyota   only these makes
    --limit 20           stop after this many models (for a trial run)
    --delay 2            seconds to wait between requests
"""
import argparse
import csv
import gzip
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import reference_prices, web_prices  # noqa: E402
from import_prices import COLUMNS, STORE, normalise, write  # noqa: E402

FIELDS = {f: f for f in COLUMNS}


def dataset_name(year: int) -> str:
    return f"web_trims_us_{year}"


def current_models(newest_years: int = 2) -> list[tuple[str, str]]:
    """Makes and models the store has for its most recent model years, US only."""
    found: dict[tuple[str, str], tuple[str, str]] = {}
    latest = 0
    rows = []
    for path in sorted(STORE.glob("*.csv.gz")):
        name = path.name[:-len(".csv.gz")]
        if reference_prices.sources().get(name, {}).get("market", "US") != "US":
            continue
        with gzip.open(path, "rt", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append((row["make"], row["model"], int(row["year"])))
                latest = max(latest, int(row["year"]))
    for make, model, year in rows:
        if year > latest - newest_years - 1:
            found.setdefault((web_prices._key(make), web_prices._key(model)), (make, model))
    return sorted(found.values())


def trim_only(name: str, model: str) -> str:
    """'Telluride X-Line EX AWD' -> 'X-Line EX AWD'."""
    return re.sub(rf"^\s*{re.escape(model)}\b\s*", "", name, flags=re.IGNORECASE).strip() or name


def read_year(client, make: str, model: str, year: int, delay: float) -> tuple[list, str] | None:
    """([(trim, price)], site) from the first page that lists this year's trims."""
    for site, pattern, separator in web_prices.SITES:
        for name in web_prices.model_names(make, model):
            outcome = web_prices.try_site(client, site, pattern, separator, make, name, year)
            time.sleep(delay)
            if outcome["result"] == "found" and outcome.get("trims"):
                return outcome["trims"], site
            if outcome["result"].startswith(("not allowed", "site answered 403", "could not be reached")):
                break
    return None


def existing_rows(year: int) -> list[dict]:
    path = STORE / f"{dataset_name(year)}.csv.gz"
    if not path.exists():
        return []
    with gzip.open(path, "rt", newline="") as handle:
        return list(csv.DictReader(handle))


def save(collected: dict[int, dict[tuple[str, str], list[dict]]], sites: set[str]) -> None:
    for year, models in collected.items():
        fresh = {key for key in models}
        records = [r for r in existing_rows(year)
                   if (web_prices._key(r["make"]), web_prices._key(r["model"])) not in fresh]
        for rows in models.values():
            records += rows
        rows, _ = normalise(records, FIELDS)
        write(dataset_name(year),
              f"Prices of each trim for model year {year}, read from public pages "
              f"({' and '.join(sorted(sites)) or 'MotorTrend and Car and Driver'}), last pulled {date.today().isoformat()}",
              "https://www.motortrend.com/cars/", str(year), rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    this_year = date.today().year
    parser.add_argument("--years", type=int, nargs="+", default=[this_year + 1, this_year])
    parser.add_argument("--makes", nargs="+", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()
    have = {year: {(web_prices._key(r["make"]), web_prices._key(r["model"])) for r in existing_rows(year)}
            for year in args.years} if args.skip_existing else {}

    models = current_models()
    if args.makes:
        wanted = {web_prices._key(m) for m in args.makes}
        models = [m for m in models if web_prices._key(m[0]) in wanted]
    if args.limit:
        models = models[:args.limit]
    print(f"{len(models)} models to read, model years {args.years}", flush=True)

    collected: dict[int, dict[tuple[str, str], list[dict]]] = {}
    sites: set[str] = set()
    missed = []
    with web_prices._new_client() as client:
        for number, (make, model) in enumerate(models, 1):
            key = (web_prices._key(make), web_prices._key(model))
            got = []
            for year in args.years:
                if key in have.get(year, ()):
                    got.append(f"{year} already stored")
                    continue
                try:
                    result = read_year(client, make, model, year, args.delay)
                except Exception as exc:      # one bad page must not lose the whole pull
                    result = None
                    print(f"  error on {year} {make} {model}: {type(exc).__name__}", flush=True)
                if not result:
                    continue
                trims, site = result
                sites.add(site)
                collected.setdefault(year, {})[key] = [
                    {"make": make, "model": model, "year": year, "trim": trim_only(name, model),
                     "drive": name, "msrp": price} for name, price in trims]
                got.append(f"{year}: {len(trims)} trims from {site}")
            if not got:
                missed.append(f"{make} {model}")
            print(f"[{number}/{len(models)}] {make} {model}: {'; '.join(got) or 'no trim prices found'}", flush=True)
            if number % 25 == 0:
                save(collected, sites)        # keep what we have if the run is interrupted
    save(collected, sites)
    print(f"\nStored trim prices for {len(models) - len(missed)} of {len(models)} models "
          f"({', '.join(f'{len(m)} read for {y}' for y, m in sorted(collected.items()))}).")
    if missed:
        print(f"No trim prices for {len(missed)}: {', '.join(missed)}")


if __name__ == "__main__":
    main()
