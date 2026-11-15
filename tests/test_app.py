"""End-to-end tests of the web app (the NHTSA service is replaced by a stand-in)."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app import vpic
from app.main import app

VIN = "1HGCM82633A004352"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


@pytest.fixture(autouse=True)
def fake_vpic(monkeypatch):
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: {
        "available": True, "make": "Honda", "model": "Accord", "year": 2003,
        "clean": True, "message": None})


@pytest.fixture
def own_database(monkeypatch):
    """Use the app's real built-in vehicle database instead of the stand-in."""
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: vpic.decode_offline(vin))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def logged_in(client):
    response = client.post("/login", data={"username": "admin", "password": "test-password-123"},
                           follow_redirects=False)
    assert response.status_code == 303
    return client


def test_pages_require_login():
    with TestClient(app) as anonymous:
        assert anonymous.get("/history", follow_redirects=False).headers["location"] == "/login"
        assert anonymous.post("/api/vin-check", json={"vin": VIN}).status_code == 401
        assert anonymous.get("/history.csv", follow_redirects=False).status_code == 303
        wrong = anonymous.post("/login", data={"username": "admin", "password": "nope"})
        assert "Wrong username or password" in wrong.text


def test_cross_site_post_is_blocked(logged_in):
    response = logged_in.post("/api/vin-check", json={"vin": VIN}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_vin_check_reports_mismatch(logged_in):
    r = logged_in.post("/api/vin-check", json={"vin": VIN, "make": "Toyota", "model": "Camry", "year": "2003"}).json()
    assert r["validation"]["check_digit_ok"] is True
    assert r["decoded"]["make"] == "Honda"
    assert len(r["mismatches"]) == 2


def test_read_document_from_ocr_text(logged_in):
    r = logged_in.post("/api/read-document", data={
        "document_text": "Seller: ACME Motors\nVIN: 1HGCM82633A004352\nTotal: USD 4,000.00"}).json()
    assert r["fields"]["vin"] == VIN and r["fields"]["declared_price"] == 4000.0


def test_full_check_is_saved_searchable_and_exported(logged_in):
    r = logged_in.post("/api/checks", data={
        "vin": VIN, "make": "Honda", "model": "Accord", "year": "2003",
        "declared_price": "1000", "currency": "USD", "market_value": "4000",
        "market_source": "price guide", "seller": "=cmd()", "buyer": "Test Buyer"},
        files={"document": ("doc.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200, r.text
    page = logged_in.get(r.json()["url"])
    assert "Needs review (priority)" in page.text and "-75.0%" in page.text
    assert "not proof of fraud" in page.text
    assert "Why this flag?" in page.text

    assert VIN in logged_in.get("/history?q=82633").text
    assert VIN not in logged_in.get("/history?q=ZZZZ").text
    assert VIN not in logged_in.get("/history?flag=GREEN").text

    rows = list(csv.DictReader(io.StringIO(logged_in.get("/history.csv").text)))
    row = next(x for x in rows if x["vin"] == VIN)
    assert row["flag"] == "RED" and row["diff_pct"] == "-75.0" and row["created_by"] == "admin"
    assert row["seller"] == "'=cmd()"  # formula neutralised for spreadsheets

    # The photo is stored under a random name and only served to logged-in users.
    import re
    name = re.search(r"/photos/([0-9a-f]{32}\.jpg)", page.text).group(1)
    assert logged_in.get(f"/photos/{name}").content == JPEG
    with TestClient(app) as anonymous:
        assert anonymous.get(f"/photos/{name}", follow_redirects=False).status_code == 303
    assert logged_in.get("/photos/..%2Fapp.db").status_code == 404


def test_mock_provider_value_is_recomputed_on_server(logged_in):
    r = logged_in.post("/api/checks", data={
        "vin": VIN, "make": "Honda", "model": "Accord", "year": "2003", "declared_price": "2500",
        "currency": "USD", "market_value": "1", "use_provider": "1"})
    page = logged_in.get(r.json()["url"]).text
    assert "Mock sample data" in page          # browser-sent value "1" was ignored
    assert "typical range" in page


def test_foreign_currency_and_no_market_value(logged_in):
    r = logged_in.post("/api/checks", data={
        "vin": VIN, "declared_price": "1000", "currency": "EUR", "exchange_rate": "1.1"})
    page = logged_in.get(r.json()["url"]).text
    assert "No market value" in page and "1,100.00 USD" in page
    assert "checked before" in page  # duplicate VIN notice


def test_invalid_input_is_rejected(logged_in):
    bad_vin = logged_in.post("/api/checks", data={"vin": "1HGCM82633A00435O", "declared_price": "100"})
    assert bad_vin.status_code == 422 and "I, O or Q" in bad_vin.json()["detail"]
    assert logged_in.post("/api/checks", data={"vin": VIN, "declared_price": "abc"}).status_code == 422
    not_image = logged_in.post("/api/checks", data={"vin": VIN, "declared_price": "100"},
                               files={"document": ("x.jpg", b"<script>hi</script>", "image/jpeg")})
    assert not_image.status_code == 415


def test_decision_is_recorded(logged_in):
    check = logged_in.post("/api/checks", data={"vin": VIN, "declared_price": "100"}).json()
    logged_in.post(f"/checks/{check['id']}/decision", data={"decision": "referred", "notes": "see file"})
    page = logged_in.get(check["url"]).text
    assert "Last updated by admin" in page and "see file" in page


def test_settings_change_thresholds(logged_in):
    bad = logged_in.post("/settings", data={"green_threshold": "40", "yellow_threshold": "30",
                                            "base_currency": "USD", "exchange_rates": "EUR = 1.08"})
    assert "Thresholds must satisfy" in bad.text
    logged_in.post("/settings", data={"green_threshold": "50", "yellow_threshold": "80",
                                      "base_currency": "USD", "exchange_rates": "EUR = 1.08"})
    r = logged_in.post("/api/checks", data={"vin": VIN, "declared_price": "2400", "market_value": "4000"})
    assert "Within expected range" in logged_in.get(r.json()["url"]).text  # 40% below, now green
    logged_in.post("/settings", data={"green_threshold": "15", "yellow_threshold": "30",
                                      "base_currency": "USD", "exchange_rates": "EUR = 1.08"})


def test_non_admin_cannot_change_settings(logged_in):
    logged_in.post("/settings/users", data={"username": "inspector1", "password": "long-password-1"})
    with TestClient(app) as other:
        other.post("/login", data={"username": "inspector1", "password": "long-password-1"})
        assert other.get("/history").status_code == 200
        denied = other.post("/settings", data={"green_threshold": "1", "yellow_threshold": "2"})
        assert denied.status_code == 403


def test_value_estimated_from_manufacturer_price(logged_in, monkeypatch):
    # Not in the sample table, no price typed: nothing found.
    body = {"vin": VIN, "make": "Volvo", "model": "S60", "year": "2012"}
    assert logged_in.post("/api/market-value", json=body).json()["found"] is False
    # Inspector types the MSRP: an estimate comes back.
    r = logged_in.post("/api/market-value", json={**body, "new_price": "31,300"}).json()
    assert r["found"] and "entered by inspector" in r["value"]["source"]
    # Our vehicle database has a base price for this VIN: it is used automatically.
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: {
        "available": True, "make": "MINI", "model": "Cooper", "year": 2006, "base_price": 17450.0,
        "clean": True, "message": None})
    r = logged_in.post("/api/market-value", json={"vin": VIN, "make": "MINI", "model": "Cooper", "year": "2006"}).json()
    assert r["found"] and "17,450 USD, from our vehicle database" in r["value"]["source"]
    # And the saved check records where the value came from.
    saved = logged_in.post("/api/checks", data={"vin": VIN, "make": "Volvo", "model": "S60", "year": "2012",
                                                "declared_price": "500", "use_provider": "1", "new_price": "31300"})
    assert "31,300 USD, entered by inspector" in logged_in.get(saved.json()["url"]).text


def test_identity_review_for_wrong_trim_and_physical_check(logged_in, monkeypatch):
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: {
        "available": True, "make": "GMC", "model": "Terrain", "year": 2013, "trim": "Denali",
        "series": None, "clean": True, "message": None,
        "specs": {"Trim": "Denali", "Engine": "3.6L 6-cyl", "Drive": "AWD/All-Wheel Drive"}})
    base = {"vin": "2GKFLZE39D6253778", "make": "GMC", "model": "Terrain", "year": "2013",
            "declared_price": "9000", "market_value": "9000"}
    report = logged_in.post("/api/vin-check", json={**base, "trim": "SLE"}).json()
    assert report["decoded"]["specs"]["Engine"] == "3.6L 6-cyl" and len(report["mismatches"]) == 1

    # Price is fine (GREEN), but the paper trim is wrong: identity still needs review.
    page = logged_in.get(logged_in.post("/api/checks", data={**base, "trim": "SLE"}).json()["url"]).text
    assert "Within expected range" in page and "Needs review: vehicle identity" in page
    assert "Built as: engine" in page and "3.6L 6-cyl" in page

    # Paper agrees with the VIN, but the inspector sees a different car.
    page = logged_in.get(logged_in.post("/api/checks", data={
        **base, "trim": "Denali", "physical_check": "differs"}).json()["url"]).text
    assert "Needs review: vehicle identity" in page and "does not match the details in its VIN" in page

    # Everything agrees: no identity flag.
    page = logged_in.get(logged_in.post("/api/checks", data={
        **base, "trim": "Denali", "physical_check": "matches"}).json()["url"]).text
    assert "Needs review: vehicle identity" not in page and "Vehicle matches the VIN details" in page

    rows = list(csv.DictReader(io.StringIO(logged_in.get("/history.csv?q=2GKFLZE39D6253778").text)))
    assert sorted(r["identity_review"] for r in rows) == ["0", "1", "1"]
    assert "Identity needs review" in logged_in.get("/history?q=2GKFLZE39D6253778").text


def test_old_database_gains_the_new_columns(tmp_path, monkeypatch):
    import sqlite3
    from app import config, db
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)     # this test is about the SQLite file
    with sqlite3.connect(tmp_path / "app.db") as conn:   # a database from the first version
        conn.execute("CREATE TABLE checks (id INTEGER PRIMARY KEY, vin TEXT NOT NULL)")
        conn.execute("INSERT INTO checks (vin) VALUES ('1HGCM82633A004352')")
    db.init()
    row = db.get_check(1)
    assert row["physical_check"] == "not_done" and row["identity_review"] == 0 and row["doc_trim"] is None


# ---------- the port's own price book ----------
def test_vin_pattern():
    from app.vin import vin_pattern
    # Two real 2012 Ford Taurus Limited cars share a pattern; serial numbers differ.
    assert vin_pattern("1FAHP2FW5CG103485") == "1FAHP2FWC"
    assert vin_pattern("1FAHP2FW5CG103485") == vin_pattern("1FAHP2FW0CG999999")
    assert vin_pattern("1FAHP2FW5DG103485") != vin_pattern("1FAHP2FW5CG103485")   # other year
    assert vin_pattern("JZX100-0012345") is None


def test_price_book_learns_from_inspector_and_is_reused(logged_in):
    first, second = "1FAHP2FW5CG103485", "1FAHP2FW9CG555555"     # same model/trim/year
    car = {"make": "Ford", "model": "Taurus", "trim": "Limited", "year": "2012"}
    # Nothing known yet for this car.
    assert logged_in.post("/api/market-value", json={"vin": second, **car}).json()["found"] is False
    # Inspector types the manufacturer's price while checking the first car.
    logged_in.post("/api/checks", data={"vin": first, **car, "declared_price": "9000",
                                        "use_provider": "1", "new_price": "32,950"})
    # The second car of the same pattern now gets it automatically.
    r = logged_in.post("/api/market-value", json={"vin": second, **car}).json()
    assert r["found"] and r["value"]["new_price"] == 32950
    assert "from our price book, matched by VIN pattern; added by admin" in r["value"]["source"]
    page = logged_in.get("/prices?q=taurus").text
    assert "2012 Ford Taurus Limited" in page and "32,950" in page and "Typed in during check" in page
    # Saving the second car does not create a duplicate entry.
    logged_in.post("/api/checks", data={"vin": second, **car, "declared_price": "9100", "use_provider": "1"})
    assert logged_in.get("/prices?q=taurus").text.count("2012 Ford Taurus Limited") == 1


def test_price_book_add_import_match_and_delete(logged_in):
    bad = logged_in.post("/prices", data={"make": "Volvo", "model": "S60", "year": "12", "msrp": "31300"})
    assert "Not saved: year must be a 4-digit year" in bad.text
    logged_in.post("/prices", data={"make": "Volvo", "model": "S60", "trim": "T5", "year": "2012",
                                    "msrp": "31300", "source": "price list"})
    # Matched by make, model and year even though this VIN was never seen.
    r = logged_in.post("/api/market-value", json={"vin": "YV1622FS9C2117877", "make": "VOLVO",
                                                  "model": "S-60", "year": "2012"}).json()
    assert r["found"] and "matched by make, model and year" in r["value"]["source"]

    csv_file = ("make,model,trim,year,msrp,source\n"
                "Kia,Cadenza,Premium,2015,\"$35,100\",Kia price list\n"
                "Kia,Cadenza,Limited,2015,43800,\n"
                "Kia,,Limited,2015,43800,\n"            # missing model
                "Kia,Rio,LX,2015,abc,\n").encode()       # bad price
    done = logged_in.post("/prices/import", files={"file": ("list.csv", csv_file, "text/csv")}).text
    assert "2 price(s) imported. 2 line(s) skipped" in done and "line 4: make and model are required" in done
    # The same trim is preferred over another trim of the same model.
    pick = lambda trim: logged_in.post("/api/market-value", json={  # noqa: E731
        "vin": "KNALN4D73F5170942", "make": "Kia", "model": "Cadenza", "year": "2015", "trim": trim}).json()
    assert pick("Premium")["value"]["new_price"] == 35100
    assert pick("Limited")["value"]["new_price"] == 43800
    wrong_file = logged_in.post("/prices/import", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}).text
    assert "The first line must name the columns" in wrong_file

    with TestClient(app) as other:      # inspectors can read the book but not change it
        other.post("/login", data={"username": "inspector1", "password": "long-password-1"})
        assert "Kia Cadenza" in other.get("/prices?q=cadenza").text
        assert other.post("/prices", data={"make": "A", "model": "B", "year": "2020", "msrp": "9000"}).status_code == 403


