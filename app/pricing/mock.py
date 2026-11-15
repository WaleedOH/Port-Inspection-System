"""Mock pricing provider: SAMPLE VALUES ONLY, not real market data.

It lets the whole app be tried end-to-end before a paid provider exists.
Value = sample new price, reduced for age (and mileage when known).
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


class MockPricingProvider:
    name = "Mock sample data (not real market prices)"

    def __init__(self, today: date | None = None):
        self._today = today

    def get_value(self, *, vin: str = "", make: str, model: str, year: int,
                  mileage: int | None = None) -> MarketValue | None:
        today = self._today or date.today()
        make_k, model_k = _key(make or ""), _key(model or "")
        if not make_k or not model_k or not year:
            return None
        new_price = None
        for (m, mdl), price in SAMPLE_NEW_PRICES.items():
            # "Camry LE" should still match "camry"; "F150" should match "f-150".
            if _key(m) == make_k and (_key(mdl) in model_k or model_k in _key(mdl)):
                new_price = price
                break
        if new_price is None:
            return None

        age = max(0, today.year - int(year))
        # Simple depreciation: -18% in year one, then -10% a year, floor at 8%.
        factor = 1.0 if age == 0 else 0.82 * (0.90 ** (age - 1))
        value = new_price * max(factor, 0.08)
        if mileage is not None and age > 0:
            expected = age * 12000          # miles a typical car has at this age
            # +/- 1% of value per 5,000 miles away from typical, capped at 20%.
            adjustment = max(-0.20, min(0.20, (expected - mileage) / 5000 * 0.01))
            value *= 1 + adjustment
        value = round(value, -2)
        return MarketValue(value=value, low=round(value * 0.85, -2), high=round(value * 1.15, -2),
                           currency="USD", source=self.name, as_of=today.isoformat())
