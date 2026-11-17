"""The supervisor's side: dashboard, audit log, history filters, error pages."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import dashboard, db, vpic
from app.main import app

VIN = "1HGCM82633A004352"


@pytest.fixture(autouse=True)
def fake_vpic(monkeypatch):
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: {
        "available": True, "make": "Honda", "model": "Accord", "year": 2003,
        "clean": True, "message": None})


@pytest.fixture(scope="module")
def admin():
    with TestClient(app) as c:
        c.post("/login", data={"username": "admin", "password": "test-password-123"})
        yield c


def _check(client, price, **extra):
    data = {"vin": VIN, "declared_price": str(price), "market_value": "10000", "market_source": "test", **extra}
    return client.post("/api/checks", data=data).json()


def test_dashboard_figures_are_worked_out_from_the_checks():
    today = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    row = lambda **kw: {"created_at": "2026-11-16 09:00:00", "created_by": "amira", "decoded_make": "HONDA",  # noqa: E731
                        "doc_make": "", "seller": "Blue Quay", "declared_base": 10000, "market_value": 10000,
                        "flag": "GREEN", "identity_review": 0, "decision": "pending", **kw}
    rows = [row(), row(declared_base=6000, flag="RED", decision="referred"),
            row(declared_base=8000, flag="YELLOW"), row(flag="NO_DATA", market_value=None, seller="Other"),
            row(created_at="2026-11-10 09:00:00", identity_review=1, decision="cleared", created_by="tomas")]
    s = dashboard.build(rows, 30, duty_rate=5, today=today)
    assert (s["total"], s["review"], s["awaiting"], s["referred"], s["cleared"]) == (5, 3, 1, 1, 1)
    assert s["gap"] == 6000 and s["duty_at_stake"] == 300 and s["referred_duty"] == 200
    assert dict(s["flags"]) == {"green": 2, "yellow": 1, "red": 1, "no_data": 1} and s["identity"] == 1
    assert len(s["bars"]) == 30 and s["bars"][-2]["total"] == 4 and s["tallest"] == 4
    # The stacked rectangles of the busiest day fill the chart's height and nothing more.
    busiest = s["bars"][-2]["rects"]
    assert abs(sum(r["h"] for r in busiest) - (dashboard.CHART_H - 1.5)) < 0.01 and min(r["y"] for r in busiest) >= 0.99
    assert s["sellers"][0] == {"name": "Blue Quay", "total": 4, "flagged": 3}
    assert s["makes"][0]["name"] == "Honda" and [p["name"] for p in s["people"]] == ["amira", "tomas"]
    weekly = dashboard.build(rows, 90, duty_rate=5, today=today)
    assert weekly["weekly"] and len(weekly["bars"]) == 13 and sum(b["total"] for b in weekly["bars"]) == 5
    assert dashboard.build([], 7, duty_rate=5, today=today)["total"] == 0


def test_dashboard_page_shows_the_figures_and_an_empty_state(admin):
    _check(admin, 5000, seller="Castellan Motors")
    page = admin.get("/dashboard").text
    assert "Estimated duty at stake" in page and "Checks per day" in page and "Castellan Motors" in page
    assert '<rect class="red"' in page and "style=" not in page       # drawn without inline styles
    assert "Checks per week" in admin.get("/dashboard?days=90").text
    assert "Last 30 days" in admin.get("/dashboard?days=12345").text   # unknown period falls back


def test_history_filters_by_kind_of_review_and_by_seller(admin):
    low = _check(admin, 2000, seller="Mirador Auto Group")
    fine = _check(admin, 9900, seller="Northgate Sales", make="Toyota")      # paper disagrees with the VIN
    price = admin.get("/history?review=price").text
    assert f'/checks/{low["id"]}"' in price and f'/checks/{fine["id"]}"' not in price
    identity = admin.get("/history?review=identity").text
    assert f'/checks/{fine["id"]}"' in identity and f'/checks/{low["id"]}"' not in identity
    by_seller = admin.get("/history?q=mirador").text
    assert f'/checks/{low["id"]}"' in by_seller and f'/checks/{fine["id"]}"' not in by_seller
    assert "No checks found" in admin.get("/history?q=nobody-by-this-name").text
    export = admin.get("/history.csv?review=identity").text
    assert "Northgate Sales" in export and "Mirador Auto Group" not in export


def test_result_page_has_the_price_bar_and_a_printable_record(admin):
    saved = _check(admin, 6000)
    page = admin.get(saved["url"]).text
    # 40% below: the marker sits at 60% of the expected value, on a bar that runs to 150%.
    assert 'data-marker="40.0"' in page and 'data-expected="66.67"' in page and 'data-red="46.67"' in page
    assert "Vehicle valuation review record" in page and "Reviewing officer" in page
    done = admin.post(saved["url"] + "/decision", data={"decision": "referred", "notes": "see file"})
    assert "Decision saved." in done.text and "Referred for valuation review" in done.text


def test_audit_log_records_who_did_what(admin):
    saved = _check(admin, 4000)
    admin.get(saved["url"])
    admin.post(saved["url"] + "/decision", data={"decision": "cleared", "notes": ""})
    with TestClient(app) as stranger:
        stranger.post("/login", data={"username": "admin", "password": "not-the-password"})
    log = admin.get("/audit").text
    for action in ("Logged in", "Login failed", "Saved a check", "Viewed a check", "Recorded a decision"):
        assert action in log
    assert f"#{saved['id']} · Cleared, no action" in log
    only = admin.get("/audit?q=decision").text
    assert "Recorded a decision" in only and "Logged in" not in only
    export = admin.get("/audit.csv")
    assert export.text.startswith("at_utc,username,action,detail") and "Saved a check" in export.text
    assert db.list_audit("Exported the audit log")


def test_only_administrators_see_the_audit_log_and_errors_are_readable(admin):
    admin.post("/settings/users", data={"username": "inspector9", "password": "long-password-9"})
    with TestClient(app) as inspector:
        inspector.post("/login", data={"username": "inspector9", "password": "long-password-9"})
        refused = inspector.get("/audit", headers={"accept": "text/html"})
        assert refused.status_code == 403 and "Not allowed" in refused.text and "Back to checking a vehicle" in refused.text
        assert inspector.get("/audit.csv").status_code == 403
        assert "Audit log" not in inspector.get("/settings").text
        assert inspector.get("/dashboard").status_code == 200           # supervisors' figures are open to inspectors
    missing = admin.get("/checks/99999999", headers={"accept": "text/html"})
    assert missing.status_code == 404 and "Page not found" in missing.text
    assert admin.get("/checks/99999999").json() == {"detail": "Check not found."}    # API-style callers keep JSON


def test_organisation_name_and_duty_rate_are_settings(admin):
    form = {"green_threshold": "15", "yellow_threshold": "30", "base_currency": "USD",
            "exchange_rates": "EUR = 1.08", "org_name": "Port of Marlow, Customs", "duty_rate": "7.5"}
    admin.post("/settings", data=form)
    assert db.get_settings()["duty_rate"] == 7.5
    assert "Port of Marlow, Customs" in admin.get("/history").text
    assert "at 7.5% duty" in admin.get("/dashboard").text
    bad = admin.post("/settings", data={**form, "duty_rate": "lots"})
    assert "The duty rate must be a number" in bad.text
    admin.post("/settings", data={**form, "org_name": "", "duty_rate": "5"})
    assert "Changed settings" in admin.get("/audit").text


def test_a_demo_site_fills_itself_once_and_never_touches_a_real_database(monkeypatch):
    from app import demo
    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) AS n FROM checks").fetchone()["n"]
    assert before and demo.seed_if_asked() == 0                 # not asked for
    monkeypatch.setenv("VERCEL", "1")
    assert demo.wanted()                                        # a Vercel site with no database is a demo
    monkeypatch.setenv("DEMO_DATA", "0")
    assert not demo.wanted()
    monkeypatch.setenv("DEMO_DATA", "1")
    assert demo.seed_if_asked() == 0                            # there are checks already
    monkeypatch.setattr(db, "connect", lambda: pytest.fail("a real database must not be read"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    assert demo.seed_if_asked() == 0
