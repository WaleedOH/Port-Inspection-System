"""Choose the pricing provider from the PRICING_PROVIDER environment variable.

To add a real provider later:
  1. Create app/pricing/yourprovider.py with a class that has get_value().
     Read its API key from an environment variable (never write it in code).
  2. Add one line to PROVIDERS below.
  3. Set PRICING_PROVIDER=yourprovider.
"""
from .. import config, db
from .base import MarketValue, PricingProvider
from .mock import MockPricingProvider

PROVIDERS = {
    "mock": MockPricingProvider,
}


def get_provider() -> PricingProvider:
    name = config.PRICING_PROVIDER()
    if name not in PROVIDERS:
        raise ValueError(f"Unknown PRICING_PROVIDER '{name}'. Options: {', '.join(PROVIDERS)}")
    if name == "mock":
        settings = db.get_settings()
        return MockPricingProvider(schedule=settings["depreciation"],
                                   adjust_for_mileage=settings["adjust_for_mileage"])
    return PROVIDERS[name]()


__all__ = ["MarketValue", "PricingProvider", "get_provider"]