def test_comparison_with_earlier_declarations(logged_in):
    car = {"make": "Subaru", "model": "Outback", "year": "2014"}
    serials = ["279476", "111111", "222222", "333333"]
    for serial, price in zip(serials, ["15000", "16000", "17000", "8000"]):
        last = logged_in.post("/api/checks", data={"vin": f"4S4BRBCC3E3{serial}", **car, "declared_price": price})
    page = logged_in.get(last.json()["url"]).text          # the 8,000 one, against 15/16/17 thousand
    assert "3 other cars of this same model, trim and year" in page
    assert "typical: 16,000" in page and "-50.0%" in page
    first = logged_in.get("/history?q=4S4BRBCC3E3279476").text
    assert "4S4BRBCC3E3279476" in first


# ---------- the one-step lookup ----------
def test_one_step_lookup_returns_vehicle_and_expected_price(logged_in, monkeypatch):
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: {
        "available": True, "make": "Lincoln", "model": "Zephyr", "year": 2006, "trim": "Zephyr", "series": None,
        "base_price": None, "clean": True, "message": None, "source": "own database",
        "specs": {"Engine": "3.0L 6-cyl", "Drive": "FWD/Front-Wheel Drive"}})
    vin = "3LNHM261X6R633550"
    # Only the VIN is sent. The vehicle comes back; no price is stored yet.
    r = logged_in.post("/api/lookup", json={"vin": vin}).json()
    assert r["validation"]["ok"] and r["decoded"]["model"] == "Zephyr" and r["decoded"]["specs"]["Engine"]
    assert r["value"] is None and r["mismatches"] == []
    # Give the manufacturer's price once...
    r = logged_in.post("/api/lookup", json={"vin": vin, "new_price": "29,660"}).json()
    assert r["value"]["new_price"] == 29660 and 0 < r["value"]["value"] < 29660
    # ...and another car of the same model gets the expected price from the VIN alone.
    r = logged_in.post("/api/lookup", json={"vin": "3LNHM26156R000001"}).json()
    assert r["value"]["new_price"] == 29660 and "from our price book" in r["value"]["source"]
    # Saving needs only the VIN and the declared price.
    saved = logged_in.post("/api/checks", data={"vin": vin, "declared_price": "900", "use_provider": "1"})
    page = logged_in.get(saved.json()["url"]).text
    assert "Needs review" in page and "29,660 USD" in page and "2006 Lincoln Zephyr" in page


