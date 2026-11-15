"""Price comparison logic: declared price vs. market value -> flag.

The flag is only a RISK INDICATOR. A low declared price can be legitimate
(damage, high mileage, a genuine bargain). It never proves fraud; it only
tells the inspector which declarations deserve a closer look.
"""
from dataclasses import dataclass

GREEN, YELLOW, RED, NO_DATA = "GREEN", "YELLOW", "RED", "NO_DATA"

FLAG_LABELS = {
    GREEN: "Within expected range",
    YELLOW: "Needs review",
    RED: "Needs review (priority)",
    NO_DATA: "No market value, cannot compare",
}

DEFAULT_GREEN_THRESHOLD = 15.0   # up to 15% below market = GREEN
DEFAULT_YELLOW_THRESHOLD = 30.0  # 15-30% below = YELLOW, more than 30% = RED


@dataclass
class Comparison:
    declared_base: float | None   # declared price converted to the base currency
    market_value: float | None
    diff_pct: float | None        # negative = declared is BELOW market
    flag: str
    label: str
    explanation: list[str]        # plain-language steps: "why this flag?"

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def validate_thresholds(green: float, yellow: float) -> None:
    if not (0 <= green < yellow <= 100):
        raise ValueError(
            "Thresholds must satisfy 0 <= green < yellow <= 100 "
            f"(got green={green}, yellow={yellow})."
        )


def convert_to_base(amount: float, exchange_rate: float) -> float:
    """exchange_rate = how many units of base currency one unit of the
    declared currency is worth (e.g. 1 EUR = 1.08 USD -> rate 1.08)."""
    if exchange_rate <= 0:
        raise ValueError("Exchange rate must be greater than zero.")
    return round(amount * exchange_rate, 2)


def compare_prices(
    declared_price: float,
    market_value: float | None,
    exchange_rate: float = 1.0,
    green_threshold: float = DEFAULT_GREEN_THRESHOLD,
    yellow_threshold: float = DEFAULT_YELLOW_THRESHOLD,
    currency: str = "",
    base_currency: str = "USD",
) -> Comparison:
    validate_thresholds(green_threshold, yellow_threshold)
    if declared_price < 0:
        raise ValueError("Declared price cannot be negative.")

    declared_base = convert_to_base(declared_price, exchange_rate)
    steps = []
    if currency and currency != base_currency:
        steps.append(
            f"Declared price {declared_price:,.2f} {currency} x exchange rate "
            f"{exchange_rate:g} = {declared_base:,.2f} {base_currency}."
        )
    else:
        steps.append(f"Declared price: {declared_base:,.2f} {base_currency}.")

    if market_value is None or market_value <= 0:
        steps.append("No market value was available, so no comparison was made.")
        return Comparison(declared_base, None, None, NO_DATA, FLAG_LABELS[NO_DATA], steps)

    diff_pct = round((declared_base - market_value) / market_value * 100, 1)
    below = -diff_pct  # how far BELOW market, as a positive number
    steps.append(f"Reference market value: {market_value:,.2f} {base_currency}.")
    if diff_pct < 0:
        steps.append(f"The declared price is {below:g}% below the market value.")
    else:
        steps.append(f"The declared price is {diff_pct:g}% above (or equal to) the market value.")

    if below <= green_threshold:
        flag = GREEN
        steps.append(f"Up to {green_threshold:g}% below market counts as GREEN.")
    elif below <= yellow_threshold:
        flag = YELLOW
        steps.append(
            f"Between {green_threshold:g}% and {yellow_threshold:g}% below market counts as YELLOW."
        )
    else:
        flag = RED
        steps.append(f"More than {yellow_threshold:g}% below market counts as RED.")
    if flag != GREEN:
        steps.append(
            "This is a risk indicator only, not proof of anything. Condition, damage, "
            "mileage and trim can explain a lower price."
        )
    return Comparison(declared_base, market_value, diff_pct, flag, FLAG_LABELS[flag], steps)
