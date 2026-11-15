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
    # NHTSA has a base price for this VIN: it is used automatically.
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: {
        "available": True, "make": "MINI", "model": "Cooper", "year": 2006, "base_price": 17450.0,
        "clean": True, "message": None})
    r = logged_in.post("/api/market-value", json={"vin": VIN, "make": "MINI", "model": "Cooper", "year": "2006"}).json()
    assert r["found"] and "17,450 USD, from NHTSA" in r["value"]["source"]
    # And the saved check records where the value came from.
    saved = logged_in.post("/api/checks", data={"vin": VIN, "make": "Volvo", "model": "S60", "year": "2012",
                                                "declared_price": "500", "use_provider": "1", "new_price": "31300"})
    assert "31,300 USD, entered by inspector" in logged_in.get(saved.json()["url"]).text
