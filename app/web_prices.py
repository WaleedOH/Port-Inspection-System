"""Automatic web lookup: fetch a manufacturer's price the app does not have.

When a car is not in our stored data, the app reads a public car-information
page for that make, model and year, takes the manufacturer's price range from
it, and SAVES it. The next lookup for that model is answered from our own
database, without going online.

Rules this module follows:
  - No API keys and no accounts: only public pages.
  - It identifies itself honestly and obeys each site's robots.txt. If a site
    says no, or blocks automated visitors, that site is skipped.
  - One request per model and year, ever: results (including "not found")
    are stored, so sites are not asked again.
  - Only make, model and year are sent. Never a VIN or a name.
  - A price is accepted only from a page that is clearly about that model
    and year, and only if it is a plausible car price.
"""
import json
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse

import httpx

from . import config, db

USER_AGENT = "ImportValueChecker/1.0 (vehicle reference-price lookup; one request per model)"
TIMEOUT = 7.0
RETRY_NOT_FOUND_AFTER_DAYS = 14

# Public pages that show the manufacturer's price for a model and year.
# {make}, {model} are lower-case with the given separator; {year} is 4 digits.
SITES = [
    ("MotorTrend", "https://www.motortrend.com/cars/{make}/{model}/{year}/", "-"),
    ("Car and Driver", "https://www.caranddriver.com/{make}/{model}-{year}", "-"),
    ("Cars.com", "https://www.cars.com/research/{make}-{model}-{year}/", "_"),
    ("J.D. Power", "https://www.jdpower.com/cars/{year}/{make}/{model}", "-"),
    ("The Car Connection", "https://www.thecarconnection.com/overview/{make}_{model}_{year}", "-"),
]


def model_names(make: str, model: str) -> list[str]:
    """The name to ask for, plus other names the same car goes by on the web.
    The vehicle database says '328i' and 'Silverado'; price pages say
    '3 Series' and 'Silverado 1500'."""
    names = [model]
    key, make_key = _key(model), _key(make)
    if make_key == "bmw" and key[:1].isdigit():
        names.append(f"{key[0]} Series")
    if make_key == "mazda" and re.fullmatch(r"mazda\d", key):
        names += [f"Mazda {key[-1]}", key[-1]]
    if make_key in ("chevrolet", "gmc") and key in ("silverado", "sierra"):
        names.append(f"{model} 1500")
    if make_key == "mercedesbenz" and re.fullmatch(r"[a-z]{1,3}\d{2,3}", key):
        names.append(f"{re.match('[a-z]+', key).group(0).upper()}-Class")
    return names


@dataclass
class WebPrice:
    low: float
    high: float
    site: str
    url: str
    fetched_at: str

    @property
    def typical(self) -> float:
        return round((self.low + self.high) / 2, -1)

    def describe(self) -> str:
        return (f"found online at {self.site} on {self.fetched_at[:10]} and saved to our data; "
                "not yet confirmed by an administrator")


def enabled() -> bool:
    return config.WEB_PRICE_LOOKUP() == "on"


def _slug(text: str, separator: str) -> str:
    return re.sub(r"[^a-z0-9]+", separator, text.lower()).strip(separator)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# ---------- reading a page ----------
_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+|\d{4,7})"
_LABELLED = re.compile(
    r"(?:MSRP|M\.S\.R\.P\.|starting (?:at|price|msrp)|base price|price range|starts at)"
    r"[^$<]{0,60}" + _MONEY + r"(?:[^$<\d]{0,25}" + _MONEY + r")?", re.IGNORECASE)


def _plausible(value) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return number if 5_000 <= number <= 3_000_000 else None


def _from_structured_data(html: str) -> list[float]:
    """Many pages describe the car in a machine-readable block (JSON-LD) with
    its price or price range. That is the most reliable place to read it."""
    prices = []

    def walk(node):
        if isinstance(node, dict):
            kind = str(node.get("@type", "")).lower()
            offers = node.get("offers") if kind in ("car", "vehicle", "product", "motorizedvehicle") else None
            for offer in (offers if isinstance(offers, list) else [offers] if offers else []):
                if isinstance(offer, dict):
                    for field in ("lowPrice", "highPrice", "price"):
                        price = _plausible(offer.get(field))
                        if price:
                            prices.append(price)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE):
        try:
            walk(json.loads(unescape(block.strip())))
        except ValueError:
            continue
    return prices


def extract_prices(html: str) -> list[float]:
    """Manufacturer prices found on a page, lowest first. Structured data
    wins; otherwise amounts written right after words like 'MSRP'."""
    prices = _from_structured_data(html)
    if not prices:
        text = unescape(re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE))
        text = re.sub(r"<[^>]+>", " ", text)
        for match in _LABELLED.finditer(text):
            for group in match.groups():
                price = _plausible(group)
                if price:
                    prices.append(price)
    return sorted(set(prices))


