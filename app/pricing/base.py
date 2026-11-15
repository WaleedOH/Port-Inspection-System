"""The pricing "plug socket".

Any pricing source (mock data today; Black Book, J.D. Power, auction data
later) is a class with one method, get_value(). The rest of the app only
ever talks to this interface, so swapping providers touches one file.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class MarketValue:
    value: float            # best single estimate
    low: float              # realistic range, because used-car prices vary a lot
    high: float
    currency: str
    source: str             # shown to the inspector and saved with the check
    as_of: str              # date of the data

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class PricingProvider(Protocol):
    name: str

    def get_value(self, *, vin: str, make: str, model: str, year: int,
                  mileage: int | None = None) -> MarketValue | None:
        """Return a market value, or None if this vehicle is not covered."""
        ...
