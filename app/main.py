"""The web server: pages and API endpoints.

Run it with:  uvicorn app.main:app --reload
"""
import csv
import hashlib
import io
import json
import os
import re
import secrets
import statistics
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, reference_prices, research, vpic, web_prices
from .compare import FLAG_LABELS, compare_prices, validate_thresholds
from .mismatch import find_mismatches
from .pricing import get_provider
from .readers import Photo, get_reader
from .vin import validate_vin, vin_pattern

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def _asset_version() -> str:
    """A short fingerprint of our own scripts and styles. It is added to their
    web addresses, so a phone never keeps running an old copy after an update."""
    digest = hashlib.sha1()
    for path in sorted((HERE / "static").glob("*.*")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()
DECISIONS = {"pending": "Pending", "cleared": "Cleared, no action",
             "referred": "Referred for valuation review"}
PHYSICAL_CHECKS = {"not_done": "Vehicle not compared yet",
                   "matches": "Vehicle matches the VIN details",
                   "differs": "Vehicle does NOT match the VIN details"}
PUBLIC_PATHS = ("/login", "/static/", "/healthz")


_ready = False


def ensure_ready() -> None:
    """Create the database tables and the first administrator, once."""
    global _ready
    if _ready:
        return
    db.init()
    if db.count_users() == 0:
        username = os.environ.get("ADMIN_USERNAME", "admin")
        password = os.environ.get("ADMIN_PASSWORD")
        if not password and config.is_ephemeral():
            return  # nobody could read a printed password here; the login page explains
        if not password:
            password = secrets.token_urlsafe(9)
            print(f"\n*** First start. Log in with username '{username}' and password "
                  f"'{password}'  (shown only once; set ADMIN_PASSWORD to choose your own) ***\n")
        db.create_user(username, password, is_admin=True)
    _ready = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_ready()
    yield


app = FastAPI(title="Import Value Checker", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def security(request: Request, call_next):
    path = request.url.path
    ensure_ready()  # some hosts skip the start-up step, so make sure here too
    # 1. Block form posts coming from other websites (CSRF protection).
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    # 2. Everything except the login page needs a logged-in user.
    if not path.startswith(PUBLIC_PATHS) and not request.session.get("user"):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Please log in again."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    response = await call_next(request)
    # 3. Headers that tell the browser to be strict.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; "
        "img-src 'self' blob: data:; style-src 'self'; frame-ancestors 'none'")
    if not path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"  # don't keep invoice data in caches
    return response


# Added last so it runs first: makes request.session available above.
app.add_middleware(SessionMiddleware, secret_key=config.secret_key(), same_site="lax",
                   https_only=config.cookie_secure(), max_age=12 * 3600)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def page(request: Request, name: str, **context):
    context.update(user=request.session.get("user"), is_admin=request.session.get("is_admin"),
                   flag_labels=FLAG_LABELS, decisions=DECISIONS, physical_checks=PHYSICAL_CHECKS,
                   v=ASSET_VERSION,
                   ephemeral=config.is_ephemeral(), needs_setup=not _ready)
    return templates.TemplateResponse(request, name, context)


def require_admin(request: Request) -> None:
    if not request.session.get("is_admin"):
        raise HTTPException(403, "Only an administrator can do this.")


# ---------------- login ----------------
_failed_logins: dict[str, list[float]] = {}


def _too_many_attempts(key: str) -> bool:
    recent = [t for t in _failed_logins.get(key, []) if time.time() - t < 300]
    _failed_logins[key] = recent
    return len(recent) >= 5


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/healthz/price-sources")
def price_sources():
    """Which public price pages can this server read? A fixed self-test with a
    handful of well-known cars; it takes no input and runs at most every 10 minutes."""
    return {"enabled": web_prices.enabled(), "cars": web_prices.selftest()}


@app.get("/login")
def login_page(request: Request):
    return page(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form("")):
    username = username.strip()
    key = f"{request.client.host if request.client else ''}:{username.lower()}"
    if _too_many_attempts(key):
        return page(request, "login.html", error="Too many attempts. Wait 5 minutes and try again.")
    user = db.get_user(username)
    if not user or not db.verify_password(password, user["password_hash"]):
        _failed_logins.setdefault(key, []).append(time.time())
        return page(request, "login.html", error="Wrong username or password.")
    request.session.clear()
    request.session.update(user=user["username"], is_admin=bool(user["is_admin"]))
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------- new check ----------------
@app.get("/")
def new_check(request: Request):
    reader = get_reader()
    settings = db.get_settings()
    client_config = {
        "needsBrowserOcr": reader.needs_browser_ocr,
        "rates": settings["exchange_rates"],
        "baseCurrency": settings["base_currency"],
        "greenThreshold": settings["green_threshold"],
        "yellowThreshold": settings["yellow_threshold"],
    }
    return page(request, "new_check.html", reader=reader, settings=settings, recent=db.list_checks(limit=5),
                provider=get_provider(), client_config=json.dumps(client_config).replace("<", "\\u003c"))


async def _read_photo(upload: UploadFile | None) -> Photo | None:
    """Accept only real JPEG/PNG/WebP images under the size limit."""
    if upload is None or not upload.filename:
        return None
    data = await upload.read(config.MAX_PHOTO_BYTES + 1)
    if len(data) > config.MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is too large (limit 8 MB).")
    if data[:3] == b"\xff\xd8\xff":
        return Photo(data, "image/jpeg")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return Photo(data, "image/png")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return Photo(data, "image/webp")
    raise HTTPException(415, "Only JPEG, PNG or WebP photos are accepted.")


def _save_photo(photo: Photo | None) -> str | None:
    if photo is None:
        return None
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[photo.media_type]
    name = f"{secrets.token_hex(16)}.{extension}"  # random name, never the uploaded one
    db.save_photo(name, photo.media_type, photo.data)
    return name


@app.post("/api/read-document")
async def read_document(document: UploadFile | None = File(None),
                        vin_plate: UploadFile | None = File(None),
                        document_text: str = Form(""), vin_plate_text: str = Form("")):
    try:
        fields = get_reader().read(
            document=await _read_photo(document), vin_plate=await _read_photo(vin_plate),
            document_text=document_text[:50000], vin_plate_text=vin_plate_text[:5000])
    except HTTPException:
        raise
    except Exception as exc:  # a reader problem must never block the inspector
        return {"fields": {}, "error": f"The photo could not be read automatically "
                                       f"({type(exc).__name__}). Please type the details in."}
    return {"fields": fields.to_dict(), "error": None}


def _vin_report(vin_raw: str, make: str = "", model: str = "", year: int | None = None,
                trim: str = "") -> dict:
    result = validate_vin(vin_raw)
    decoded, mismatches = None, []
    if result.is_standard:
        decoded = vpic.decode_vin(result.vin)
        mismatches = find_mismatches({"make": make, "model": model, "year": year, "trim": trim}, decoded)
    return {"validation": result.to_dict(), "decoded": decoded, "mismatches": mismatches}


@app.post("/api/vin-check")
async def vin_check(request: Request):
    body = await request.json()
    year = body.get("year")
    report = _vin_report(str(body.get("vin", "")), body.get("make") or "", body.get("model") or "",
                         int(year) if str(year or "").isdigit() else None, body.get("trim") or "")
    previous = db.previous_checks_for_vin(report["validation"]["vin"]) if report["validation"]["vin"] else []
    report["previous"] = [dict(row) for row in previous]
    return report


def _lookup_value(vin: str, make: str, model: str, year: int, mileage: int | None,
                  new_price: float | None, trim: str = "", user: str = "system"):
    """Find a value for the vehicle. Returns (value or None, note for the inspector).

    The manufacturer's price when new is looked for in this order:
      1. typed in by the inspector for this check;
      2. OUR OWN stored data: the price book, the vehicle database, the
         built-in price list (exact model year), and prices already found online;
      2b. the automatic WEB LOOKUP of public car-price pages, saved for next time;
          failing that, the built-in list's nearest model year;
      3. ONLINE search (if switched on). What it finds is saved in the price
         book with its web sources, so step 2 answers next time.
    """
    source, note = "entered by inspector", ""
    pattern = vin_pattern(vin)
    decoded = vpic.decode_vin(validate_vin(vin).vin) if pattern else {}
    if not new_price:
        entry = db.find_price(pattern, make, model, year, trim)
        if entry:
            new_price = entry["msrp"]
            source = (f"from our price book, matched by {entry['matched_by']}; "
                      f"added by {entry['added_by']} on {entry['added_at'][:10]}")
            if not entry["verified"]:
                source += "; found online, not yet confirmed"
    if not new_price and decoded.get("base_price"):
        new_price, source = decoded["base_price"], "from our vehicle database"
    price_range = None
    listed = None if new_price else reference_prices.find(make, model, year, decoded.get("specs"))
    if listed and not listed.adjusted:
        # Our built-in list has this exact model year.
        new_price, price_range, source = listed.typical, (listed.low, listed.high), listed.describe(year)
    if not new_price:
        # Not in our stored data for this year: look it up on the web, and keep it.
        online, note = web_prices.find(make, model, year)
        if online:
            new_price, price_range = online.typical, (online.low, online.high)
            source = online.describe()
    if not new_price and listed:
        # Nothing online either: fall back to the nearest model year we do have.
        new_price, price_range, source = listed.typical, (listed.low, listed.high), listed.describe(year)
        note = ""
    if not new_price and research.enabled() and make and model and year:
        details = ", ".join(f"{k}: {v}" for k, v in (decoded.get("specs") or {}).items())
        found, note = research.find_msrp(year=year, make=make, model=model,
                                         trim=trim or decoded.get("trim") or "", details=details)
        if found:
            new_price = found.msrp
            where = ", ".join(urlparse(u).netloc for u in found.urls)
            source = f"found online just now ({where}); saved to our price book, not yet confirmed"
            if not found.trim_matched:
                source += "; price is for the base model, not this exact trim"
            db.add_price(make=make, model=model, trim=trim, year=year, msrp=found.msrp,
                         vin_pattern=pattern, added_by=user, verified=False,
                         source=f"Found online: {' '.join(found.urls)}. {found.note}")
    value = get_provider().get_value(vin=vin, make=make, model=model, year=year, mileage=mileage,
                                     new_price=new_price, new_price_source=source,
                                     new_price_range=price_range)
    return value, note


def _learn_price(vin: str, make: str, model: str, trim: str, year: int, typed: float | None,
                 user: str, where: str) -> None:
    """Remember a typed-in manufacturer's price, so the next car with the same
    VIN pattern gets it automatically. This is how the price book fills itself."""
    pattern = vin_pattern(vin)
    if not (typed and typed > 0 and pattern and make.strip() and model.strip() and year):
        return
    known = db.find_price(pattern, "", "", 0)
    if not known or known["msrp"] != typed:
        db.add_price(make=make, model=model, trim=trim, year=year, msrp=typed, vin_pattern=pattern,
                     added_by=user, source=where)


def _versions(decoded: dict, year: int) -> list[dict]:
    """The versions (trims) this model was built in that year, marking the one
    this VIN belongs to and adding the manufacturer's price where we know it.
    This is what shows the money at stake when a trim is misdescribed."""
    names = decoded.get("versions") or []
    mine = {(decoded.get("trim") or "").lower(), (decoded.get("series") or "").lower()} - {""}
    if len(names) < 2 or len(names) > 30 or not decoded.get("make") or not decoded.get("model"):
        return []
    out = []
    for name in names:
        entry = db.find_price(None, decoded["make"], decoded["model"], year, name)
        exact = entry is not None and db._key(entry["trim"]) == db._key(name)
        out.append({"name": name, "current": name.lower() in mine,
                    "msrp": entry["msrp"] if exact else None})
    return out


def _to_int(value) -> int | None:
    text = str(value if value is not None else "").replace(",", "").strip()
    return int(float(text)) if text.replace(".", "", 1).isdigit() else None


@app.post("/api/lookup")
async def lookup(request: Request):
    """The one-step lookup: VIN in, vehicle details and expected price out."""
    body = await request.json()
    user = request.session["user"]
    paper = {k: str(body.get(k) or "").strip() for k in ("make", "model", "trim")}
    paper_year = _to_int(body.get("year"))
    report = _vin_report(str(body.get("vin", "")), paper["make"], paper["model"], paper_year, paper["trim"])
    validation, decoded = report["validation"], report["decoded"] or {}
    report["previous"] = [dict(r) for r in db.previous_checks_for_vin(validation["vin"])] if validation["vin"] else []
    report["value"], report["note"] = None, ""
    report["searches_online"] = research.enabled() or web_prices.enabled()
    if validation["ok"]:
        # What the paper says wins when given; otherwise use what the VIN says.
        make, model = paper["make"] or decoded.get("make") or "", paper["model"] or decoded.get("model") or ""
        trim = paper["trim"] or decoded.get("trim") or decoded.get("series") or ""
        year = paper_year or decoded.get("year") or 0
        try:
            typed = float(str(body["new_price"]).replace(",", "")) if body.get("new_price") not in (None, "") else None
        except ValueError:
            raise HTTPException(422, "The manufacturer's price must be a number.")
        value, report["note"] = _lookup_value(validation["vin"], make, model, year,
                                              _to_int(body.get("mileage")), typed, trim, user)
        if value:
            report["value"] = value.to_dict()
            _learn_price(validation["vin"], make, model, trim, year, typed, user, "Typed in during a lookup")
        report["versions"] = _versions(decoded, year)
    return report


@app.post("/api/market-value")
async def market_value(request: Request):
    body = await request.json()
    try:
        year = int(body.get("year") or 0)
        mileage = int(body["mileage"]) if body.get("mileage") not in (None, "") else None
        new_price = float(str(body["new_price"]).replace(",", "")) if body.get("new_price") not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(422, "Year, mileage and price must be numbers.")
    value, note = _lookup_value(str(body.get("vin", "")), body.get("make") or "", body.get("model") or "",
                                year, mileage, new_price, body.get("trim") or "", request.session["user"])
    return {"found": value is not None, "value": value.to_dict() if value else None, "note": note,
            "searches_online": research.enabled() or web_prices.enabled()}


def _decode_notes(decoded: dict) -> list[str]:
    """Keep the VIN database's message when it could not decode cleanly."""
    unreachable = decoded.get("available") is False
    unclean = decoded.get("available") and not decoded.get("clean")
    return [decoded["message"]] if decoded.get("message") and (unreachable or unclean) else []


def _number(text: str, name: str, required: bool = False) -> float | None:
    text = (text or "").strip().replace(",", "")
    if not text:
        if required:
            raise HTTPException(422, f"{name} is required.")
        return None
    try:
        return float(text)
    except ValueError:
        raise HTTPException(422, f"{name} must be a number.")


@app.post("/api/checks")
async def create_check(
    request: Request,
    vin: str = Form(""), make: str = Form(""), model: str = Form(""), year: str = Form(""),
    seller: str = Form(""), buyer: str = Form(""), mileage: str = Form(""), incoterm: str = Form(""),
    declared_price: str = Form(""), currency: str = Form("USD"), exchange_rate: str = Form(""),
    market_value: str = Form(""), market_source: str = Form(""), use_provider: str = Form(""),
    new_price: str = Form(""), trim: str = Form(""), physical_check: str = Form("not_done"),
    physical_details: str = Form(""),
    notes: str = Form(""),
    document: UploadFile | None = File(None), vin_plate: UploadFile | None = File(None),
):
    settings = db.get_settings()
    base = settings["base_currency"]
    currency = currency.strip().upper()[:3] or base
    price = _number(declared_price, "Declared price", required=True)
    year_n = _number(year, "Year")
    mileage_n = _number(mileage, "Mileage")
    rate = 1.0 if currency == base else (
        _number(exchange_rate, "Exchange rate") or settings["exchange_rates"].get(currency))
    if not rate:
        raise HTTPException(422, f"Enter an exchange rate for {currency}.")

    report = _vin_report(vin, make, model, int(year_n) if year_n else None, trim)
    # The checklist: {"Engine": "matches", "Drive": "differs", ...}
    try:
        ticks = {str(k)[:40]: v for k, v in json.loads(physical_details or "{}").items()
                 if v in ("matches", "differs")}
    except (ValueError, AttributeError):
        ticks = {}
    if ticks:
        physical_check = "differs" if "differs" in ticks.values() else "matches"
    elif physical_check not in PHYSICAL_CHECKS:
        physical_check = "not_done"
    validation = report["validation"]
    if not validation["ok"]:
        raise HTTPException(422, " ".join(validation["errors"]))
    decoded = report["decoded"] or {}

    low = high = None
    market = _number(market_value, "Market value")
    if use_provider == "1":
        # Ask the provider again on the server instead of trusting the browser.
        found, _ = _lookup_value(
            validation["vin"], make.strip() or decoded.get("make") or "", model.strip() or decoded.get("model") or "",
            int(year_n or decoded.get("year") or 0), int(mileage_n) if mileage_n is not None else None,
            _number(new_price, "Manufacturer's price"),
            trim.strip() or decoded.get("trim") or decoded.get("series") or "", request.session["user"])
        if found:
            market, low, high, market_source = found.value, found.low, found.high, found.source
    elif market is not None:
        market_source = f"Entered by inspector: {market_source.strip() or 'no source given'}"

    try:
        result = compare_prices(price, market, rate, settings["green_threshold"],
                                settings["yellow_threshold"], currency, base)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    check_id = db.insert_check({
        "created_at": db.now(), "created_by": request.session["user"],
        "vin": validation["vin"], "vin_standard": int(validation["is_standard"]),
        "vin_check_digit_ok": validation["check_digit_ok"],
        "vin_notes": "\n".join(validation["warnings"] + _decode_notes(decoded)),
        "doc_make": make.strip(), "doc_model": model.strip(), "doc_year": int(year_n) if year_n else None,
        "decoded_make": decoded.get("make"), "decoded_model": decoded.get("model"),
        "decoded_year": decoded.get("year"), "mismatch_notes": "\n".join(report["mismatches"]),
        "doc_trim": trim.strip()[:60], "decoded_trim": decoded.get("trim"),
        "decoded_specs": json.dumps(decoded.get("specs") or {}), "physical_check": physical_check,
        "physical_details": json.dumps(ticks),
        # Identity needs review when paper and VIN disagree, or the inspector saw a difference.
        "identity_review": int(bool(report["mismatches"]) or physical_check == "differs"),
        "seller": seller.strip()[:200], "buyer": buyer.strip()[:200],
        "mileage": int(mileage_n) if mileage_n is not None else None, "incoterm": incoterm.strip()[:10],
        "declared_price": price, "currency": currency, "exchange_rate": rate, "base_currency": base,
        "declared_base": result.declared_base, "market_value": result.market_value,
        "market_low": low, "market_high": high,
        "market_source": market_source.strip()[:400] if result.market_value else None,
        "diff_pct": result.diff_pct, "flag": result.flag, "flag_label": result.label,
        "explanation": "\n".join(result.explanation),
        "green_threshold": settings["green_threshold"], "yellow_threshold": settings["yellow_threshold"],
        "photo_file": _save_photo(await _read_photo(document)),
        "vin_photo_file": _save_photo(await _read_photo(vin_plate)),
        "notes": notes.strip()[:2000],
    })
    _learn_price(validation["vin"], make.strip() or decoded.get("make") or "",
                 model.strip() or decoded.get("model") or "", trim.strip() or decoded.get("trim") or "",
                 int(year_n or decoded.get("year") or 0), _number(new_price, "Manufacturer's price"),
                 request.session["user"], f"Typed in during check #{check_id}")
    return {"id": check_id, "url": f"/checks/{check_id}"}


# ---------------- result, history, export ----------------
def _peer_summary(check) -> dict | None:
    """How this declaration compares with earlier ones for the same model,
    trim and year at this port. Needs at least 3 other cars to mean anything."""
    rows = db.peer_declarations(vin_pattern(check["vin"]), check["id"])
    by_vin = {}
    for row in rows:                      # one declaration per car (the latest)
        by_vin.setdefault(row["vin"], row["declared_base"])
    by_vin.pop(check["vin"], None)
    if len(by_vin) < 3 or not check["declared_base"]:
        return {"count": len(by_vin)} if by_vin else None
    typical = statistics.median(by_vin.values())
    return {"count": len(by_vin), "median": typical, "low": min(by_vin.values()),
            "high": max(by_vin.values()),
            "diff_pct": round((check["declared_base"] - typical) / typical * 100, 1) if typical else None}


@app.get("/checks/{check_id}")
def check_detail(request: Request, check_id: int):
    check = db.get_check(check_id)
    if not check:
        raise HTTPException(404, "Check not found.")
    return page(request, "check.html", c=check, specs=json.loads(check["decoded_specs"] or "{}"),
                peers=_peer_summary(check), ticks=json.loads(check["physical_details"] or "{}"),
                parties=[(label, check[col], db.party_history(col, check[col], check_id))
                         for label, col in (("Seller", "seller"), ("Buyer", "buyer"))],
                previous=db.previous_checks_for_vin(check["vin"], exclude_id=check_id))


@app.post("/checks/{check_id}/decision")
def save_decision(request: Request, check_id: int, decision: str = Form(...), notes: str = Form("")):
    if decision not in DECISIONS or not db.get_check(check_id):
        raise HTTPException(422, "Unknown decision.")
    db.set_decision(check_id, decision, notes.strip()[:2000], request.session["user"])
    return RedirectResponse(f"/checks/{check_id}", status_code=303)


@app.get("/photos/{filename}")
def photo(filename: str):
    # Only names we generated ourselves, and only photos attached to a check.
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp)", filename) or not db.photo_in_use(filename):
        raise HTTPException(404, "Photo not found.")
    stored = db.get_photo(filename)
    if stored:
        return Response(stored[1], media_type=stored[0])
    return FileResponse(config.data_dir() / "photos" / filename)   # photos saved by older versions


