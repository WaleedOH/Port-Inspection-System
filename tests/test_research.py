"""The online price search, tested with a stand-in for the search service
(no internet and no API key are used here)."""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import research, vpic
from app.main import app

URL = "https://www.volvocars.com/us/pricing-2012-s60"


def reply(text, urls=(URL,), stop_reason="end_turn"):
    results = NS(type="web_search_tool_result", content=[NS(url=u) for u in urls])
    return NS(stop_reason=stop_reason, content=[results, NS(type="text", text=text)])


GOOD = ('The 2012 Volvo S60 T5 started at $31,300.\n'
        '{"msrp": 31300, "trim_matched": true, "source_urls": ["%s"], "note": "Manufacturer price page"}' % URL)


@pytest.fixture
def online(monkeypatch):
    """Switch online search on and let each test script the replies."""
    calls = []
    monkeypatch.setenv("PRICE_RESEARCH", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    research._not_found.clear()

    def use(*replies):
        queue = list(replies)

        def create(**kwargs):
            calls.append(kwargs)
            return queue.pop(0)
        monkeypatch.setattr(research, "_client", lambda: NS(beta=NS(messages=NS(create=create))))
    return NS(use=use, calls=calls)


def test_a_price_backed_by_a_searched_page_is_accepted(online):
    online.use(reply(GOOD))
    found, note = research.find_msrp(year=2012, make="Volvo", model="S60", trim="T5", details="Engine: 2.5L 5-cyl")
    assert found.msrp == 31300 and found.trim_matched and found.urls == [URL]
    sent = online.calls[0]
    assert sent["model"] == "claude-opus-5-5" and sent["tools"][0]["type"] == "web_search_20260209"
    question = sent["messages"][0]["content"]
    assert "2012 Volvo S60 T5" in question and "2.5L 5-cyl" in question


@pytest.mark.parametrize("text, urls", [
    ('{"msrp": 31300, "trim_matched": true, "source_urls": ["https://made-up.example/x"], "note": ""}', (URL,)),  # invented link
    ('{"msrp": 31300, "trim_matched": true, "source_urls": [], "note": ""}', (URL,)),                           # no source
    ('{"msrp": 12, "trim_matched": true, "source_urls": ["%s"], "note": ""}' % URL, (URL,)),                    # absurd price
    ('{"msrp": null, "trim_matched": false, "source_urls": [], "note": "not found"}', (URL,)),                  # honest "not found"
    ("I could not find it.", (URL,)),                                                                           # no answer at all
])
def test_unsupported_answers_are_rejected(online, text, urls):
    online.use(reply(text, urls))
    found, note = research.find_msrp(year=2012, make="Volvo", model="S60")
    assert found is None and "no reliable" in note
    # A failed search is not repeated for the same vehicle within a day.
    assert research.find_msrp(year=2012, make="Volvo", model="S60")[0] is None
    assert len(online.calls) == 1


def test_refusal_and_paused_search(online):
    online.use(reply("", stop_reason="refusal"))
    assert research.find_msrp(year=2012, make="Volvo", model="S60")[0] is None
    research._not_found.clear()
    online.use(reply("still searching", stop_reason="pause_turn"), reply(GOOD))
    found, _ = research.find_msrp(year=2012, make="Volvo", model="S60")
    assert found.msrp == 31300
    assert online.calls[-1]["messages"][-1]["role"] == "assistant"   # the paused turn was resumed


def test_lookup_chain_own_data_first_then_online_then_stored(online, monkeypatch):
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: {
        "available": True, "make": "Saab", "model": "9-3", "year": 2008, "trim": "Aero",
        "specs": {"Engine": "2.8L 6-cyl"}, "clean": True, "message": None})
    saab = {"vin": "YS3FH41U481000001", "make": "Saab", "model": "9-3", "year": "2008", "trim": "Aero"}
    url = "https://www.saabusa.com/2008-9-3-pricing"
    online.use(reply('{"msrp": 41000, "trim_matched": false, "source_urls": ["%s"], "note": "Base model price"}' % url, (url,)))
    with TestClient(app) as c:
        c.post("/login", data={"username": "admin", "password": "test-password-123"})
        first = c.post("/api/market-value", json=saab).json()
        assert first["found"] and first["value"]["new_price"] == 41000
        assert "found online just now (www.saabusa.com)" in first["value"]["source"]
        assert "not this exact trim" in first["value"]["source"]
        # Second lookup: answered from our own price book, no online call.
        second = c.post("/api/market-value", json={**saab, "vin": "YS3FH41U481999999"}).json()
        assert "from our price book" in second["value"]["source"] and "not yet confirmed" in second["value"]["source"]
        assert len(online.calls) == 1
        page = c.get("/prices?q=saab").text
        assert "Found online, not yet confirmed" in page and url in page
        # An administrator confirms it; the warning disappears.
        price_id = page.split("/prices/")[1].split("/confirm")[0]
        assert "Price confirmed" in c.post(f"/prices/{price_id}/confirm").text
        third = c.post("/api/market-value", json=saab).json()
        assert "not yet confirmed" not in third["value"]["source"]


def test_online_search_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PRICE_RESEARCH", raising=False)
    monkeypatch.setattr(research, "_client", lambda: (_ for _ in ()).throw(AssertionError("must not be called")))
    assert research.enabled() is False
    with TestClient(app) as c:
        c.post("/login", data={"username": "admin", "password": "test-password-123"})
        r = c.post("/api/market-value", json={"vin": "ZFF76ZFA5E0200001", "make": "Ferrari", "model": "458", "year": "2014"}).json()
        assert r["found"] is False and r["searches_online"] is False
