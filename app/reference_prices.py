"""The app's own reference price list: manufacturer's price when new.

app/vehicle_data/reference_prices.csv.gz ships with the app (see
scripts/build_price_list.py for where it comes from). It lists the versions
of each model and year with their price, engine, cylinders and drive type.

The list has no trim names. So for a VIN we find the versions of the same
make, model and year, narrow them down with what the VIN tells us (number of
cylinders, drive type, horsepower), and report the RANGE of prices left.
"""
import csv
import gzip
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).parent / "vehicle_data" / "reference_prices.csv.gz"
MAX_YEAR_GAP = 5          # how far to look for the same model in another year
YEARLY_CHANGE = 0.015     # typical yearly rise in new-car list prices, used to adjust
SOURCE = "reference price list (open Edmunds-derived dataset, model years 1990-2017)"


def _key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


@dataclass
class ReferencePrice:
    low: float
    high: float
    typical: float       # the middle price of the matching versions
    versions: int        # how many versions matched
    year: int            # model year the prices were recorded for
    narrowed_by: list[str]
    adjusted: bool = False   # True when taken from another model year and scaled

    def describe(self, wanted_year: int) -> str:
        how = f", narrowed by {' and '.join(self.narrowed_by)}" if self.narrowed_by else ""
        other = (f"; taken from the {self.year} model (the nearest year on file) and adjusted "
                 f"{YEARLY_CHANGE:.1%} a year, so treat it as approximate") if self.adjusted else ""
        return f"from our {SOURCE}: {self.versions} version{'s' if self.versions != 1 else ''} of this model{how}{other}"


@lru_cache(maxsize=1)
def _table() -> dict:
    by_make: dict[str, dict[tuple[str, int], list[dict]]] = defaultdict(lambda: defaultdict(list))
    if FILE.exists():
        with gzip.open(FILE, "rt", newline="") as handle:
            for row in csv.DictReader(handle):
                row["msrp"] = float(row["msrp"])
                by_make[_key(row["make"])][(_key(row["model"]), int(row["year"]))].append(row)
    return by_make


def count() -> int:
    return sum(len(rows) for models in _table().values() for rows in models.values())


def _model_rows(make: str, model: str, year: int) -> list[dict]:
    """Rows for this make, model and year. Model names differ between sources
    ('Silverado' vs 'Silverado 1500', '328i' vs '3 Series'), so fall back to
    the closest name that starts the same way."""
    models = _table().get(_key(make), {})
    wanted = _key(model)
    if not wanted:
        return []
    # Two makes are named differently here than in the vehicle database.
    if _key(make) == "bmw" and wanted[0].isdigit():
        wanted = wanted[0] + "series"            # '328i' -> '3 Series'
    if _key(make) == "mazda" and re.fullmatch(r"mazda\d", wanted):
        wanted = wanted[-1]                      # 'Mazda3' -> '3'
    exact = models.get((wanted, year))
    if exact:
        return exact
    names = {name for (name, y) in models if y == year}
    close = [n for n in names if n.startswith(wanted) or wanted.startswith(n)]
    if not close:
        return []
    # Prefer the shortest extension: 'silverado1500' before 'silverado3500hd'.
    best = min(close, key=lambda n: (abs(len(n) - len(wanted)), n))
    return models[(best, year)]


def _drive_group(text: str | None) -> str | None:
    text = (text or "").upper()
    if not text:
        return None
    if "AWD" in text or "4WD" in text or "4X4" in text or "ALL" in text:
        return "all"      # all four wheels driven
    if "FWD" in text or "RWD" in text or "4X2" in text or "FRONT" in text or "REAR" in text:
        return "two"
    return None


def find(make: str, model: str, year: int, specs: dict | None = None) -> ReferencePrice | None:
    """specs = the VIN's build details, as shown to the inspector
    ({'Engine': '3.6L 6-cyl, 301 hp', 'Drive': 'AWD/All-Wheel Drive', ...})."""
    specs = specs or {}
    rows, used_year = [], year
    gaps = [0] + [g * sign for g in range(1, MAX_YEAR_GAP + 1) for sign in (1, -1)]
    for gap in gaps:
        rows = _model_rows(make, model, year + gap)
        if rows:
            used_year = year + gap
            break
    if not rows:
        return None

    narrowed = []
    engine = specs.get("Engine") or ""
    cylinders = re.search(r"(\d+)-cyl", engine)
    if cylinders:
        kept = [r for r in rows if r["cylinders"] == cylinders.group(1)]
        if kept and len(kept) < len(rows):
            narrowed.append(f"{cylinders.group(1)} cylinders")
        rows = kept or rows
    group = _drive_group(specs.get("Drive"))
    if group:
        kept = [r for r in rows if (r["drive"] in ("AWD", "4WD")) == (group == "all")]
        if kept and len(kept) < len(rows):
            narrowed.append("all-wheel drive" if group == "all" else "two-wheel drive")
        rows = kept or rows
    power = re.search(r"(\d+) hp", engine)
    if power:
        hp = float(power.group(1))
        kept = [r for r in rows if r["hp"] and abs(float(r["hp"]) - hp) <= hp * 0.06]
        if kept and len(kept) < len(rows):
            narrowed.append(f"about {hp:.0f} hp")
        rows = kept or rows
    # Prices from another model year are scaled to this one.
    scale = (1 + YEARLY_CHANGE) ** (year - used_year)
    prices = sorted(round(r["msrp"] * scale, -1) for r in rows)
    return ReferencePrice(low=prices[0], high=prices[-1], typical=statistics.median(prices),
                          versions=len(prices), year=used_year, narrowed_by=narrowed,
                          adjusted=used_year != year)