def test_one_step_lookup_with_a_bad_vin(logged_in):
    r = logged_in.post("/api/lookup", json={"vin": "1HGCM82633A00435O"}).json()
    assert not r["validation"]["ok"] and r["value"] is None and r["decoded"] is None
    assert logged_in.post("/api/lookup", json={"vin": ""}).json()["validation"]["errors"] == ["VIN is empty."]


# ---------- going further: versions, checklist, repeat parties, depreciation ----------
def test_versions_of_the_model_with_known_prices(logged_in, own_database):
    vin = "1FAHP2FW5CG103485"                       # real 2012 Ford Taurus Limited
    r = logged_in.post("/api/lookup", json={"vin": vin}).json()
    names = {v["name"]: v for v in r["versions"]}
    assert {"Limited", "SE", "SEL", "SHO"} <= set(names)
    assert names["Limited"]["current"] and not names["SE"]["current"]
    # Prices added per trim show the money at stake between versions.
    for trim, msrp in (("SE", "26350"), ("SHO", "39200")):
        logged_in.post("/prices", data={"make": "Ford", "model": "Taurus", "trim": trim, "year": "2012", "msrp": msrp})
    names = {v["name"]: v for v in logged_in.post("/api/lookup", json={"vin": vin}).json()["versions"]}
    assert names["SE"]["msrp"] == 26350 and names["SHO"]["msrp"] == 39200


