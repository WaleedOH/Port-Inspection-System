import pytest

from app.compare import GREEN, NO_DATA, RED, YELLOW, compare_prices, convert_to_base


@pytest.mark.parametrize("declared, flag, diff", [
    (20000, GREEN, 0.0),     # equal to market
    (25000, GREEN, 25.0),    # above market is never flagged
    (18000, GREEN, -10.0),
    (17000, GREEN, -15.0),   # exactly 15% below is still "within 15%"
    (16980, YELLOW, -15.1),
    (15000, YELLOW, -25.0),
    (14000, YELLOW, -30.0),  # exactly 30% below is still yellow
    (13980, RED, -30.1),
    (5000, RED, -75.0),
    (0, RED, -100.0),
])
def test_default_thresholds(declared, flag, diff):
    result = compare_prices(declared, 20000)
    assert result.flag == flag
    assert result.diff_pct == diff


def test_thresholds_are_configurable():
    assert compare_prices(15000, 20000).flag == YELLOW
    assert compare_prices(15000, 20000, green_threshold=25, yellow_threshold=50).flag == GREEN
    assert compare_prices(15000, 20000, green_threshold=5, yellow_threshold=10).flag == RED


@pytest.mark.parametrize("green, yellow", [(30, 15), (15, 15), (-1, 30), (15, 101)])
def test_invalid_thresholds_are_rejected(green, yellow):
    with pytest.raises(ValueError):
        compare_prices(100, 100, green_threshold=green, yellow_threshold=yellow)


def test_currency_conversion():
    assert convert_to_base(10000, 1.08) == 10800.0
    # 10,000 EUR at 1.08 = 10,800 USD against a 12,000 USD market = 10% below.
    result = compare_prices(10000, 12000, exchange_rate=1.08, currency="EUR")
    assert result.declared_base == 10800.0
    assert result.diff_pct == -10.0
    assert result.flag == GREEN
    assert "exchange rate" in result.explanation[0]


@pytest.mark.parametrize("rate", [0, -1])
def test_bad_exchange_rate_is_rejected(rate):
    with pytest.raises(ValueError):
        compare_prices(100, 100, exchange_rate=rate)


def test_negative_price_is_rejected():
    with pytest.raises(ValueError):
        compare_prices(-1, 100)


@pytest.mark.parametrize("market", [None, 0, -5])
def test_no_market_value_gives_no_data_not_a_flag(market):
    result = compare_prices(10000, market)
    assert result.flag == NO_DATA
    assert result.diff_pct is None


def test_flags_are_never_called_fraud():
    for declared in (20000, 15000, 5000):
        result = compare_prices(declared, 20000)
        text = (result.label + " ".join(result.explanation)).lower()
        assert "fraud" not in text
    assert "review" in compare_prices(5000, 20000).label.lower()
    assert "review" in compare_prices(15000, 20000).label.lower()
    assert "not proof" in " ".join(compare_prices(5000, 20000).explanation)
