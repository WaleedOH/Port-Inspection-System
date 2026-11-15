"""Stand-in pricing provider, used until a paid market-data provider exists.

It ESTIMATES today's value from the price when new, reduced for age (and
mileage when known). The price when new comes from, in order:
  1. the manufacturer's price (MSRP) the inspector typed in, or the base
     price NHTSA holds for the VIN (rare);
  2. a small table of SAMPLE prices below, which are not real data.
"""
from datetime import date

from .base import MarketValue

# (make, model) -> sample price when new, in USD. Illustrative numbers only.
SAMPLE_NEW_PRICES = {
    ("toyota", "camry"): 28000, ("toyota", "corolla"): 23000, ("toyota", "rav4"): 30000,
    ("toyota", "land cruiser"): 58000, ("toyota", "hilux"): 32000,
    ("honda", "accord"): 29000, ("honda", "civic"): 25000, ("honda", "cr-v"): 31000,
    ("ford", "f-150"): 40000, ("ford", "mustang"): 33000, ("ford", "explorer"): 39000,
    ("chevrolet", "silverado"): 40000, ("chevrolet", "tahoe"): 58000,
    ("nissan", "altima"): 27000, ("nissan", "patrol"): 60000,
    ("hyundai", "elantra"): 22000, ("hyundai", "tucson"): 29000, ("kia", "sportage"): 28000,
    ("bmw", "3 series"): 46000, ("bmw", "x5"): 67000,
    ("mercedes-benz", "c-class"): 48000, ("mercedes-benz", "e-class"): 63000,
    ("mercedes-benz", "g-class"): 145000,
    ("lexus", "rx"): 50000, ("lexus", "lx"): 95000,
    ("tesla", "model 3"): 42000, ("tesla", "model y"): 47000,
    ("volkswagen", "golf"): 26000, ("audi", "a4"): 43000, ("jeep", "wrangler"): 36000,
    ("land rover", "range rover"): 110000, ("porsche", "cayenne"): 82000,
}


def _key(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def estimate_current_value(new_price: float, year: int, mileage: int | None, today: date) -> float:
    """Rough used-car value: -18% in the first year, then -10% a year, never
    below 8% of the new price; +/-1% per 5,000 miles from typical, max 20%."""
    age = max(0, today.year - year)
    factor = 1.0 if age == 0 else 0.82 * (0.90 ** (age - 1))
    value = new_price * max(factor, 0.08)
    if mileage is not None and age > 0:
        expected = age * 12000  # miles a typical car has at this age
        value *= 1 + max(-0.20, min(0.20, (expected - mileage) / 5000 * 0.01))
    return round(value, -2)


class MockPricingProvider:
    name = "Mock sample data (not real market prices)"

    def __init__(self, today: date | None = None):
        self._today = today

    def get_value(self, *, vin: str = "", make: str = "", model: str = "", year: int = 0,
                  mileage: int | None = None, new_price: float | None = None,
                  new_price_source: str = "") -> MarketValue | None:
        today = self._today or date.today()
        if not year:
            return None
        if new_price and new_price > 0:
            source = (f"Estimate from the manufacturer's price when new "
                      f"({new_price:,.0f} USD, {new_price_source or 'entered by inspector'}), "
                      "reduced for age and mileage by a simple rule")
        else:
            new_price = self._sample_price(make, model)
            source = self.name
        if not new_price:
            return None
        value = estimate_current_value(new_price, int(year), mileage, today)
        return MarketValue(value=value, low=round(value * 0.85, -2), high=round(value * 1.15, -2),
                           currency="USD", source=source, as_of=today.isoformat())

    @staticmethod
    def _sample_price(make: str, model: str) -> float | None:
        make_k, model_k = _key(make or ""), _key(model or "")
        if not make_k or not model_k:
            return None
        for (m, mdl), price in SAMPLE_NEW_PRICES.items():
            # "Camry LE" should still match "camry"; "F150" should match "f-150".
            if _key(m) == make_k and (_key(mdl) in model_k or model_k in _key(mdl)):
                return price
        return None
