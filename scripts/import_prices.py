"""Add a price dataset to the app's built-in price store.

Any CSV that lists cars with their manufacturer's price can be added. You
say which of its columns hold the make, model, year and price; the script
cleans the rows and writes app/vehicle_data/prices/<name>.csv.gz, which the
app loads automatically on its next start.

Example:
    python scripts/import_prices.py new_cars.csv --name cars_2018_2024 \\
        --title "New car prices 2018-2024 (source ...)" --url https://... \\
        --make Make --model Model --year Year --msrp MSRP --trim Trim

Optional columns: --trim --cylinders --hp --drive --doors --style
"""
import argparse
import csv
import gzip
import json
import re
from pathlib import Path

STORE = Path(__file__).parent.parent / "app" / "vehicle_data" / "prices"
COLUMNS = ["make", "model", "year", "trim", "cylinders", "hp", "drive", "doors", "style", "msrp"]


def clean_number(text, low, high):
    digits = re.sub(r"[^\d.]", "", str(text or ""))
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if low <= value <= high else None


def clean_drive(text):
    t = str(text or "").upper()
    if "ALL" in t or "AWD" in t:
        return "AWD"
    if "FOUR" in t or "4WD" in t or "4X4" in t:
        return "4WD"
    if "FRONT" in t or "FWD" in t:
        return "FWD"
    if "REAR" in t or "RWD" in t:
        return "RWD"
    return ""


def normalise(records, mapping):
    """Turn rows of any dataset into the store's standard rows. Rows without a
    usable make, model, year or price are dropped."""
    kept, dropped = set(), 0
    for record in records:
        get = lambda field: str(record.get(mapping.get(field) or "", "") or "").strip()  # noqa: E731
        year = clean_number(get("year"), 1950, 2100)
        msrp = clean_number(get("msrp"), 2500, 5_000_000)
        if not get("make") or not get("model") or year is None or msrp is None:
            dropped += 1
            continue
        cylinders = clean_number(get("cylinders"), 1, 16)
        hp = clean_number(get("hp"), 20, 2500)
        doors = clean_number(get("doors"), 1, 9)
        kept.add((get("make"), get("model"), int(year), get("trim")[:80],
                  str(int(cylinders)) if cylinders else "", str(int(hp)) if hp else "",
                  clean_drive(get("drive")), str(int(doors)) if doors else "", get("style")[:40], int(msrp)))
    return sorted(kept), dropped


def write(name, title, url, years, rows, market="US"):
    STORE.mkdir(parents=True, exist_ok=True)
    with gzip.open(STORE / f"{name}.csv.gz", "wt", newline="", compresslevel=9) as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    index = STORE / "SOURCES.json"
    sources = json.loads(index.read_text()) if index.exists() else {}
    sources[name] = {"name": title, "years": years, "url": url, "market": market}
    index.write_text(json.dumps(sources, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file")
    parser.add_argument("--name", required=True, help="short file name, letters, digits and _ only")
    parser.add_argument("--title", required=True, help="what the dataset is and where it comes from")
    parser.add_argument("--url", default="")
    parser.add_argument("--market", default="US", help="market the prices are for, e.g. US, Qatar, UAE")
    for field in COLUMNS:
        parser.add_argument(f"--{field}", required=field in ("make", "model", "year", "msrp"),
                            help=f"name of the column holding the {field}")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9_]+", args.name):
        parser.error("--name must be lower-case letters, digits and underscores")
    with open(args.file, newline="", encoding="utf-8-sig") as handle:
        rows, dropped = normalise(csv.DictReader(handle), {f: getattr(args, f) for f in COLUMNS})
    if not rows:
        parser.error("no usable rows found; check the column names")
    years = f"{min(r[2] for r in rows)}-{max(r[2] for r in rows)}"
    write(args.name, args.title, args.url, years, rows, args.market)
    print(f"{len(rows):,} prices stored as {args.name} ({years}); {dropped:,} rows dropped as unusable")


if __name__ == "__main__":
    main()