def page_is_about(html: str, model: str, year: int) -> bool:
    """The page title must name this model and this year. A site that sends
    us to its current-year page instead would give the wrong year's price."""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    words = _key(unescape(title.group(1))) if title else ""
    return bool(words) and _key(model) in words and str(year) in words


# ---------- fetching, politely ----------
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _allowed_by_robots(client: httpx.Client, url: str) -> bool:
    host = urlparse(url).netloc
    if host not in _robots:
        parser = urllib.robotparser.RobotFileParser()
        try:
            response = client.get(f"https://{host}/robots.txt")
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            _robots[host] = parser
        except httpx.HTTPError:
            _robots[host] = None       # could not read the rules: do not visit
    parser = _robots[host]
    return parser is not None and parser.can_fetch(USER_AGENT, url)


def try_site(client: httpx.Client, site: str, pattern: str, separator: str,
             make: str, model: str, year: int) -> dict:
    """Visit one site. Returns what happened, for the result and for diagnostics."""
    url = pattern.format(make=_slug(make, separator), model=_slug(model, separator), year=year)
    outcome = {"site": site, "url": url, "prices": []}
    try:
        if not _allowed_by_robots(client, url):
            return {**outcome, "result": "not allowed by the site's robots.txt"}
        response = client.get(url)
        if response.status_code != 200:
            return {**outcome, "result": f"site answered {response.status_code}"}
        if not page_is_about(response.text, model, year):
            return {**outcome, "result": "page is not about this model and year"}
        prices = extract_prices(response.text)
        if not prices:
            return {**outcome, "result": "no manufacturer's price found on the page"}
        return {**outcome, "result": "found", "prices": prices, "url": str(response.url)}
    except httpx.HTTPError as exc:
        return {**outcome, "result": f"could not be reached ({type(exc).__name__})"}


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                        headers={"User-Agent": USER_AGENT, "Accept": "text/html"})


def fetch(make: str, model: str, year: int) -> tuple[WebPrice | None, list[dict]]:
    """Ask the sites one after another. Stop as soon as one gives a price
    RANGE (cheapest to dearest version); a single 'starting at' price is kept
    but the next site is still asked, in case it has the range."""
    tried, prices, sites, url = [], [], [], ""
    with _new_client() as client:
        for site, pattern, separator in SITES:
            for name in model_names(make, model):
                outcome = try_site(client, site, pattern, separator, make, name, year)
                tried.append(outcome)
                if outcome["result"] == "found":
                    prices += outcome["prices"]
                    sites.append(site)
                    url = url or outcome["url"]
                    break
                if outcome["result"].startswith(("not allowed", "site answered 403", "could not be reached")):
                    break       # the site itself said no: another name will not help
            if len(set(prices)) >= 2:
                break
    if not prices:
        return None, tried
    return WebPrice(min(prices), max(prices), " and ".join(sites), url, db.now()), tried


def find(make: str, model: str, year: int) -> tuple[WebPrice | None, str]:
    """Own data first; go online only if this model and year was never asked
    (or the last 'not found' is old). Whatever happens is stored."""
    if not (_key(make) and _key(model) and year):
        return None, ""
    stored = db.get_web_price(_key(make), _key(model), year)
    if stored and stored["found"]:
        return WebPrice(stored["low"], stored["high"], stored["site"], stored["url"], stored["fetched_at"]), ""
    if stored:
        age_days = (time.time() - time.mktime(time.strptime(stored["fetched_at"], "%Y-%m-%d %H:%M:%S"))) / 86400
        if age_days < RETRY_NOT_FOUND_AFTER_DAYS:
            return None, "An online search for this model found no price recently."
    if not enabled():
        return None, ""
    price, tried = fetch(make, model, year)
    db.save_web_price(_key(make), _key(model), year, price)
    if price:
        return price, ""
    reasons = "; ".join(f"{t['site']}: {t['result']}" for t in tried)
    return None, f"The app searched online and found no price ({reasons})."


# ---------- self-test, shown to administrators ----------
_last_selftest: tuple[float, list[dict]] | None = None
SELFTEST_CARS = [("Toyota", "Camry", 2020), ("Ram", "1500", 2014), ("Jeep", "Wrangler", 2022),
                 ("BMW", "328i", 2012), ("Chevrolet", "Silverado", 2019), ("Nissan", "Patrol", 2021)]


def selftest() -> list[dict]:
    """Look up a fixed handful of well-known cars, to show which sources work
    from this server. Nothing is stored. Runs at most once every 10 minutes."""
    global _last_selftest
    if _last_selftest and time.time() - _last_selftest[0] < 600:
        return _last_selftest[1]
    results = []
    for make, model, year in SELFTEST_CARS:
        price, tried = fetch(make, model, year)
        results.append({"car": f"{year} {make} {model}",
                        "price": [price.low, price.high, price.site] if price else None,
                        "tried": [f"{t['site']}: {t['result']}" for t in tried]})
    _last_selftest = (time.time(), results)
    return results
