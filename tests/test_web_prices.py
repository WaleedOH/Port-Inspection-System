"""The automatic web price lookup, tested with made-up pages (no internet)."""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app import db, vpic, web_prices
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
    db.init()

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
    web.use({"motortrend.com": (403, ""), "caranddriver.com": (200, TEXT_PAGE)})
    price, note = web_prices.find("Jeep", "Wrangler", 2021)
    assert (price.low, price.high, price.site) == (28900, 74995, "Car and Driver") and note == ""
    assert not any("cars.com" in url for url in web.visited)       # stopped once it had a price range
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
    web.use({"caranddriver.com": (200, JSON_LD_PAGE.replace("Jeep Wrangler", "Ram 1500")
                                  .replace("28900", "24385").replace("74995", "51675"))})
    with TestClient(app) as c:
        c.post("/login", data={"username": "admin", "password": "test-password-123"})
        # 2015 GMC Terrain is in the built-in list for that exact year: no web visit.
        value = c.post("/api/lookup", json={"vin": "2GKALMEK0F6100001"}).json()["value"]
        assert "built-in price list" in value["source"] and web.visited == []
        # The list has no 2021 prices of its own (only nearby years): looked up, shown, and saved.
        value = c.post("/api/lookup", json={"vin": "1C6SRFFT7MN000001"}).json()["value"]
        assert (value["new_price_low"], value["new_price_high"]) == (24385, 51675)
        assert "found online at Car and Driver" in value["source"] and "saved to our data" in value["source"]
        visits = len(web.visited)
        c.post("/api/lookup", json={"vin": "1C6SRFFT9MN000002"})      # second Ram 1500: no new visit
        assert len(web.visited) == visits
        assert "2021 ram 1500" in c.get("/prices").text and "Found online and saved" in c.get("/prices").text


def test_lookup_is_off_in_tests_by_default():
    assert web_prices.enabled() is False
    assert web_prices.find("Jeep", "Compass", 2019) == (None, "")


def test_a_single_starting_price_is_widened_by_the_next_site(web):
    one_price = JSON_LD_PAGE.replace('"lowPrice": "28900", "highPrice": 74995', '"price": 28900').replace("Wrangler", "Compass")
    web.use({"motortrend.com": (200, one_price), "caranddriver.com": (200, TEXT_PAGE.replace("Wrangler", "Compass"))})
    price, _ = web_prices.find("Jeep", "Compass", 2021)      # a model no other test has stored
    assert (price.low, price.high, price.site) == (28900, 74995, "MotorTrend and Car and Driver")


def test_other_names_for_the_same_model_are_tried(web):
    assert web_prices.model_names("BMW", "328i") == ["328i", "3 Series"]
    assert web_prices.model_names("Chevrolet", "Silverado") == ["Silverado", "Silverado 1500"]
    assert web_prices.model_names("Toyota", "Camry") == ["Camry"]
    page = JSON_LD_PAGE.replace("2021 Jeep Wrangler", "2012 BMW 3-Series")
    web.use({"/bmw/3-series/2012": (200, page)})
    price, _ = web_prices.find("BMW", "328i", 2012)
    assert price is not None and any("/bmw/328i/2012" in url for url in web.visited)


# ---------- a price for each trim ----------
def _row(name, price):
    return (f'<tr class="a"><td class="b"><a href="/cars/zastava/yugo/specs/1" rel="nofollow">{name}</a></td>'
            f'<td class="c"><p class="d">{price}</p></td></tr>')


TRIM_TABLE_PAGE = ("<html><head><title>2021 Zastava Yugo Review: Pricing and Trims</title></head><body>"
                   "<p>Lease from $399 a month.</p><table>"
                   + _row("Yugo LX FWD", "$40,735") + _row("Yugo EX FWD", "$45,335") + _row("Yugo EX AWD", "$47,335")
                   + _row("Yugo SX-Prestige AWD", "$55,435") + _row("Yugo X-Line SX-Prestige AWD", "$56,435")
                   + _row("Yugo X-Pro SX-Prestige AWD", "$58,335") + "</table></body></html>")

