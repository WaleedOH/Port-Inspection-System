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
    provider = MockPricingProvider(today=date(2026, 6, 1), use_samples=True)
    new = provider.get_value(make="Toyota", model="Camry", year=2026)
    old = provider.get_value(make="TOYOTA", model="Camry LE", year=2019)
    assert new.value == 28000 and old.value < new.value
    assert old.low < old.value < old.high and old.currency == "USD"
    assert "not real" in old.source.lower()
    assert provider.get_value(make="Unknown", model="Thing", year=2020) is None
    assert provider.get_value(make="", model="", year=0) is None


def test_mock_pricing_mileage_adjustment():
    provider = MockPricingProvider(today=date(2026, 6, 1), use_samples=True)
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
    provider = MockPricingProvider(today=date(2026, 6, 1), use_samples=True)
    # A model that is NOT in the sample table still gets a value from its MSRP.
    v = provider.get_value(make="Volvo", model="S60", year=2012, new_price=31300)
    assert v is not None and 0 < v.value < 31300
    assert "manufacturer's price when new (31,300 USD, entered by inspector)" in v.source
    # The typed-in MSRP wins over the sample table.
    table = provider.get_value(make="Toyota", model="Camry", year=2026).value
    assert provider.get_value(make="Toyota", model="Camry", year=2026, new_price=40000).value == 40000 != table
    # A brand-new car is worth its price; no year means no estimate.
    assert provider.get_value(make="X", model="Y", year=0, new_price=30000) is None
    # The app's own provider never invents a price from the sample table.
    assert MockPricingProvider(today=date(2026, 6, 1)).get_value(make="Toyota", model="Camry", year=2020) is None


def test_nhtsa_base_price_is_read_when_present():
    with_price = parse_vpic_response({"Results": [{"Make": "MINI", "Model": "Cooper", "ModelYear": "2006",
                                                   "BasePrice": "17450", "ErrorCode": "0"}]})
    assert with_price["base_price"] == 17450.0
    assert parse_vpic_response({"Results": [{"Make": "FORD", "BasePrice": ""}]})["base_price"] is None


def test_importing_a_price_dataset_cleans_and_maps_columns():
    import sys
    sys.path.insert(0, "scripts")
    from import_prices import normalise
    records = [
        {"Brand": "Jeep", "Name": "Wrangler", "MY": "2021", "Version": "Rubicon", "Price": "$38,695", "Drivetrain": "Four-Wheel Drive", "Cyl": "6"},
        {"Brand": "Jeep", "Name": "Wrangler", "MY": "2021", "Version": "Rubicon", "Price": "$38,695", "Drivetrain": "Four-Wheel Drive", "Cyl": "6"},  # duplicate
        {"Brand": "Jeep", "Name": "", "MY": "2021", "Version": "Sport", "Price": "28000"},          # no model
        {"Brand": "Jeep", "Name": "Gladiator", "MY": "21", "Version": "Sport", "Price": "33000"},   # bad year
        {"Brand": "Jeep", "Name": "Compass", "MY": "2021", "Version": "Sport", "Price": "call"},    # no price
    ]
    mapping = {"make": "Brand", "model": "Name", "year": "MY", "trim": "Version", "msrp": "Price",
               "drive": "Drivetrain", "cylinders": "Cyl"}
    rows, dropped = normalise(records, mapping)
    assert rows == [("Jeep", "Wrangler", 2021, "Rubicon", "6", "", "4WD", "", "", 38695)] and dropped == 3


def test_trim_names_narrow_the_price_when_a_dataset_has_them(tmp_path, monkeypatch):
    import csv, gzip
    from app import reference_prices as rp
    with gzip.open(tmp_path / "demo.csv.gz", "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["make", "model", "year", "trim", "cylinders", "hp", "drive", "doors", "style", "msrp"])
        writer.writerows([["Jeep", "Wrangler", 2021, "Sport", "6", "", "4WD", "", "", 28900],
                          ["Jeep", "Wrangler", 2021, "Sahara", "6", "", "4WD", "", "", 39000],
                          ["Jeep", "Wrangler", 2021, "Rubicon 392", "8", "", "4WD", "", "", 74000]])
    monkeypatch.setattr(rp, "STORE", tmp_path)
    rp._table.cache_clear()
    rp.sources.cache_clear()
    try:
        everything = rp.find("Jeep", "Wrangler", 2021)
        assert (everything.low, everything.high, everything.versions) == (28900, 74000, 3)
        rubicon = rp.find("Jeep", "Wrangler", 2021, {"Trim": "Rubicon"})
        assert (rubicon.low, rubicon.high) == (74000, 74000) and "trim (Rubicon)" in rubicon.narrowed_by
        assert rp.find("Jeep", "Wrangler", 2021, {"Trim": "Willys"}).versions == 3     # unknown trim: keep all
        assert rp.counts_by_dataset() == {"demo": 3}
    finally:
        rp._table.cache_clear()
        rp.sources.cache_clear()
