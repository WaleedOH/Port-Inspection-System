"""The automatic web price lookup, tested with made-up pages (no internet)."""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import vpic, web_prices
from app.main import app

JSON_LD_PAGE = """<html><head><title>2021 Jeep Wrangler Review, Pricing and Specs</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Car", "name": "2021 Jeep Wrangler",
 "offers": {"@type": "AggregateOffer", "lowPrice": "28900", "highPrice": 74995, "priceCurrency": "USD"}}</script>
</head><body>Lease from $399 a month. Destination $1,495.</body></html>"""

TEXT_PAGE = """<html><head><title>2021 Jeep Wrangler | Prices &amp; Trims</title></head><body>
<p>Monthly payment $512</p><div>MSRP range: <b>$28,900</b> &ndash; <b>$74,995</b></div>
<script>var x = "MSRP $1";</script></body></html>"""


def test_prices_are_read_from_structured_data_and_from_labelled_text():
    assert web_prices.extract_prices(JSON_LD_PAGE) == [28900, 74995]
    assert web_prices.extract_prices(TEXT_PAGE) == [28900, 74995]      # the $512 payment is ignored
    assert web_prices.extract_prices("<html><body>Call for price. Fees $499.</body></html>") == []
    assert web_prices.extract_prices("<p>Starting at $3</p><p>MSRP $99,999,999</p>") == []   # not car prices


def test_page_must_be_about_the_same_model_and_year():
    assert web_prices.page_is_about(JSON_LD_PAGE, "Wrangler", 2021)
    assert not web_prices.page_is_about(JSON_LD_PAGE, "Wrangler", 2019)     # redirected to another year
    assert not web_prices.page_is_about(JSON_LD_PAGE, "Gladiator", 2021)
    assert not web_prices.page_is_about("<html>no title</html>", "Wrangler", 2021)


@pytest.fixture
def web(monkeypatch):
    """Switch the lookup on with a pretend internet: {url fragment: (status, html)}."""
    visited = []
    monkeypatch.setenv("WEB_PRICE_LOOKUP", "on")
    web_prices._robots.clear()

    def use(pages: dict, robots: str = ""):
        class Client:
            def __enter__(self): return self
            def __exit__(self, *args): return False

            def get(self, url):
                visited.append(url)
                if url.endswith("/robots.txt"):
                    return NS(status_code=200, text=robots, url=url)
                for fragment, (status, html) in pages.items():
                    if fragment in url:
                        return NS(status_code=status, text=html, url=url)
                return NS(status_code=404, text="", url=url)
        monkeypatch.setattr(web_prices, "_new_client", lambda: Client())
    return NS(use=use, visited=visited)


def test_first_working_site_wins_and_the_result_is_stored(web):
    web.use({"caranddriver.com": (403, ""), "motortrend.com": (200, TEXT_PAGE)})
    price, note = web_prices.find("Jeep", "Wrangler", 2021)
    assert (price.low, price.high, price.site) == (28900, 74995, "MotorTrend") and note == ""
    assert not any("cars.com" in url for url in web.visited)       # stopped after the first success
    pages_before = len(web.visited)
    again, _ = web_prices.find("JEEP", "wrangler", 2021)             # answered from our own database
    assert again.low == 28900 and len(web.visited) == pages_before


def test_nothing_found_is_remembered_and_explained(web):
    web.use({"caranddriver.com": (200, JSON_LD_PAGE)})                # a page about a different car
    price, note = web_prices.find("Zastava", "Yugo", 1988)
    assert price is None and "Car and Driver: page is not about this model and year" in note
    assert "site answered 404" in note
    visits = len(web.visited)
    assert web_prices.find("Zastava", "Yugo", 1988)[0] is None and len(web.visited) == visits   # not asked again


def test_a_site_that_forbids_robots_is_not_visited(web):
    web.use({"caranddriver.com": (200, JSON_LD_PAGE.replace("Wrangler", "Gladiator"))},
            robots="User-agent: *\nDisallow: /")
    price, note = web_prices.find("Jeep", "Gladiator", 2021)
    assert price is None and "not allowed by the site's robots.txt" in note
    assert not any("/jeep/gladiator-2021" in url for url in web.visited)


def test_lookup_goes_online_only_when_our_data_has_no_exact_price(web, monkeypatch):
    monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: vpic.decode_offline(vin))
    web.use({"caranddriver.com": (200, JSON_LD_PAGE.replace("Jeep Wrangler", "Ram 1500").replace("2021", "2014")
                                  .replace("28900", "24385").replace("74995", "51675"))})
    with TestClient(app) as c:
        c.post("/login", data={"username": "admin", "password": "test-password-123"})
        # 2015 GMC Terrain is in the built-in list for that exact year: no web visit.
        value = c.post("/api/lookup", json={"vin": "2GKALMEK0F6100001"}).json()["value"]
        assert "built-in price list" in value["source"] and web.visited == []
        # A 2014 Ram 1500 is not in our data at all: looked up, shown, and saved.
        value = c.post("/api/lookup", json={"vin": "1C6RR7MT1ES422095"}).json()["value"]
        assert (value["new_price_low"], value["new_price_high"]) == (24385, 51675)
        assert "found online at Car and Driver" in value["source"] and "saved to our data" in value["source"]
        visits = len(web.visited)
        c.post("/api/lookup", json={"vin": "1C6RR7MT0ES000001"})      # second Ram 1500: no new visit
        assert len(web.visited) == visits
        assert "2014 ram 1500" in c.get("/prices").text and "Found online and saved" in c.get("/prices").text


def test_lookup_is_off_in_tests_by_default():
    assert web_prices.enabled() is False
    assert web_prices.find("Jeep", "Compass", 2019) == (None, "")