def test_inspection_checklist_is_saved_and_raises_identity_review(logged_in, own_database):
    base = {"vin": "1FAHP2FW5CG103485", "declared_price": "9000", "market_value": "9000"}
    ok = logged_in.post("/api/checks", data={**base, "physical_details": '{"Engine": "matches", "Drive": "matches"}'})
    page = logged_in.get(ok.json()["url"]).text
    assert "Vehicle matches the VIN details" in page and "Needs review: vehicle identity" not in page
    bad = logged_in.post("/api/checks", data={**base, "physical_details": '{"Engine": "differs", "Drive": "matches", "x": "junk"}'})
    page = logged_in.get(bad.json()["url"]).text
    assert "Needs review: vehicle identity" in page and "Differs:" in page and "VIN says 3.5L 6-cyl" in page
    assert "junk" not in page
    # Nonsense in the checklist field is ignored, not an error.
    assert logged_in.post("/api/checks", data={**base, "physical_details": "not json"}).status_code == 200


def test_repeat_seller_is_pointed_out(logged_in):
    car = {"vin": "JTHBA30G265161304", "market_value": "10000", "seller": "Blue Harbor Motors"}
    logged_in.post("/api/checks", data={**car, "declared_price": "2000"})                     # needs review
    logged_in.post("/api/checks", data={**car, "declared_price": "9900", "seller": " blue harbor MOTORS "})
    third = logged_in.post("/api/checks", data={**car, "declared_price": "9800"})
    page = logged_in.get(third.json()["url"]).text
    assert "appears in 2 earlier checks, 1 of which needed review" in page


