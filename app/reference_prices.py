"""The app's own price store: manufacturer's price when new.

Every *.csv.gz file in app/vehicle_data/prices/ is one price dataset that
ships with the app (SOURCES.json says where each came from; add more with
scripts/import_prices.py). Each row is one version of a model and year with
its price and, where the dataset has them, trim, engine and drive type.

For a VIN we find the versions of the same make, model and year, narrow
them down with what the VIN tells us (trim, cylinders, drive type,
horsepower), and report the RANGE of prices left.
"""
import csv
import gzip
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

STORE = Path(__file__).parent / "vehicle_data" / "prices"
HOME_MARKET = "US"        # prices for this market are preferred; others are a fallback
MAX_YEAR_GAP = 5          # how far to look for the same model in another year
YEARLY_CHANGE = 0.015     # typical yearly rise in new-car list prices, used to adjust


@lru_cache(maxsize=1)
def sources() -> dict:
    index = STORE / "SOURCES.json"
    return json.loads(index.read_text()) if index.exists() else {}


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
    datasets: tuple[str, ...] = ()   # which stored datasets the prices came from

    def describe(self, wanted_year: int) -> str:
        how = f", narrowed by {' and '.join(self.narrowed_by)}" if self.narrowed_by else ""
        other = (f"; taken from the {self.year} model (the nearest year on file) and adjusted "
                 f"{YEARLY_CHANGE:.1%} a year, so treat it as approximate") if self.adjusted else ""
        names = [sources().get(d, {}).get("name", d) for d in self.datasets]
        if any(sources().get(d, {}).get("market", HOME_MARKET) != HOME_MARKET for d in self.datasets):
            other += "; this is another market's price, so treat it as a rough guide"
        return (f"from our built-in price list ({'; '.join(names)}): "
                f"{self.versions} version{'s' if self.versions != 1 else ''} of this model{how}{other}")


@lru_cache(maxsize=1)
def _table() -> dict:
    by_make: dict[str, dict[tuple[str, int], list[dict]]] = defaultdict(lambda: defaultdict(list))
    for path in sorted(STORE.glob("*.csv.gz")):
        dataset = path.name[:-len(".csv.gz")]
        with gzip.open(path, "rt", newline="") as handle:
            for row in csv.DictReader(handle):
                row["msrp"] = float(row["msrp"])
                row["dataset"] = dataset
                by_make[_key(row["make"])][(_key(row["model"]), int(row["year"]))].append(row)
    return by_make


def count() -> int:
    return sum(len(rows) for models in _table().values() for rows in models.values())


def counts_by_dataset() -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for models in _table().values():
        for rows in models.values():
            for row in rows:
                totals[row["dataset"]] += 1
    return dict(totals)


def _words(text: str | None) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(w) >= 2}


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
    # Prices for the home market first. Another market's prices (taxes and
    # specifications differ) are used only when the home market has nothing.
    def market(row) -> str:
        return sources().get(row["dataset"], {}).get("market", HOME_MARKET)

    rows, used_year = [], year
    gaps = [0] + [g * sign for g in range(1, MAX_YEAR_GAP + 1) for sign in (1, -1)]
    for home in (True, False):
        for gap in gaps:
            found = [r for r in _model_rows(make, model, year + gap) if (market(r) == HOME_MARKET) == home]
            if found:
                rows, used_year = found, year + gap
                break
        if rows:
            break
    if not rows:
        return None

    narrowed = []
    # Trim first: it says the most about price. Only datasets with trim names can use it.
    trim_words = _words(specs.get("Trim")) | _words(specs.get("Series"))
    if trim_words:
        kept = [r for r in rows if r.get("trim") and _words(r["trim"]) & trim_words]
        if kept:
            if len(kept) < len(rows):
                narrowed.append(f"trim ({specs.get('Trim') or specs.get('Series')})")
            rows = kept
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
                          adjusted=used_year != year,
                          datasets=tuple(sorted({r["dataset"] for r in rows})))
