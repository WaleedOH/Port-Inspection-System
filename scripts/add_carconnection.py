"""Rebuild the "New Cars Price 2019" dataset in the built-in price store.

About 32,000 versions with trim names and manufacturer prices, model years
1990 to 2019 (2017 is missing from the source), collected from The Car
Connection and published on Kaggle by Prassanth. Kaggle lists its licence as
"Unknown", so check the rights before any commercial use.

Download and unzip it first, then run:
    python scripts/add_carconnection.py path/to/New_cars_price.csv

  https://www.kaggle.com/datasets/prassanth/new-cars-price-2019
  (direct: https://www.kaggle.com/api/v1/datasets/download/prassanth/new-cars-price-2019)
"""
import csv
import re
import sys
from pathlib import Path

from import_prices import normalise, write

NAME = "carconnection_1990_2019"
URL = "https://www.kaggle.com/datasets/prassanth/new-cars-price-2019"
FIELDS = {f: f for f in ("make", "model", "year", "trim", "cylinders", "hp", "drive", "doors", "style", "msrp")}
TWO_WORD_MAKES = ("Alfa Romeo", "Aston Martin", "Land Rover")
# The source files some models once per body ("Accord Sedan", "Accord Coupe").
# The vehicle database and the other price datasets call all of them "Accord",
# so the body word moves from the model name to the style column.
BODY_WORDS = ("Sedan", "Coupe", "Convertible", "Hatchback", "Wagon", "Cabriolet", "Roadster")
TITLE = re.compile(r"(\d{4}) (.+?) Specs: ?(.*)")


def split_name(name: str) -> tuple[str, str]:
    for make in TWO_WORD_MAKES:
        if name.startswith(make + " "):
            return make, name[len(make) + 1:]
    make, _, model = name.partition(" ")
    return make, model


def plain_model(make: str, model: str) -> str:
    """Name the model the way the vehicle database and the other datasets do."""
    if make == "Mazda" and re.match(r"mazda\d", model, re.I):
        return model[5]                                        # 'MAZDA3', 'Mazda3 4-Door' -> '3'
    if make == "Ford" and model.startswith("Super Duty "):
        return model[len("Super Duty "):]                      # 'Super Duty F-250' -> 'F-250'
    if make == "Ford" and model.startswith("F-150 "):
        return "F-150"                                         # 'F-150 SuperCrew' -> 'F-150'
    return re.sub(r" (?:[24]WD|\d-door)$", "", model, flags=re.I)   # 'Tacoma 4WD', 'Rio 5-door'


def split_body(model: str) -> tuple[str, str]:
    words = model.split()
    if len(words) > 1 and words[-1] in BODY_WORDS:
        return " ".join(words[:-1]), words[-1]
    return model, ""


def cylinders(engine: str) -> str:
    """'Gas V6, 3.5L' and 'Premium Unleaded I-4, 2.0 L' -> '6' and '4'."""
    found = re.search(r"\b(?:V|I|H|W|L|Flat)[- ]?(\d{1,2})\b", engine or "")
    return found.group(1) if found else ""


def drive(text: str) -> str:
    t = (text or "").lower().replace("-", " ")
    if "all wheel" in t:
        return "AWD"
    if "4 wheel" in t or "four wheel" in t:
        return "4WD"
    if "front" in t:
        return "FWD"
    if "rear" in t:
        return "RWD"
    return ""


def records(path: Path):
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            title = TITLE.fullmatch(row["Model"].strip())
            if not title:
                continue
            make, model = split_name(title.group(2))
            model, body = split_body(model)
            model = plain_model(make, model)
            yield {"make": make, "model": model, "year": title.group(1),
                   "trim": row["Style Name"] or title.group(3), "msrp": row["MSRP"],
                   "cylinders": cylinders(row["Engine"]),
                   "hp": (row["SAE Net Horsepower @ RPM"] or "").split("@")[0],
                   "drive": drive(row["Drivetrain"]), "doors": row["Passenger Doors"],
                   "style": body or row["Body Style"]}


def main(path: Path) -> None:
    rows, dropped = normalise(records(path), FIELDS)
    years = f"{min(r[2] for r in rows)}-{max(r[2] for r in rows)}"
    write(NAME, "New Cars Price 2019 dataset with trim names (collected from The Car Connection, "
          "published on Kaggle by Prassanth; licence not stated)", URL, years, rows)
    print(f"{len(rows):,} prices stored as {NAME} ({years}); {dropped:,} rows dropped as unusable")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
