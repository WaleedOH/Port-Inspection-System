"""Build the app's reference price list (manufacturer's price when new).

Source: the open "Car Features and MSRP" dataset (11,914 US-market cars,
model years 1990-2017, originally collected from Edmunds and published on
Kaggle by CooperUnion; this copy comes from the MIT-licensed qacBase R
package, file data/cardata.rda).

    pip install pyreadr
    python scripts/build_price_list.py path/to/cardata.rda

It writes app/vehicle_data/prices/edmunds_1990_2017.csv.gz. Other datasets are
added next to it with scripts/import_prices.py.
"""
import csv
import gzip
import sys
from pathlib import Path

OUT = Path(__file__).parent.parent / "app" / "vehicle_data" / "prices" / "edmunds_1990_2017.csv.gz"
DRIVE = {"front wheel drive": "FWD", "rear wheel drive": "RWD",
         "all wheel drive": "AWD", "four wheel drive": "4WD"}


def number(value) -> str:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return ""


def build(source: str) -> None:
    import pyreadr

    table = list(pyreadr.read_r(source).values())[0]
    rows = set()
    for r in table.itertuples():
        # Cars from before 2001 carry a placeholder price of 2,000 in this dataset.
        if r.msrp <= 2500:
            continue
        rows.add((str(r.make), str(r.model), int(r.year), "", number(r.engine_cylinders), number(r.engine_hp),
                  DRIVE.get(str(r.driven_wheels), ""), number(r.number_of_doors), str(r.vehicle_style),
                  int(r.msrp)))
    with gzip.open(OUT, "wt", newline="", compresslevel=9) as handle:
        writer = csv.writer(handle)
        writer.writerow(["make", "model", "year", "trim", "cylinders", "hp", "drive", "doors", "style", "msrp"])
        writer.writerows(sorted(rows))
    print(f"{len(rows):,} prices written, {OUT.stat().st_size / 1000:.0f} KB")


if __name__ == "__main__":
    build(sys.argv[1])