@app.get("/history")
def history(request: Request, q: str = "", flag: str = "", awaiting: int = 0):
    q = q.strip()[:17]
    flag = flag if flag in FLAG_LABELS else ""
    return page(request, "history.html", checks=db.list_checks(q, flag, awaiting=bool(awaiting)), q=q,
                flag=flag, awaiting=bool(awaiting), summary=db.summary())


CSV_COLUMNS = ["id", "created_at", "created_by", "vin", "vin_check_digit_ok", "doc_make", "doc_model",
               "doc_trim", "doc_year", "decoded_make", "decoded_model", "decoded_trim", "decoded_year",
               "mismatch_notes", "physical_check", "physical_details", "identity_review", "seller",
               "buyer", "mileage", "incoterm", "declared_price", "currency", "exchange_rate",
               "base_currency", "declared_base", "market_value", "market_source", "diff_pct", "flag",
               "flag_label", "decision", "decision_by", "decision_at", "notes"]


def csv_safe(value):
    """Stop spreadsheet programs from running a cell as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@app.get("/history.csv")
def history_csv(q: str = "", flag: str = ""):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for row in db.list_checks(q.strip()[:17], flag if flag in FLAG_LABELS else "", limit=None):
        writer.writerow([csv_safe(row[col]) for col in CSV_COLUMNS])
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="import-checks-{time.strftime("%Y%m%d")}.csv"'})


# ---------------- our own price book ----------------
def _prices_page(request: Request, q: str = "", message: str | None = None, error: str | None = None):
    return page(request, "prices.html", prices=db.list_prices(q), q=q, total=db.count_prices(),
                web_found=db.list_web_prices(),
                message=message, error=error, base=db.get_settings()["base_currency"])


@app.get("/prices")
def prices(request: Request, q: str = ""):
    return _prices_page(request, q.strip()[:40])


def _price_row(row: dict) -> dict:
    """Validate one price-book entry (from the form or from a CSV line)."""
    get = lambda key: str(row.get(key) or "").strip()  # noqa: E731
    make, model, year = get("make"), get("model"), get("year")
    msrp = get("msrp").replace(",", "").replace("$", "")
    if not make or not model:
        raise ValueError("make and model are required")
    if not year.isdigit() or not 1950 <= int(year) <= 2100:
        raise ValueError("year must be a 4-digit year")
    try:
        price = float(msrp)
    except ValueError:
        raise ValueError("msrp must be a number")
    if not 500 <= price <= 5_000_000:
        raise ValueError("msrp must be between 500 and 5,000,000")
    pattern = None
    if get("vin"):
        pattern = vin_pattern(get("vin"))
        if not pattern:
            raise ValueError("vin must be a full 17-character VIN")
    return {"make": make[:40], "model": model[:60], "trim": get("trim")[:60], "year": int(year),
            "msrp": price, "vin_pattern": pattern, "source": get("source")}


@app.post("/prices")
def add_price(request: Request, make: str = Form(""), model: str = Form(""), trim: str = Form(""),
              year: str = Form(""), msrp: str = Form(""), vin: str = Form(""), source: str = Form("")):
    require_admin(request)
    try:
        entry = _price_row({"make": make, "model": model, "trim": trim, "year": year, "msrp": msrp,
                            "vin": vin, "source": source})
    except ValueError as exc:
        return _prices_page(request, error=f"Not saved: {exc}.")
    db.add_price(**entry, added_by=request.session["user"])
    return _prices_page(request, message="Price added.")


@app.post("/prices/import")
async def import_prices(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    raw = await file.read(1_000_001)
    if len(raw) > 1_000_000:
        return _prices_page(request, error="The file is too large (limit 1 MB).")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _prices_page(request, error="The file must be a CSV saved as UTF-8 text.")
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in reader.fieldnames or []}
    if not {"make", "model", "year", "msrp"} <= headers:
        return _prices_page(request, error="The first line must name the columns: make, model, year, msrp "
                                           "(optional: trim, vin, source).")
    added, problems = 0, []
    for number, row in enumerate(reader, start=2):
        if number > 5001:
            problems.append("stopped after 5,000 lines")
            break
        try:
            entry = _price_row({(k or "").strip().lower(): v for k, v in row.items()})
        except ValueError as exc:
            problems.append(f"line {number}: {exc}")
            continue
        entry["source"] = entry["source"] or f"Imported from {(file.filename or 'CSV')[:60]}"
        db.add_price(**entry, added_by=request.session["user"])
        added += 1
    note = f"{added} price(s) imported."
    if problems:
        note += f" {len(problems)} line(s) skipped: " + "; ".join(problems[:5])
    return _prices_page(request, message=note)


@app.post("/prices/{price_id}/confirm")
def confirm_price(request: Request, price_id: int):
    require_admin(request)
    db.confirm_price(price_id)
    return _prices_page(request, message="Price confirmed.")


@app.post("/prices/{price_id}/delete")
def remove_price(request: Request, price_id: int):
    require_admin(request)
    db.delete_price(price_id)
    return _prices_page(request, message="Price removed.")


# ---------------- settings ----------------
def _settings_page(request: Request, error: str | None = None, saved: bool = False):
    settings = db.get_settings()
    rates_text = "\n".join(f"{code} = {rate:g}" for code, rate in settings["exchange_rates"].items())
    schedule_text = "\n".join(f"{age} = {share:g}" for age, share in enumerate(settings["depreciation"]))
    return page(request, "settings.html", settings=settings, rates_text=rates_text, error=error,
                schedule_text=schedule_text,
                saved=saved, users=db.list_users(), reader=get_reader(), provider=get_provider(),
                decoder=vpic.decoder_name(), research_on=research.enabled(), web_lookup_on=web_prices.enabled(),
                reference_sources=[(reference_prices.sources().get(name, {}), total)
                                   for name, total in sorted(reference_prices.counts_by_dataset().items())],
                storage="PostgreSQL database (permanent)" if db.is_postgres() else
                        ("SQLite file in temporary storage (erased regularly)" if config.is_ephemeral()
                         else "SQLite file in the data folder"))


@app.get("/settings")
def settings_page(request: Request, saved: int = 0):
    return _settings_page(request, saved=bool(saved))


@app.post("/settings")
def save_settings(request: Request, green_threshold: float = Form(...), yellow_threshold: float = Form(...),
                  base_currency: str = Form("USD"), exchange_rates: str = Form(""),
                  depreciation: str = Form(""), adjust_for_mileage: str = Form("")):
    require_admin(request)
    base = base_currency.strip().upper()
    try:
        validate_thresholds(green_threshold, yellow_threshold)
        if not re.fullmatch(r"[A-Z]{3}", base):
            raise ValueError("Base currency must be a 3-letter code such as USD.")
        rates = {}
        for line in exchange_rates.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*([A-Za-z]{3})\s*=\s*([\d.]+)\s*", line)
            if not match or float(match.group(2)) <= 0:
                raise ValueError(f"Could not understand the exchange rate line '{line.strip()}'. Use the form: EUR = 1.08")
            rates[match.group(1).upper()] = float(match.group(2))
        rates[base] = 1.0
        schedule = db.get_settings()["depreciation"]
        if depreciation.strip():
            entries = {}
            for line in depreciation.splitlines():
                if not line.strip():
                    continue
                match = re.fullmatch(r"\s*(\d{1,2})\s*=\s*([\d.]+)\s*%?\s*", line)
                if not match or not 1 <= float(match.group(2)) <= 100:
                    raise ValueError(f"Could not understand the depreciation line '{line.strip()}'. "
                                     "Use the form: 3 = 66  (age in years = % of the new price kept)")
                entries[int(match.group(1))] = float(match.group(2))
            if sorted(entries) != list(range(len(entries))):
                raise ValueError("The depreciation schedule must list every age from 0 upward, with no gaps.")
            schedule = [entries[age] for age in range(len(entries))]
    except ValueError as exc:
        return _settings_page(request, error=str(exc))
    db.save_settings({"green_threshold": green_threshold, "yellow_threshold": yellow_threshold,
                      "base_currency": base, "exchange_rates": rates, "depreciation": schedule,
                      "adjust_for_mileage": adjust_for_mileage == "1" or not depreciation.strip()})
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/users")
def add_user(request: Request, username: str = Form(...), password: str = Form(...),
             is_admin: str = Form("")):
    require_admin(request)
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username):
        return _settings_page(request, error="Username: 3-30 letters, digits, dot, dash or underscore.")
    if len(password) < 10:
        return _settings_page(request, error="Password must be at least 10 characters.")
    if db.get_user(username):
        return _settings_page(request, error="That username already exists.")
    db.create_user(username, password, is_admin == "1")
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/users/{user_id}/delete")
def remove_user(request: Request, user_id: int):
    require_admin(request)
    target = next((u for u in db.list_users() if u["id"] == user_id), None)
    if target and target["username"] == request.session["user"]:
        return _settings_page(request, error="You cannot delete your own account.")
    db.delete_user(user_id)
    return RedirectResponse("/settings?saved=1", status_code=303)
