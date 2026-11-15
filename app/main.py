"""The web server: pages and API endpoints.

Run it with:  uvicorn app.main:app --reload
"""
import csv
import io
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, vpic
from .compare import FLAG_LABELS, compare_prices, validate_thresholds
from .mismatch import find_mismatches
from .pricing import get_provider
from .readers import Photo, get_reader
from .vin import validate_vin

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
DECISIONS = {"pending": "Pending", "cleared": "Cleared, no action",
             "referred": "Referred for valuation review"}
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
                   flag_labels=FLAG_LABELS, decisions=DECISIONS,
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
    }
    return page(request, "new_check.html", reader=reader, settings=settings,
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
    (config.data_dir() / "photos" / name).write_bytes(photo.data)
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


def _vin_report(vin_raw: str, make: str = "", model: str = "", year: int | None = None) -> dict:
    result = validate_vin(vin_raw)
    decoded, mismatches = None, []
    if result.is_standard:
        decoded = vpic.decode_vin(result.vin)
        mismatches = find_mismatches({"make": make, "model": model, "year": year}, decoded)
    return {"validation": result.to_dict(), "decoded": decoded, "mismatches": mismatches}


@app.post("/api/vin-check")
async def vin_check(request: Request):
    body = await request.json()
    year = body.get("year")
    report = _vin_report(str(body.get("vin", "")), body.get("make") or "", body.get("model") or "",
                         int(year) if str(year or "").isdigit() else None)
    previous = db.previous_checks_for_vin(report["validation"]["vin"]) if report["validation"]["vin"] else []
    report["previous"] = [dict(row) for row in previous]
    return report


def _lookup_value(vin: str, make: str, model: str, year: int, mileage: int | None,
                  new_price: float | None):
    """Ask the pricing provider. The manufacturer's price when new comes from
    the inspector if typed in, otherwise from NHTSA when it has one."""
    source = "entered by inspector"
    if not new_price:
        result = validate_vin(vin)
        decoded = vpic.decode_vin(result.vin) if result.is_standard else {}
        new_price, source = decoded.get("base_price"), "from NHTSA"
    return get_provider().get_value(vin=vin, make=make, model=model, year=year, mileage=mileage,
                                    new_price=new_price, new_price_source=source)


@app.post("/api/market-value")
async def market_value(request: Request):
    body = await request.json()
    try:
        year = int(body.get("year") or 0)
        mileage = int(body["mileage"]) if body.get("mileage") not in (None, "") else None
        new_price = float(str(body["new_price"]).replace(",", "")) if body.get("new_price") not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(422, "Year, mileage and price must be numbers.")
    value = _lookup_value(str(body.get("vin", "")), body.get("make") or "", body.get("model") or "",
                          year, mileage, new_price)
    return {"found": value is not None, "value": value.to_dict() if value else None}


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
    new_price: str = Form(""),
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

    report = _vin_report(vin, make, model, int(year_n) if year_n else None)
    validation = report["validation"]
    if not validation["ok"]:
        raise HTTPException(422, " ".join(validation["errors"]))
    decoded = report["decoded"] or {}

    low = high = None
    market = _number(market_value, "Market value")
    if use_provider == "1":
        # Ask the provider again on the server instead of trusting the browser.
        found = _lookup_value(validation["vin"], make, model, int(year_n or 0),
                              int(mileage_n) if mileage_n is not None else None,
                              _number(new_price, "Manufacturer's price"))
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
        "seller": seller.strip()[:200], "buyer": buyer.strip()[:200],
        "mileage": int(mileage_n) if mileage_n is not None else None, "incoterm": incoterm.strip()[:10],
        "declared_price": price, "currency": currency, "exchange_rate": rate, "base_currency": base,
        "declared_base": result.declared_base, "market_value": result.market_value,
        "market_low": low, "market_high": high,
        "market_source": market_source.strip()[:300] if result.market_value else None,
        "diff_pct": result.diff_pct, "flag": result.flag, "flag_label": result.label,
        "explanation": "\n".join(result.explanation),
        "green_threshold": settings["green_threshold"], "yellow_threshold": settings["yellow_threshold"],
        "photo_file": _save_photo(await _read_photo(document)),
        "vin_photo_file": _save_photo(await _read_photo(vin_plate)),
        "notes": notes.strip()[:2000],
    })
    return {"id": check_id, "url": f"/checks/{check_id}"}


# ---------------- result, history, export ----------------
@app.get("/checks/{check_id}")
def check_detail(request: Request, check_id: int):
    check = db.get_check(check_id)
    if not check:
        raise HTTPException(404, "Check not found.")
    return page(request, "check.html", c=check,
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
    return FileResponse(config.data_dir() / "photos" / filename)


@app.get("/history")
def history(request: Request, q: str = "", flag: str = ""):
    q = q.strip()[:17]
    flag = flag if flag in FLAG_LABELS else ""
    return page(request, "history.html", checks=db.list_checks(q, flag), q=q, flag=flag)


CSV_COLUMNS = ["id", "created_at", "created_by", "vin", "vin_check_digit_ok", "doc_make", "doc_model",
               "doc_year", "decoded_make", "decoded_model", "decoded_year", "mismatch_notes", "seller",
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


# ---------------- settings ----------------
def _settings_page(request: Request, error: str | None = None, saved: bool = False):
    settings = db.get_settings()
    rates_text = "\n".join(f"{code} = {rate:g}" for code, rate in settings["exchange_rates"].items())
    return page(request, "settings.html", settings=settings, rates_text=rates_text, error=error,
                saved=saved, users=db.list_users(), reader=get_reader(), provider=get_provider())


@app.get("/settings")
def settings_page(request: Request, saved: int = 0):
    return _settings_page(request, saved=bool(saved))


@app.post("/settings")
def save_settings(request: Request, green_threshold: float = Form(...), yellow_threshold: float = Form(...),
                  base_currency: str = Form("USD"), exchange_rates: str = Form("")):
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
    except ValueError as exc:
        return _settings_page(request, error=str(exc))
    db.save_settings({"green_threshold": green_threshold, "yellow_threshold": yellow_threshold,
                      "base_currency": base, "exchange_rates": rates})
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
