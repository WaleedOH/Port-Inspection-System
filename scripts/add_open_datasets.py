"""Rebuild the three small open price datasets in the built-in price store.

Download these files into one folder first, then run:
    python scripts/add_open_datasets.py that_folder

  cars_us.json    https://raw.githubusercontent.com/destinjones/carfinder/main/data/cars_us.json
  gtcars.csv      https://raw.githubusercontent.com/rstudio/gt/master/data-raw/gtcars.csv
  qatarcars.csv   https://raw.githubusercontent.com/profmusgrave/qatarcars/main/inst/extdata/qatarcars.csv
"""
import csv
import json
import sys
from pathlib import Path

from import_prices import normalise, write

QAR_PER_USD = 3.64   # the Qatari riyal is pegged to the US dollar at this rate
FIELDS = {f: f for f in ("make", "model", "year", "trim", "cylinders", "hp", "drive", "doors", "style", "msrp")}


def single_drive(text: str) -> str:
    """'FWD/AWD' means both are sold, so it does not tell versions apart."""
    return "" if "/" in (text or "") else (text or "")


def main(folder: Path) -> None:
    # 1. carfinder (MIT): one row per model with its cheapest and dearest version.
    #    Rows the compilers marked as estimated (est = 1) are left out.
    records = []
    for car in json.loads((folder / "cars_us.json").read_text()):
        make, model, year, body, base, top, hp, drive, estimated = (
            car[0], car[1], car[2], car[3], car[7], car[8], car[15], car[17], car[20])
        if estimated:
            continue
        records.append({"make": make, "model": model, "year": year, "style": body, "msrp": base,
                        "trim": "cheapest version", "drive": single_drive(drive)})
        if top and top != base:
            records.append({"make": make, "model": model, "year": year, "style": body, "msrp": top,
                            "trim": "most expensive version", "drive": single_drive(drive)})
    rows, _ = normalise(records, FIELDS)
    write("carfinder_us_2025_2027", "carfinder new-car price ranges, US, compiled September 2026 "
          "from manufacturer sites (MIT licence, github.com/destinjones/carfinder)",
          "https://github.com/destinjones/carfinder", "2025-2027", rows)
    print(f"carfinder: {len(rows)} prices")

    # 2. gtcars (MIT): 47 high-end cars with real trim names.
    with open(folder / "gtcars.csv", newline="") as handle:
        records = [{"make": r["mfr"], "model": r["model"], "year": r["year"], "trim": r["trim"], "hp": r["hp"],
                    "drive": r["drivetrain"], "style": r["bdy_style"], "msrp": r["msrp"]}
                   for r in csv.DictReader(handle)]
    rows, _ = normalise(records, FIELDS)
    write("gtcars_2014_2017", "gtcars high-end cars with trims (MIT licence, from the R package gt)",
          "https://github.com/rstudio/gt", "2014-2017", rows)
    print(f"gtcars: {len(rows)} prices")

    # 3. qatarcars (CC BY 4.0): 2025 new-car prices in Qatar, converted from riyals.
    with open(folder / "qatarcars.csv", newline="") as handle:
        records = [{"make": r["make"], "model": r["model"], "year": 2025, "hp": r["horsepower"],
                    "style": r["type"], "msrp": round(float(r["price"]) / QAR_PER_USD)}
                   for r in csv.DictReader(handle) if r["price"]]
    rows, _ = normalise(records, FIELDS)
    write("qatar_2025", "Qatar new-car prices 2025, converted from riyals at 3.64 (qatarcars dataset by "
          "Paul Musgrave, CC BY 4.0, mostly from YallaMotor Qatar)",
          "https://github.com/profmusgrave/qatarcars", "2025", rows, market="Qatar")
    print(f"qatarcars: {len(rows)} prices")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