TRIM_DATA_PAGE = """<html><head><title>2021 Zastava Yugo Review, Pricing, and Specs</title></head><body>
<script id="__NEXT_DATA__" type="application/json">{"props": {"models": [
 {"year": 2021, "chrome_trims": [{"name": "LX", "msrp_low_style": {"id": 1, "base_msrp": 40735}, "estimated_price": false},
                                 {"name": "SX-Prestige", "msrp_low_style": {"id": 2, "base_msrp": 55435}, "estimated_price": false},
                                 {"name": "Concept", "msrp_low_style": {"id": 3, "base_msrp": 70000}, "estimated_price": true}]},
 {"year": 2020, "chrome_trims": [{"name": "LX", "msrp_low_style": {"id": 4, "base_msrp": 37885}, "estimated_price": false}]}
]}}</script></body></html>"""


def test_trim_names_are_matched_the_way_people_write_them():
    from app import trims
    names = ["LX FWD", "EX FWD", "EX AWD", "EX-L AWD", "SX-Prestige AWD", "X-Line SX-Prestige AWD", "X-Pro SX-Prestige AWD"]
    pick = lambda *vin_trims: [names[i] for i in trims.match(names, *vin_trims)]  # noqa: E731
    assert pick("EX") == ["EX FWD", "EX AWD"]                        # 'EX' is not 'EX-L'
    assert pick("EXL") == ["EX-L AWD"] and pick("ex-l") == ["EX-L AWD"]
    assert pick("SXP") == ["SX-Prestige AWD"]                        # 'SXP' = 'SX-Prestige'
    assert pick("SXP, X-LINE SXP") == ["SX-Prestige AWD", "X-Line SX-Prestige AWD"]   # the VIN allows either
    assert pick(None, "X-Pro SX Prestige") == ["X-Pro SX-Prestige AWD"]              # from the series field
    assert pick("Limited") == [] and pick(None) == [] and pick("AWD") == []
    assert trims.match(["Yugo LX FWD", "Yugo EX FWD"], "Yugo LX", ignore="Yugo") == [0]


def test_each_trim_and_its_price_is_read_from_a_page():
    listed = web_prices.extract_trims(TRIM_TABLE_PAGE, 2021)
    assert len(listed) == 6 and listed[0] == ("Yugo LX FWD", 40735) and listed[-1] == ("Yugo X-Pro SX-Prestige AWD", 58335)
    # Or from the page's own data, for this model year only, without estimated prices.
    assert web_prices.extract_trims(TRIM_DATA_PAGE, 2021) == [("LX", 40735), ("SX-Prestige", 55435)]
    assert web_prices.extract_trims(JSON_LD_PAGE, 2021) == []


def test_the_vin_trim_picks_its_own_price_from_the_page(web, monkeypatch):
    def decoded(trim, drive):
        return {"available": True, "make": "Zastava", "model": "Yugo", "year": 2021, "trim": trim,
                "specs": {"Trim": trim, "Drive": drive}, "clean": True, "message": None}
    web.use({"motortrend.com": (200, TRIM_TABLE_PAGE)})
    with TestClient(app) as c:
        c.post("/login", data={"username": "admin", "password": "test-password-123"})
        monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: decoded("SXP, X-LINE SXP", "4WD/4-Wheel Drive/4x4"))
        value = c.post("/api/lookup", json={"vin": "1C6SRFFT7MN000001"}).json()["value"]
        assert (value["new_price_low"], value["new_price_high"]) == (55435, 56435)
        assert "price for this VIN's trim (Yugo SX-Prestige AWD, Yugo X-Line SX-Prestige AWD)" in value["source"]
        visits = len(web.visited)
        # Another trim of the same model: answered from the stored page, no new visit.
        monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: decoded("EX", "FWD/Front-Wheel Drive"))
        value = c.post("/api/lookup", json={"vin": "1C6SRFFT7MN000001"}).json()["value"]
        assert (value["new_price_low"], value["new_price_high"]) == (45335, 45335) and len(web.visited) == visits
        # A trim the page does not list: the whole range, and the app says so.
        monkeypatch.setattr(vpic, "decode_vin", lambda vin, client=None: decoded("Limited", ""))
        value = c.post("/api/lookup", json={"vin": "1C6SRFFT7MN000001"}).json()["value"]
        assert (value["new_price_low"], value["new_price_high"]) == (40735, 58335)
        assert "was not among the listed versions" in value["source"]