def test_history_summary_and_awaiting_filter(logged_in):
    page = logged_in.get("/history").text
    assert "awaiting a decision" in page and "checks in total" in page
    awaiting = logged_in.get("/history?awaiting=1").text
    assert "JTHBA30G265161304" in awaiting          # the 2,000 declaration above, still pending


def test_depreciation_schedule_is_configurable(logged_in, own_database):
    form = {"green_threshold": "15", "yellow_threshold": "30", "base_currency": "USD", "exchange_rates": "EUR = 1.08"}
    lookup = lambda: logged_in.post("/api/lookup", json={  # noqa: E731
        "vin": "WDDPK5HA1CF016122", "new_price": "50000"}).json()["value"]["value"]
    before = lookup()
    bad = logged_in.post("/settings", data={**form, "depreciation": "0 = 100\n2 = 50"})
    assert "must list every age from 0 upward" in bad.text
    # An official flat schedule: every used car keeps exactly half, mileage ignored.
    logged_in.post("/settings", data={**form, "depreciation": "0 = 100\n1 = 50"})
    assert lookup() == 25000 != before
    assert "1 = 50" in logged_in.get("/settings").text
    default = "\n".join(f"{i} = {v}" for i, v in enumerate(
        [100, 82, 74, 66, 60, 54, 48, 44, 39, 35, 32, 29, 26, 23, 21, 19, 17, 15, 14, 12, 11, 10]))
    logged_in.post("/settings", data={**form, "depreciation": default, "adjust_for_mileage": "1"})
    assert lookup() == before
