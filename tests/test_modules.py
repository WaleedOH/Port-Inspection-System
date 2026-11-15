"""Tests for the pluggable modules: document parser, pricing, mismatch, vPIC parsing."""
from datetime import date

from app.mismatch import find_mismatches
from app.pricing.mock import MockPricingProvider
from app.readers.parser import find_vin, parse_amount, parse_document_text
from app.vpic import parse_vpic_response

INVOICE = """COMMERCIAL INVOICE          No. 2291
Seller: Sunrise Auto Exports LLC
Buyer: Ahmed Trading Co.
Vehicle: 2019 Toyota Camry LE
VIN: 4T1B11HK5KU123456
Mileage: 45,200 miles
Terms: FOB Baltimore
Total Amount: USD 12,500.00
"""


def test_parser_reads_a_clean_invoice():
    f = parse_document_text(INVOICE)
    assert f.vin == "4T1B11HK5KU123456"
    assert f.declared_price == 12500.0 and f.currency == "USD"
    assert (f.make, f.model, f.year) == ("Toyota", "Camry LE", 2019)
    assert f.seller == "Sunrise Auto Exports LLC"
    assert f.buyer == "Ahmed Trading Co"
    assert f.mileage == 45200 and f.incoterm == "FOB"


def test_parser_returns_blanks_for_unreadable_text():
    f = parse_document_text("~~ %% blurry ## nothing here")
    assert f.vin is None and f.declared_price is None and f.make is None


def test_amount_formats():
    assert parse_amount("12,500.00") == 12500.0
    assert parse_amount("12.500,00") == 12500.0   # European style
    assert parse_amount("12 500") == 12500.0
    assert parse_amount("8500") == 8500.0
    assert parse_amount("abc") is None


def test_vin_ocr_fixes_and_preferences():
    # OCR read the digit 0 as the letter O; O is never valid in a VIN.
    assert find_vin("VIN: 1HGCM82633AOO4352") == "1HGCM82633A004352"
    # OCR split the VIN with spaces.
    assert find_vin("VIN 1HGCM 82633 A004352") == "1HGCM82633A004352"
    # The VIN plate photo wins over the document.
    f = parse_document_text("VIN: 4T1B11HK5KU123456", vin_plate_text="1HGCM82633A004352")
    assert f.vin == "1HGCM82633A004352"


def test_european_price_with_symbol():
    f = parse_document_text("Mercedes-Benz C-Class 2020\nInvoice total: € 21.900,00")
    assert f.declared_price == 21900.0 and f.currency == "EUR"
    assert f.make == "Mercedes-Benz" and f.year == 2020


def test_mock_pricing_known_and_unknown_vehicle():
    provider = MockPricingProvider(today=date(2026, 6, 1))
    new = provider.get_value(make="Toyota", model="Camry", year=2026)
    old = provider.get_value(make="TOYOTA", model="Camry LE", year=2019)
    assert new.value == 28000 and old.value < new.value
    assert old.low < old.value < old.high and old.currency == "USD"
    assert "not real" in old.source.lower()
    assert provider.get_value(make="Unknown", model="Thing", year=2020) is None
    assert provider.get_value(make="", model="", year=0) is None


def test_mock_pricing_mileage_adjustment():
    provider = MockPricingProvider(today=date(2026, 6, 1))
    kwargs = dict(make="Honda", model="Civic", year=2020)
    typical = provider.get_value(**kwargs).value
    assert provider.get_value(**kwargs, mileage=200000).value < typical
    assert provider.get_value(**kwargs, mileage=10000).value > typical


def test_mismatch_detection():
    decoded = {"make": "Honda", "model": "Accord", "year": 2003}
    assert find_mismatches({"make": "HONDA", "model": "Accord EX", "year": 2003}, decoded) == []
    problems = find_mismatches({"make": "Toyota", "model": "Camry", "year": 2010}, decoded)
    assert len(problems) == 3 and "Toyota" in problems[0]
    # Blank decoded model (common for non-US vehicles) is not a mismatch.
    assert find_mismatches({"make": "Honda", "model": "Fit", "year": 2003},
                           {"make": "Honda", "model": None, "year": 2003}) == []
    assert find_mismatches({"make": "Mercedes Benz", "model": "", "year": None},
                           {"make": "Mercedes-Benz", "model": "C-Class", "year": 2020}) == []


def test_vpic_response_parsing():
    payload = {"Results": [{"Make": "HONDA", "Model": "Accord", "ModelYear": "2003",
                            "ErrorCode": "0", "ErrorText": "0 - VIN decoded clean"}]}
    d = parse_vpic_response(payload)
    assert (d["make"], d["model"], d["year"], d["clean"]) == ("Honda", "Accord", 2003, True)
    partial = parse_vpic_response({"Results": [{"Make": "TOYOTA", "Model": "", "ModelYear": "",
                                                "ErrorCode": "1,8", "ErrorText": "1 - Check Digit..."}]})
    assert partial["model"] is None and partial["year"] is None and not partial["clean"]
    assert parse_vpic_response({})["make"] is None


def test_estimate_from_manufacturer_price():
    provider = MockPricingProvider(today=date(2026, 6, 1))
    # A model that is NOT in the sample table still gets a value from its MSRP.
    v = provider.get_value(make="Volvo", model="S60", year=2012, new_price=31300)
    assert v is not None and 0 < v.value < 31300
    assert "manufacturer's price when new (31,300 USD, entered by inspector)" in v.source
    # The typed-in MSRP wins over the sample table.
    table = provider.get_value(make="Toyota", model="Camry", year=2026).value
    assert provider.get_value(make="Toyota", model="Camry", year=2026, new_price=40000).value == 40000 != table
    # A brand-new car is worth its price; no year means no estimate.
    assert provider.get_value(make="X", model="Y", year=0, new_price=30000) is None


def test_nhtsa_base_price_is_read_when_present():
    with_price = parse_vpic_response({"Results": [{"Make": "MINI", "Model": "Cooper", "ModelYear": "2006",
                                                   "BasePrice": "17450", "ErrorCode": "0"}]})
    assert with_price["base_price"] == 17450.0
    assert parse_vpic_response({"Results": [{"Make": "FORD", "BasePrice": ""}]})["base_price"] is None
